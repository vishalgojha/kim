import os
from pathlib import Path

from .registry import tool

MAX_LINES = 200


def _clip(text: str, head: int | None, tail: int | None) -> str:
    lines = text.splitlines()
    if head is not None and len(lines) > head:
        lines = lines[:head] + [f"... ({len(lines) - head} more lines) ..."]
    if tail is not None and len(lines) > tail:
        lines = lines[-tail:]
    return "\n".join(lines)


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    return p


@tool(
    "read_file",
    "Read a text file (bounded output). Use for inspecting source files, configs, notes.",
    {
        "path": {"type": "string", "description": "absolute or relative path", "required": True},
        "lines": {"type": "integer", "description": "max lines to return (default 200)", "required": False},
        "offset": {"type": "integer", "description": "start at this 1-based line (default 1)", "required": False},
    },
    timeout=20,
)
def read_file(path: str, lines: int = 200, offset: int = 1) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"file not found: {p}"
    if p.is_dir():
        return f"{p} is a directory; list it with files_list"
    try:
        raw = p.read_text(errors="replace")
    except Exception as e:
        return f"cannot read {p}: {e}"
    parts = raw.splitlines(keepends=True)
    o = max(1, offset) - 1
    chunk = "".join(parts[o : o + min(lines, MAX_LINES)])
    total = len(parts)
    meta = f"{p} ({total} lines); showing {o + 1}-{o + len(parts[o:o + min(lines, MAX_LINES)])}\n"
    return meta + chunk


@tool(
    "write_file",
    "Write content to a file, overwriting it (create directories if needed). Use carefully.",
    {"path": {"type": "string", "description": "absolute or relative path", "required": True}, "content": {"type": "string", "description": "full content to write", "required": True}},
    timeout=20,
)
def write_file(path: str, content: str) -> str:
    p = _resolve(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {len(content)} chars to {p}"
    except Exception as e:
        return f"write failed: {e}"


@tool(
    "edit_file",
    "Replace the first occurrence of old_text with new_text in a file. Use for small surgical edits.",
    {
        "path": {"type": "string", "description": "absolute or relative path", "required": True},
        "old": {"type": "string", "description": "exact text to replace (must exist)", "required": True},
        "new": {"type": "string", "description": "replacement text", "required": True},
    },
    timeout=20,
)
def edit_file(path: str, old: str, new: str) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"file not found: {p}"
    try:
        text = p.read_text()
    except Exception as e:
        return f"cannot read {p}: {e}"
    if old not in text:
        return f"old text not found in {p}"
    p.write_text(text.replace(old, new, 1))
    return f"edited {p}"


@tool(
    "files_list",
    "List a directory's contents with sizes. Bounded output.",
    {"path": {"type": "string", "description": "directory (default: current)", "required": False}, "limit": {"type": "integer", "description": "max entries (default 100)", "required": False}},
    timeout=15,
)
def files_list(path: str = ".", limit: int = 100) -> str:
    p = _resolve(path).resolve()
    if not p.exists():
        return f"path not found: {p}"
    if not p.is_dir():
        return f"{p} is a file"
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception as e:
        return f"cannot list {p}: {e}"
    rows = []
    hidden_only = all(e.name.startswith(".") for e in entries[:100])
    for e in entries[:limit]:
        if hidden_only or not e.name.startswith("."):
            if e.is_dir():
                rows.append(f"DIR   {e.name}")
            else:
                try:
                    sz = e.stat().st_size
                except Exception:
                    sz = 0
                rows.append(f"{sz:>10} {e.name}")
    if len(entries) > limit:
        rows.append(f"... {len(entries) - limit} more entries")
    return f"{p}\n" + "\n".join(rows)


@tool(
    "files_search",
    "Search file contents with a regex. Uses ripgrep; you may pass a directory and a max line cap.",
    {
        "pattern": {"type": "string", "description": "regex to search for", "required": True},
        "path": {"type": "string", "description": "directory to search (default current)", "required": False},
        "max_matches": {"type": "integer", "description": "max results (default 50)", "required": False},
    },
    timeout=30,
)
def files_search(pattern: str, path: str = ".", max_matches: int = 50) -> str:
    import subprocess

    p = _resolve(path).resolve()
    cmd = ["rg", "--no-heading", "-n", "-m", "3", "--color", "never", pattern, str(p)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
    except Exception as e:
        return f"rg error: {e}"
    lines = out.splitlines()
    return "\n".join(lines[: max_matches,]) if lines else f"no matches for {pattern} in {p}"


@tool(
    "file_info",
    "Get metadata about a path: type, size, mtime.",
    {"path": {"type": "string", "description": "path", "required": True}},
    timeout=10,
)
def file_info(path: str) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"path not found: {p}"
    try:
        st = p.stat()
        kind = "dir" if p.is_dir() else "file"
        import datetime

        mtime = datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
        return f"{p}: {kind}, {st.st_size} bytes, modified {mtime}"
    except Exception as e:
        return f"error: {e}"