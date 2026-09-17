import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .registry import tool

TASKS_DIR = Path.home() / ".aurora" / "tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = TASKS_DIR / "tasks.json"


@dataclass(eq=False)
class TaskState:
    id: str
    command: str
    log_path: Path
    proc: asyncio.subprocess.Process = field(default=None, repr=False)
    status: str = "starting"
    started: float = field(default_factory=time.time)
    finished: float | None = None
    exit_code: int | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


_tasks: dict[str, TaskState] = {}
_actions: list[dict] = []
_seq = 0
_lock = asyncio.Lock()
_io_lock = threading.Lock()


def _save_state() -> None:
    try:
        payload = {
            "tasks": [
                {
                    "id": s.id,
                    "command": s.command,
                    "log_path": str(s.log_path),
                    "status": s.status,
                    "started": s.started,
                    "finished": s.finished,
                    "exit_code": s.exit_code,
                }
                for s in _tasks.values()
            ],
            "actions": _actions[-20:],
        }
        with _io_lock:
            STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_state() -> None:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    global _seq
    for t in raw.get("tasks", []):
        try:
            done = t.get("status") in {"finished", "failed", "cancelled"}
            event = asyncio.Event()
            if done:
                event.set()
            _tasks[t["id"]] = TaskState(
                id=t["id"],
                command=t["command"],
                log_path=Path(t["log_path"]),
                status=t.get("status", "unknown"),
                started=float(t.get("started", 0)),
                finished=t.get("finished"),
                exit_code=t.get("exit_code"),
                done_event=event,
            )
            if t["id"].startswith("task-"):
                seq = int(t["id"].removeprefix("task-"))
                _seq = max(_seq, seq)
        except Exception:  # noqa: BLE001
            continue
    _actions.extend(raw.get("actions", []))


_load_state()


async def record_action(name: str, ok: bool, summary: str = "") -> None:
    _actions.append(
        {
            "name": str(name)[:60],
            "ok": bool(ok),
            "ts": time.time(),
            "summary": str(summary or "")[:200],
        }
    )
    _save_state()


async def _run_task(state: TaskState) -> None:
    log_fh = open(state.log_path, "ab")
    try:
        proc = await asyncio.create_subprocess_shell(
            state.command,
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )
        state.proc = proc
        state.status = "running"
        code = await proc.wait()
        state.exit_code = code
        if state.status != "cancelled":
            state.status = "finished" if code == 0 else "failed"
    except Exception as e:
        state.status = f"error: {e}"
    finally:
        log_fh.close()
        state.finished = time.time()
        state.done_event.set()
        _save_state()


async def _start(command: str) -> TaskState:
    global _seq
    async with _lock:
        _seq += 1
        tid = f"task-{_seq}"
        state = TaskState(id=tid, command=command, log_path=TASKS_DIR / f"{tid}.log")
        _tasks[tid] = state
        asyncio.create_task(_run_task(state))
        _save_state()
        return state


@tool(
    "start_task",
    "Start a long-running command in the background (server, download, build, watcher, batch job). "
    "Returns immediately with a task id; it survives your response. Check progress with task_log, list with list_tasks, stop with cancel_task.",
    {"command": {"type": "string", "description": "the command to run in the background", "required": True}},
    timeout=20,
)
async def start_task(command: str) -> str:
    state = await _start(command)
    return f"started task {state.id}: {command[:200]}\nlog: {state.log_path}"


@tool(
    "task_log",
    "Tail a background task's log file.",
    {
        "task_id": {"type": "string", "description": "task id (see list_tasks)", "required": True},
        "lines": {"type": "integer", "description": "last N lines (default 50)", "required": False},
    },
    timeout=15,
)
async def task_log(task_id: str, lines: int = 50) -> str:
    state = _tasks.get(task_id)
    if not state:
        return f"unknown task {task_id}; run list_tasks"
    try:
        log_lines = state.log_path.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"cannot read log: {e}"
    content = "\n".join(log_lines[-max(1, int(lines)) :])
    return f"{task_id} [{state.status}] exit={state.exit_code}\n{content}" if content else f"{task_id} [{state.status}] no output yet"


@tool(
    "list_tasks",
    "List all background tasks and their current status, plus the most recent one-shot actions "
    "(open/run/browser/device commands) so a failed or requested action is visible instead of silently missing.",
    {},
    timeout=10,
)
async def list_tasks() -> str:
    rows = []
    for s in sorted(_tasks.values(), key=lambda t: t.started, reverse=True):
        rows.append(f"{s.id:10} {s.status:12} {s.command[:80]}")
    action_rows = []
    for a in reversed(_actions[-8:]):
        ts = time.strftime("%H:%M", time.localtime(a.get("ts", 0)))
        mark = "ok" if a.get("ok") else "FAILED"
        action_rows.append(f"{ts} {mark:6} {a.get('name', '')} - {a.get('summary', '')[:90]}")
    chunks = []
    if not rows and not action_rows:
        return "no background tasks or recorded actions"
    if rows:
        chunks.append("tasks:")
        chunks.extend(rows)
    if action_rows:
        if chunks:
            chunks.append("")
        chunks.append("recent actions:")
        chunks.extend(action_rows)
    return "\n".join(chunks)


@tool(
    "cancel_task",
    "Terminate a running background task.",
    {"task_id": {"type": "string", "description": "task id", "required": True}},
    timeout=15,
)
async def cancel_task(task_id: str) -> str:
    state = _tasks.get(task_id)
    if not state:
        return f"unknown task {task_id}; run list_tasks"
    if state.proc and state.proc.returncode is None:
        try:
            state.status = "cancelled"
            state.proc.kill()
            await state.proc.wait()
        except Exception:
            pass
        _save_state()
        return f"cancelled {task_id}"
    return f"{task_id} is not running ({state.status})"