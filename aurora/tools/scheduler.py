import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from .context import get_ctx, schedule_id
from .registry import tool

PENDING: Dict[str, Dict[str, Any]] = {}

_NUMBER = r"(\d+)"
_REL_RE = re.compile(rf"^\s*{_NUMBER}\s*(?:seconds?|s)\s*$|^\s*{_NUMBER}\s*(?:minutes?|m)\s*$|^\s*{_NUMBER}\s*(?:hours?|h)\s*$|^\s*{_NUMBER}\s*(?:days?|d)\s*$", re.I)


def _parse_delay(delay: str) -> float:
    m = _REL_RE.match(delay)
    if m:
        if m.group(1):
            return float(m.group(1))
        if m.group(2):
            return float(m.group(2)) * 60
        if m.group(3):
            return float(m.group(3)) * 3600
        if m.group(4):
            return float(m.group(4)) * 86400
    for fmt in ("%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            now = datetime.now().astimezone()
            parsed = datetime.strptime(delay.strip(), fmt)
            if fmt == "%H:%M":
                dt = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                if dt <= now:
                    dt += timedelta(days=1)
            else:
                dt = parsed.replace(tzinfo=now.tzinfo)
            return max(0, (dt - now).total_seconds())
        except ValueError:
            continue
    raise ValueError(f"cannot parse time '{delay}'. Use '5 minutes', '14:30', or an ISO datetime like '2026-09-15T10:00:00'.")


async def _sleep_and_fire(info: Dict[str, Any]) -> None:
    try:
        while info["times_left"] is None or info["times_left"] > 0:
            await asyncio.sleep(info["delay"])
            if asyncio.get_event_loop().is_closed():
                break
            n = get_ctx().get("notifier")
            if n:
                await n.speak(info["text"])
            if info["times_left"] is not None:
                info["times_left"] -= 1
                if info["times_left"] <= 0:
                    break
    except asyncio.CancelledError:
        return


@tool(
    "schedule_remind",
    "Schedule a one-shot spoken reminder at a future time. The agent will speak the text when the time arrives.\n"
    "delay: a relative time like '5 minutes', '30 seconds', '2 hours'.\n"
    "when: an absolute time (ISO like '2026-09-15T10:00:00', or '14:30' for today, or '2026-09-15 09:30').\n"
    "Either delay or when is required.",
    {
        "delay": {"type": "string", "description": "relative time from now, e.g. '10 minutes', '30 seconds'", "required": False},
        "when": {"type": "string", "description": "absolute time (ISO or HH:MM)", "required": False},
        "text": {"type": "string", "description": "what to say when the time arrives", "required": True},
    },
    timeout=20,
)
async def schedule_remind(delay: str = "", when: str = "", text: str = "") -> str:
    if not delay and not when:
        return "either delay or when is required"
    try:
        seconds = _parse_delay(delay or when)
    except ValueError as e:
        return str(e)
    if seconds < 0:
        return "that time is in the past"
    secs = min(max(0.5, seconds), 7 * 86400)
    sid = schedule_id("remind")
    info = {"id": sid, "text": text, "delay": secs, "times_left": 1}
    task = asyncio.create_task(_sleep_and_fire(info))
    PENDING[sid] = {**info, "task": task}
    if secs < 10:
        return f"scheduled reminder {sid} in {int(secs)}s"
    return f"scheduled reminder {sid} in {int(secs // 60)} minutes {int(secs % 60)}s"


@tool(
    "schedule_every",
    "Repeat a spoken message every N seconds. Useful for periodic status checks, reminders, or timers.\n"
    "Use list_schedules to view running ones; cancel with schedule_cancel.",
    {
        "interval": {"type": "string", "description": "like schedule_remind: '30 seconds', '1 hour', etc.", "required": True},
        "times": {"type": "integer", "description": "how many times to repeat (default unlimited)", "required": False},
        "text": {"type": "string", "description": "what to say each time", "required": True},
    },
    timeout=20,
)
async def schedule_every(interval: str, text: str, times: int | None = None) -> str:
    try:
        secs = _parse_delay(interval)
    except ValueError as e:
        return str(e)
    if secs < 0:
        return "interval cannot be negative"
    secs = max(1.0, secs)
    sid = schedule_id("every")
    info = {"id": sid, "text": text, "delay": secs, "times_left": times}
    task = asyncio.create_task(_sleep_and_fire(info))
    PENDING[sid] = {**info, "task": task}
    return f"started repeating schedule {sid} every {int(secs)}s ({'unlimited' if times is None else f'{times} times'})"


@tool("list_schedules", "List active scheduled reminders/repeats.", {}, timeout=10)
async def list_schedules() -> str:
    if not PENDING:
        return "no active schedules"
    rows = []
    for info in list(PENDING.values()):
        rows.append(f"{info['id']:12} every {info['delay']:.0f}s  {info['text'][:80]}")
    return "\n".join(rows)


@tool("schedule_cancel", "Cancel a scheduled reminder/repeat.", {"id": {"type": "string", "description": "schedule id", "required": True}}, timeout=10)
async def schedule_cancel(id: str) -> str:
    info = PENDING.pop(id, None)
    if not info:
        return f"no schedule {id}"
    info["task"].cancel()
    return f"cancelled {id}"
