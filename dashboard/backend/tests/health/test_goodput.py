import asyncio

from app.services.health.probes import goodput


def _serve(chunks, delay):
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {goodput.BYTES}\r\n\r\n".encode())
        try:
            for _ in range(chunks):
                writer.write(b"x" * 1000)
                await writer.drain()
                await asyncio.sleep(delay)
        except (ConnectionError, asyncio.CancelledError):
            pass
        writer.close()

    async def go():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await goodput.run(url=f"http://127.0.0.1:{port}/", timeout=0.5)

    return asyncio.run(go())


def test_a_trickle_is_cut_at_the_deadline_and_keeps_what_arrived():
    result = _serve(chunks=1000, delay=0.05)
    assert not result.ok
    assert 0 < result.data["bytes"] < goodput.BYTES and result.data["mbit"] is not None
    assert result.bytes > result.data["bytes"]
