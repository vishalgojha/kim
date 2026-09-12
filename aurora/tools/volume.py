import asyncio
import shutil
import subprocess

from .registry import tool


async def _pactl(args: list[str]) -> str:
    if not shutil.which("pactl"):
        if shutil.which("pw-cli"):
            return "pactl not found (pipewire detected: try pactl from pulseaudio-utils)"
        return "pactl not installed (install pulseaudio-utils)"
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        return out.decode(errors="replace").strip() or "(no output)"
    except Exception as e:
        return f"pactl error: {e}"


@tool("volume_get", "Get the current speaker volume percentage and mute state.", {}, timeout=15)
async def volume_get() -> str:
    out = await _pactl(["get-sink-volume", "@DEFAULT_SINK@"])
    muted = await _pactl(["get-sink-mute", "@DEFAULT_SINK@"])
    return f"volume:\n{out}\nmute: {muted}"


@tool(
    "volume_set",
    "Set speaker volume and/or mute. Percent 0-150.",
    {
        "percent": {"type": "integer", "description": "target volume 0-150", "required": False},
        "mute": {"type": "boolean", "description": "true=mute, false=unmute", "required": False},
    },
    timeout=15,
)
async def volume_set(percent: int | None = None, mute: bool | None = None) -> str:
    lines = []
    if percent is not None:
        lines.append(await _pactl(["set-sink-volume", "@DEFAULT_SINK@", f"{int(percent)}%"]))
    if mute is not None:
        lines.append(await _pactl(["set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"]))
    return "; ".join(lines) if lines else "nothing to do"