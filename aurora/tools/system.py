import os
import platform
import shutil
import time
from typing import Dict, List

from .registry import tool
from ..host import battery as host_battery


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return "n/a"


def _human(n: int) -> str:
    for unit in ["B", "K", "M", "G", "T"]:
        if n < 1024 or unit == "T":
            if unit == "B":
                return f"{n} B"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} T"


def _battery() -> Dict[str, str]:
    portable = host_battery()
    if portable.get("percent") != "n/a":
        return {k: str(v) for k, v in portable.items()}
    base = "/sys/class/power_supply"
    if not os.path.isdir(base):
        return {"present": "no", "percent": "n/a", "status": "n/a"}
    bats = [d for d in os.listdir(base) if d.startswith("BAT")]
    if not bats:
        return {"present": "no", "percent": "n/a", "status": "n/a"}
    d = os.path.join(base, bats[0])
    pct = _read(os.path.join(d, "capacity"))
    st = _read(os.path.join(d, "status"))
    return {"present": "yes", "percent": pct + "%", "status": st}


@tool(
    "system_info",
    "Get summary of the machine: OS, kernel, hostname, CPU, memory, disk usage, uptime, load. Use when the user asks about the system or before heavy actions.",
    {
        "include_battery": {"type": "boolean", "description": "Also report battery state", "required": False},
    },
    timeout=15,
)
def system_info(include_battery: bool = True) -> str:
    try:
        import psutil

        vm = psutil.virtual_memory()
        mem, free = vm.total, vm.available
        load = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)
        uptime_min = int((time.time() - psutil.boot_time()) // 60)
    except Exception:
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
        free = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 0
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        uptime_min = 0
    lines = [
        f"host: {platform.node()}",
        f"os: {platform.platform()}",
        f"kernel: {platform.release()}",
        f"cpu: {_read('/proc/cpuinfo').splitlines()[0] if os.path.exists('/proc/cpuinfo') else 'n/a'}",
        f"cores: {os.cpu_count()}",
        f"memory: {_human(mem)} total, {_human(free)} free",
        f"loadavg: {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}",
        f"uptime: {uptime_min // 60}h {uptime_min % 60}m",
    ]
    du = shutil.disk_usage("/")
    lines.append(f"disk /: {_human(du.used)} used of {_human(du.total)} ({du.free // (2**30)} G free)")
    if include_battery:
        b = _battery()
        lines.append(f"battery: {b['percent']} ({b['status']}), present={b['present']}")
    return "\n".join(lines)


@tool(
    "battery",
    "Get current battery percentage and charge status. Useful for power/watchdog questions.",
    {},
    timeout=10,
)
def battery() -> str:
    b = _battery()
    return json_dump(b)


@tool(
    "disk_usage",
    "Get disk usage per mount point.",
    {},
    timeout=10,
)
def disk_usage() -> str:
    lines = []
    for p in ["/", "/home"]:
        try:
            du = shutil.disk_usage(p)
            lines.append(f"{p}: {_human(du.used)}/ {_human(du.total)} used, {_human(du.free)} free")
        except Exception:
            continue
    return "\n".join(lines) or "unable to read disk usage"


@tool(
    "running_processes",
    "List the most CPU or memory heavy running processes. Use for 'what's eating my cpu' style questions.",
    {"top": {"type": "integer", "description": "how many to show (default 10)", "required": False}},
    timeout=15,
)
def running_processes(top: int = 10) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        return "\n".join(out.splitlines()[: min(top, 50) + 1])
    except Exception as e:
        return f"error: {e}"


def json_dump(d: Dict) -> str:
    import json

    return json.dumps(d)
