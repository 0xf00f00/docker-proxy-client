import asyncio
import json
import threading
import time
import timeit
from collections.abc import AsyncGenerator

import speedtest


class SpeedTestRunner:
    def __init__(self) -> None:
        self.phase = "init"
        self.download_mbps: float | None = None
        self.upload_mbps: float | None = None
        self.ping_ms: float | None = None
        self.server: str | None = None
        self.done = False
        self.error: str | None = None
        self._cancelled = threading.Event()
        self._dl_bytes = 0
        self._ul_bytes = 0
        self._dl_start: float | None = None
        self._ul_start: float | None = None
        self._byte_lock = threading.Lock()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _add_dl_bytes(self, n: int) -> None:
        with self._byte_lock:
            self._dl_bytes += n

    def _add_ul_bytes(self, n: int) -> None:
        with self._byte_lock:
            self._ul_bytes += n

    def _refresh_live_mbps(self) -> None:
        # Final values from st.results override live estimates; only sample
        # while the phase is still running.
        if self.phase == "download" and self._dl_start is not None:
            elapsed = time.monotonic() - self._dl_start
            if elapsed > 0.3 and self._dl_bytes > 0:
                self.download_mbps = round((self._dl_bytes * 8 / elapsed) / 1_000_000, 1)
        elif self.phase == "upload" and self._ul_start is not None:
            elapsed = time.monotonic() - self._ul_start
            if elapsed > 0.3 and self._ul_bytes > 0:
                self.upload_mbps = round((self._ul_bytes * 8 / elapsed) / 1_000_000, 1)

    def run(self) -> None:
        runner = self
        original_downloader = speedtest.HTTPDownloader
        original_uploader_data = speedtest.HTTPUploaderData

        class TrackingHTTPDownloader(original_downloader):
            def run(self) -> None:
                try:
                    if (timeit.default_timer() - self.starttime) <= self.timeout:
                        f = self._opener(self.request)
                        while (
                            not self._shutdown_event.isSet()
                            and (timeit.default_timer() - self.starttime) <= self.timeout
                        ):
                            chunk = f.read(10240)
                            n = len(chunk)
                            self.result.append(n)
                            if n > 0:
                                runner._add_dl_bytes(n)
                            else:
                                break
                        f.close()
                except (OSError, *speedtest.HTTP_ERRORS):
                    pass

        class TrackingHTTPUploaderData(original_uploader_data):
            def read(self, n: int = 10240):  # type: ignore[override]
                chunk = super().read(n)
                if chunk:
                    runner._add_ul_bytes(len(chunk))
                return chunk

        speedtest.HTTPDownloader = TrackingHTTPDownloader
        speedtest.HTTPUploaderData = TrackingHTTPUploaderData

        try:
            self.phase = "server"
            st = speedtest.Speedtest(secure=True)
            if self.cancelled:
                return self._finish("cancelled")
            st.get_best_server()
            self.server = st.best.get("host", "")
            self.ping_ms = round(st.best.get("latency", 0), 1)

            if self.cancelled:
                return self._finish("cancelled")
            self.phase = "download"
            self._dl_start = time.monotonic()
            st.download()
            self.download_mbps = round(st.results.download / 1_000_000, 1)

            if self.cancelled:
                return self._finish("cancelled")
            self.phase = "upload"
            self._ul_start = time.monotonic()
            st.upload()
            self.upload_mbps = round(st.results.upload / 1_000_000, 1)

            self._finish("done")
        except Exception as e:
            self.error = str(e)
            self._finish("error")
        finally:
            speedtest.HTTPDownloader = original_downloader
            speedtest.HTTPUploaderData = original_uploader_data

    def _finish(self, phase: str) -> None:
        self.phase = phase
        self.done = True

    def snapshot(self) -> dict:
        self._refresh_live_mbps()
        return {
            "phase": self.phase,
            "download_mbps": self.download_mbps,
            "upload_mbps": self.upload_mbps,
            "ping_ms": self.ping_ms,
            "server": self.server,
            "error": self.error,
        }


_active_runner: SpeedTestRunner | None = None
_runner_lock = threading.Lock()


def cancel_speed_test() -> bool:
    with _runner_lock:
        if _active_runner and not _active_runner.done:
            _active_runner.cancel()
            return True
    return False


async def speed_test_stream() -> AsyncGenerator[str]:
    global _active_runner

    with _runner_lock:
        if _active_runner and not _active_runner.done:
            yield f"data: {json.dumps({'error': 'Speed test already running'})}\n\n"
            return
        runner = SpeedTestRunner()
        _active_runner = runner

    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()

    try:
        while not runner.done:
            yield f"data: {json.dumps(runner.snapshot())}\n\n"
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps(runner.snapshot())}\n\n"
    finally:
        with _runner_lock:
            if _active_runner is runner:
                _active_runner = None
