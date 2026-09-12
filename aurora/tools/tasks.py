import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .registry import tool

TASKS_DIR = Path.home() / ".aurora" / "tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)


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
_seq = 0
_lock = asyncio.Lock()


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


async def _start(command: str) -> TaskState:
    global _seq
    async with _lock:
        _seq += 1
        tid = f"task-{_seq}"
        state = TaskState(id=tid, command=command, log_path=TASKS_DIR / f"{tid}.log")
        _tasks[tid] = state
        asyncio.create_task(_run_task(state))
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


@tool("list_tasks", "List all background tasks and their current status.", {}, timeout=10)
async def list_tasks() -> str:
    if not _tasks:
        return "no background tasks"
    rows = []
    for s in sorted(_tasks.values(), key=lambda t: t.started, reverse=True):
        rows.append(f"{s.id:10} {s.status:12} {s.command[:80]}")
    return "\n".join(rows)


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
        return f"cancelled {task_id}"
    return f"{task_id} is not running ({state.status})"
