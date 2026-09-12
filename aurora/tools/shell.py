import asyncio
import os

from ..permissions import PermissionDenied
from ..tools.context import get_ctx
from .registry import tool


@tool(
    "run_shell",
    "Run a shell command on the machine and return its output. This is the most powerful tool. "
    "Some sensitive commands need explicit confirmation: for those, ask the user for a clear verbal yes and "
    "then retry with confirm set to 'yes'. Never retry without their confirmation. "
    "For long-running work (servers, downloads, batch jobs) prefer start_task so it survives your response.",
    {
        "command": {"type": "string", "description": "the full command line to execute", "required": True},
        "confirm": {"type": "string", "description": "set to 'yes' only after the user verbally confirms a sensitive command", "required": False},
        "timeout": {"type": "integer", "description": "seconds to wait (default 60)", "required": False},
        "cwd": {"type": "string", "description": "working directory (default current)", "required": False},
    },
    timeout=120,
)
async def run_shell(command: str, confirm: str = "", timeout: int = 60, cwd: str = "") -> str:
    policy = get_ctx()["policy"]
    policy.check(command, confirm=confirm.strip().lower() == "yes")
    cmd_timeout = policy.timeout_secs if not timeout else min(int(timeout), policy.timeout_secs or 3600)
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd or None,
        env=os.environ.copy(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=max(1, cmd_timeout))
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"command timed out after {cmd_timeout}s and was killed."
    text = out.decode(errors="replace") if out else ""
    if len(text) > policy.max_output_chars:
        text = text[: policy.max_output_chars] + f"\n... output truncated ({len(out)} bytes total)"
    return text if text.strip() else f"(no output, exit code {proc.returncode})"