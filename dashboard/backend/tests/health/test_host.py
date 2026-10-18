from pathlib import Path

from app.services.health.collectors import host


def tree(tmp_path: Path) -> Path:
    files = {
        "proc/sys/kernel/random/boot_id": "0b6c1d3e-2f4a-4b5c-8d7e-9f0a1b2c3d4e\n",
        "proc/uptime": "307729.83 1198485.41\n",
        "proc/loadavg": "0.08 0.14 0.14 5/866 42\n",
        "proc/meminfo": "MemTotal:  8000000 kB\nMemAvailable:  6291456 kB\n",
        "sys/class/thermal/thermal_zone0/temp": "66650\n",
        "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq": "1500000\n",
        "sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": "2400000\n",
        "sys/class/hwmon/hwmon0/name": "cpu_thermal\n",
        "sys/class/hwmon/hwmon3/name": "rpi_volt\n",
        "sys/class/hwmon/hwmon3/in0_lcrit_alarm": "0\n",
        "sys/class/net/eth0/statistics/rx_bytes": "1200000000\n",
        "sys/class/net/eth0/statistics/tx_bytes": "900000000\n",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_read_pi5_host(tmp_path):
    root = tree(tmp_path)
    assert host.read(root) == {
        "temp": 66.7,
        "freq": 0.62,
        "uv": 0,
        "load1": 0.08,
        "mem_mb": 6144,
        "nic": (1200000000, 900000000),
    }
    assert host.boot_id(root) == "0b6c1d3e-2f4a-4b5c-8d7e-9f0a1b2c3d4e"
    assert host.uptime(root) == 307729.83


def test_read_tolerates_missing_sources(tmp_path):
    assert host.read(tmp_path) == {}
    assert host.boot_id(tmp_path).startswith("dev-")
