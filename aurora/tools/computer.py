"""Computer-agent style control of the laptop desktop (screen, mouse, keyboard, windows)."""

from __future__ import annotations

import asyncio
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .registry import tool
from ..vision import describe_image, is_vision_configured

IS_WINDOWS = platform.system().lower() == "windows"

if IS_WINDOWS:
    from .win32 import (  # noqa: F401
        click as _win_click, drag as _win_drag, grab_png as _win_grab,
        key as _win_key, launch as _win_launch, move as _win_move,
        ocr as _win_ocr, scroll as _win_scroll, type_text as _win_type,
        window_activate as _win_window_activate, window_list as _win_window_list,
        window_move as _win_window_move,
    )


def _ran(cmd: List[str]) -> Tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            return r.returncode, err[:500] or f"exit {r.returncode}"
        return 0, out
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out: {' '.join(cmd)}"


_PORTAL_SCRIPT = r"""
import asyncio
from dbus_next import Message, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType, MessageType


async def _main():
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    reply = await bus.call(Message(
        destination="org.freedesktop.portal.Desktop",
        path="/org/freedesktop/portal/desktop",
        interface="org.freedesktop.portal.Screenshot",
        member="Screenshot", signature="sa{sv}",
        body=["", {"interactive": Variant("b", False)}]))
    if reply.message_type == MessageType.ERROR:
        print("portal error: " + str(reply.body[0]))
        return
    handle = reply.body[0]
    fut = asyncio.get_running_loop().create_future()
    loop = asyncio.get_running_loop()
    bus.add_message_handler(lambda m: fut.set_result(m) if (m.message_type == MessageType.SIGNAL and m.member == "Response" and not fut.done()) else None)
    try:
        msg = await asyncio.wait_for(fut, timeout=8)
    except asyncio.TimeoutError:
        print("portal timeout")
        return
    resp, results = msg.body
    uri = results.get("uri")
    if isinstance(uri, Variant):
        uri = uri.value
    if resp == 0 and uri:
        print(str(uri)[len("file://"):] if str(uri).startswith("file://") else str(uri))
    else:
        print("portal declined screenshot")


asyncio.run(_main())
"""


def _portal_capture() -> Tuple[bool, str]:
    try:
        import dbus_next  # noqa: F401
    except ImportError:
        return False, ""
    rc, out = _ran([sys.executable, "-c", _PORTAL_SCRIPT])
    if rc != 0 or not (out or "").strip():
        return False, out[:500]
    p = Path(out.strip())
    if p.exists() and p.stat().st_size > 0:
        return True, str(p)
    return False, f"portal capture produced no file: {p}"


def _capture(path: str) -> Tuple[bool, str]:
    target = Path(path).expanduser()
    ok, uri = _portal_capture()
    if ok:
        try:
            target.write_bytes(Path(uri).read_bytes())
            return True, "saved screenshot: " + str(target)
        except OSError as exc:
            return False, f"could not save portal screenshot: {exc}"
    strategies = [
        ["gdbus", "call", "--session", "--dest", "org.gnome.Shell", "--object-path",
         "/org/gnome/Shell/Screenshot", "--method", "org.gnome.Shell.Screenshot.Screenshot",
         "true", "false", str(target.resolve())],
        ["gnome-screenshot", "-f", str(target.resolve())],
        ["grim", str(target.resolve())],
        ["scrot", str(target.resolve())],
        ["import", "-window", "root", str(target.resolve())],
    ]
    for cmd in strategies:
        if shutil.which(cmd[0]):
            rc, out = _ran(cmd)
            p = target
            if rc == 0 and p.exists() and p.stat().st_size > 0:
                return True, "saved screenshot: " + str(p)
            if rc != 0 and cmd[0] == "gdbus":
                gb = re.search(r"\(true,\s*'([^']+)'\)", out)
                if gb:
                    p = Path(gb.group(1))
                    if p.exists() and p.stat().st_size > 0:
                        return True, "saved screenshot: " + str(p)
    return False, "could not capture the screen (Wayland portal or gnome-screenshot/grim/scrot/imagemagick required)"


def _ocr(path: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
    if not shutil.which("tesseract"):
        return False, "tesseract is not installed; run: sudo apt install -y tesseract-ocr", []
    rc, out = _ran(["tesseract", str(Path(path).expanduser().resolve()), "stdout", "-l", "eng", "tsv"])
    if rc != 0:
        return False, out, []
    words: List[Dict[str, Any]] = []
    for line in (out or "").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or parts[10].strip().lower() in {"", "-1"}:
            continue
        try:
            words.append({
                "text": parts[11],
                "x": int(float(parts[6])),
                "y": int(float(parts[7])),
                "w": int(float(parts[8])),
                "h": int(float(parts[9])),
                "conf": float(parts[10]),
            })
        except (ValueError, IndexError):
            continue
    text = " ".join(w["text"] for w in words)
    return True, text[:4000], words


def _input(cmd: List[str]) -> Tuple[bool, str]:
    tool = "ydotool" if shutil.which("ydotool") else "xdotool"
    if not shutil.which(tool):
        return False, "no input tool found on the laptop; install xdotool (X11) or ydotool (Wayland)"
    rc, out = _ran([tool] + cmd)
    if rc != 0:
        return False, out or f"{tool} failed"
    return True, out


async def computer_action(action: str = "", x: int = 0, y: int = 0, dx: int = 0, dy: int = 0,
                          button: str = "1", text: str = "", key: str = "", title: str = "",
                          window: str = "", amount: int = 1, delay_ms: int = 50,
                          width: int = 0, height: int = 0) -> str:
    a = (action or "").strip().lower()
    if not a:
        return "computer_action needs an action: move, click, dblclick, drag, scroll, type, key, window_list, window_activate, window_move, see, describe, ocr, click_label"

    if IS_WINDOWS:
        return await _computer_action_windows(a, x, y, dx, dy, button, text, key, title, window, amount, delay_ms, width, height)

    if a == "ocr":
        with tempfile.TemporaryDirectory() as tmp:
            png = str(Path(tmp) / "kim_screen.png")
            ok, cap = _capture(png)
            if not ok:
                return cap
            okl, ocr, words = _ocr(png)
            if not okl:
                return cap + "; " + ocr
        return ocr

    if a in {"see", "describe"}:
        with tempfile.TemporaryDirectory() as tmp:
            png = str(Path(tmp) / "kim_screen.png")
            ok, cap = _capture(png)
            if not ok:
                return cap
            try:
                data = Path(png).read_bytes()
            except OSError:
                data = b""
            desc = (await describe_image(data)) if is_vision_configured() and data else ""
            okl, ocr, words = _ocr(png)
            if a == "describe":
                if is_vision_configured() and desc and not desc.startswith("vision "):
                    return f"{cap}. {desc}"
                return f"{cap}. Screen text: {ocr[:1500]}" if okl and ocr.strip() else f"{cap}. No readable text detected."
            parts = [cap]
            if is_vision_configured() and desc and not desc.startswith("vision "):
                parts.append(f"On-screen: {desc}")
            if okl and ocr.strip():
                parts.append(f"OCR text: {ocr[:2000]}")
            return ". ".join(parts)

    if a == "click_label":
        if not text.strip():
            return "click_label needs the on-screen text to click (text=...)"
        with tempfile.TemporaryDirectory() as tmp:
            png = str(Path(tmp) / "kim_screen.png")
            ok, cap = _capture(png)
            if not ok:
                return cap
            okl, ocr, words = _ocr(png)
            if not okl or not words:
                return cap + "; " + (ocr or "no text found on screen")
        target = text.strip().lower()
        matches = [w for w in words if target in w["text"].lower()]
        if not matches:
            return f"label '{text}' not found on screen. Screen text: {ocr[:500]}"
        cx = min(r["x"] for r in matches) + (max(r["x"] + r["w"] for r in matches) - min(r["x"] for r in matches)) // 2
        cy = min(r["y"] for r in matches) + (max(r["y"] + r["h"] for r in matches) - min(r["y"] for r in matches)) // 2
        ok, res = _input(["mousemove", "--absolute", "--x", str(cx), "--y", str(cy)]) if shutil.which("ydotool") else _input(["mousemove", str(cx), str(cy)])
        if not ok:
            return res
        _input(["mousemove", str(cx), str(cy)])
        cok, cres = _input(["click", button])
        if not cok:
            return cres
        return f"clicked '{text}' at ({cx},{cy})"

    if a == "move":
        ok, res = _input(["mousemove", "--absolute", "--x", str(x), "--y", str(y)]) if shutil.which("ydotool") else _input(["mousemove", str(x), str(y)])
        return res if not ok else f"pointer moved to ({x},{y})"

    if a in {"click", "dblclick"}:
        if x or y:
            ok, res = _input(["mousemove", "--absolute", "--x", str(x), "--y", str(y)]) if shutil.which("ydotool") else _input(["mousemove", str(x), str(y)])
            if not ok:
                return res
        cmd = ["click", "--repeat", str(2 if a == "dblclick" else 1), "--delay", str(delay_ms), button]
        ok, res = _input(cmd)
        return res if not ok else f"{a} at ({x},{y}) button {button}"

    if a == "drag":
        ok, res = _input(["mousemove", "--absolute", "--x", str(x), "--y", str(y)]) if shutil.which("ydotool") else _input(["mousemove", str(x), str(y)])
        if ok:
            _input(["mousedown", button])
            d = _input(["mousemove_relative", "--x", str(dx), "--y", str(dy)]) if shutil.which("ydotool") else _input(["mousemove_relative", str(dx), str(dy)])
            if d[0]:
                _input(["mouseup", button])
                return f"dragged from ({x},{y}) by ({dx},{dy})"
            return d[1]
        return res

    if a == "scroll":
        btn = "4" if dy > 0 or dx < 0 else "5" if dy < 0 or dx > 0 else "1"
        if dx != 0:
            btn = "6" if dx > 0 else "7"
        n = max(1, abs(amount))
        ok, res = _input(["click", "--repeat", str(n), "--delay", str(delay_ms), btn])
        return res if not ok else f"scrolled {n}x (button {btn})"

    if a == "type":
        ok, res = _input(["type", "--delay", "30", text])
        return res if not ok else f"typed {len(text)} chars"

    if a == "key":
        keys = key.split("+")
        ok, res = _input(["key"] + keys)
        return res if not ok else f"sent key {key}"

    if a == "window_list":
        rc, res = _ran(["xdotool", "search", "--onlyvisible", "--name", "."])
        if rc != 0:
            return res or "xdotool window search failed"
        lines = []
        for wid in (res.splitlines() or [])[:25]:
            wid = wid.strip()
            if not wid:
                continue
            _, name = _ran(["xdotool", "getwindowname", wid])
            _, geo = _ran(["xdotool", "getwindowgeometry", "--shell", wid])
            g = {k: v for k, v in (kv.split("=") for kv in geo.splitlines() if "=" in kv)}
            lines.append(f"{wid} {g.get('X', '?')},{g.get('Y', '?')} {g.get('WIDTH', '?')}x{g.get('HEIGHT', '?')} {name[:60] or '(unnamed)'}")
        return "\n".join(lines) if lines else "no visible windows found"

    if a == "window_activate":
        target = title or window or ""
        if not target:
            return "window_activate needs title= or window=<id>"
        if target.isdigit():
            return _input(["windowactivate", "--sync", target])[1] if (_input(["windowactivate", "--sync", target])[0]) else _input(["windowactivate", target])[1]
        rc, out = _ran(["xdotool", "search", "--onlyvisible", "--name", target])
        if rc != 0 or not (out or "").strip():
            return f"no visible window matching '{target}'"
        wid = out.splitlines()[0].strip()
        ok, res = _input(["windowactivate", "--sync", wid])
        return res if not ok else f"activated window {wid} ({target})"

    if a == "window_move":
        target = window or title or ""
        if not target:
            return "window_move needs window=<id> and x, y"
        if not target.isdigit():
            rc, out = _ran(["xdotool", "search", "--name", target])
            if rc == 0 and (out or "").strip():
                target = out.splitlines()[0].strip()
        ok, res = _input(["windowmove"] + ([target, str(x), str(y)] if not width else [target, str(x), str(y), str(width), str(height)]))
        if not ok:
            return res
        return f"moved window {target} to ({x},{y})" + (f" size {width}x{height}" if width else "")

    return f"unsupported computer_action: {action}"


async def _computer_action_windows(a: str, x: int, y: int, dx: int, dy: int, button: str,
                                   text: str, key: str, title: str, window: str,
                                   amount: int, delay_ms: int, width: int, height: int) -> str:
    if a == "ocr":
        with tempfile.TemporaryDirectory() as tmp:
            png = str(Path(tmp) / "kim_screen.png")
            ok, cap = _win_grab(png)
            if not ok:
                return cap
            okl, ocr, words = _win_ocr(png)
            if not okl:
                return cap + "; " + ocr
        return ocr

    if a in {"see", "describe"}:
        with tempfile.TemporaryDirectory() as tmp:
            png = str(Path(tmp) / "kim_screen.png")
            ok, cap = _win_grab(png)
            if not ok:
                return cap
            try:
                data = Path(png).read_bytes()
            except OSError:
                data = b""
            desc = (await describe_image(data)) if is_vision_configured() and data else ""
            okl, ocr, words = _win_ocr(png)
            if a == "describe":
                if is_vision_configured() and desc and not desc.startswith("vision "):
                    return f"{cap}. {desc}"
                return f"{cap}. Screen text: {ocr[:1500]}" if okl and ocr.strip() else f"{cap}. No readable text detected."
            parts = [cap]
            if is_vision_configured() and desc and not desc.startswith("vision "):
                parts.append(f"On-screen: {desc}")
            if okl and ocr.strip():
                parts.append(f"OCR text: {ocr[:2000]}")
            return ". ".join(parts)

    if a == "click_label":
        if not text.strip():
            return "click_label needs the on-screen text to click (text=...)"
        with tempfile.TemporaryDirectory() as tmp:
            png = str(Path(tmp) / "kim_screen.png")
            ok, cap = _win_grab(png)
            if not ok:
                return cap
            okl, ocr, words = _win_ocr(png)
            if not okl or not words:
                return cap + "; " + (ocr or "no text found on screen")
        target = text.strip().lower()
        matches = [w for w in words if target in w["text"].lower()]
        if not matches:
            return f"label '{text}' not found on screen. Screen text: {ocr[:500]}"
        cx = min(r["x"] for r in matches) + (max(r["x"] + r["w"] for r in matches) - min(r["x"] for r in matches)) // 2
        cy = min(r["y"] for r in matches) + (max(r["y"] + r["h"] for r in matches) - min(r["y"] for r in matches)) // 2
        cok, cres = _win_click(cx, cy, 1, button, delay_ms)
        if not cok:
            return cres
        return f"clicked '{text}' at ({cx},{cy})"

    if a == "move":
        ok, res = _win_move(x, y)
        return res if not ok else f"pointer moved to ({x},{y})"

    if a in {"click", "dblclick"}:
        clicks = 2 if a == "dblclick" else 1
        ok, res = _win_click(x, y, clicks, button, delay_ms)
        return res if not ok else f"{a} at ({x},{y}) button {button}"

    if a == "drag":
        ok, res = _win_drag(x, y, dx, dy, button)
        return res if not ok else f"dragged from ({x},{y}) by ({dx},{dy})"

    if a == "scroll":
        ok, res = _win_scroll(amount, dx)
        return res if not ok else f"scrolled {amount}"

    if a == "type":
        ok, res = _win_type(text)
        return res if not ok else f"typed {len(text)} chars"

    if a == "key":
        ok, res = _win_key(key)
        return res if not ok else f"sent key {key}"

    if a == "window_list":
        ok, res = _win_window_list()
        return res if ok else "window list failed: " + res

    if a == "window_activate":
        ref = title or window or ""
        if not ref:
            return "window_activate needs title= or window=<id>"
        ok, res = _win_window_activate(ref)
        return res if ok else "failed to activate: " + res

    if a == "window_move":
        ref = window or title or ""
        if not ref:
            return "window_move needs window=<id> and x, y"
        ok, res = _win_window_move(ref, x, y, width, height)
        return res if not ok else f"moved window {ref} to ({x},{y})" + (f" size {width}x{height}" if width and height else "")

    return f"unsupported computer_action: {action}"


@tool(
    "computer_action",
    "Operate the laptop like a computer agent: see/ocr (see screen text), click_label (click text seen on screen), move/click/dblclick/drag/scroll (mouse), type/key (keyboard), window_list/window_activate/window_move (windows). Runs on the laptop relay only.",
    {
        "action": {"type": "string", "description": "move, click, dblclick, drag, scroll, type, key, window_list, window_activate, window_move, see, describe, ocr, click_label", "required": True},
        "x": {"type": "integer", "description": "absolute x coordinate", "required": False},
        "y": {"type": "integer", "description": "absolute y coordinate", "required": False},
        "dx": {"type": "integer", "description": "relative x for drag", "required": False},
        "dy": {"type": "integer", "description": "relative y for drag", "required": False},
        "button": {"type": "string", "description": "1 left, 2 middle, 3 right", "required": False},
        "text": {"type": "string", "description": "text to type or the on-screen label to click (click_label)", "required": False},
        "key": {"type": "string", "description": "key or combo like ctrl+c enter", "required": False},
        "title": {"type": "string", "description": "window title substring to activate or move", "required": False},
        "window": {"type": "string", "description": "window id (activates/moves by id)", "required": False},
        "amount": {"type": "integer", "description": "scroll steps", "required": False},
        "delay_ms": {"type": "integer", "description": "delay between clicks in ms", "required": False},
        "width": {"type": "integer", "description": "new window width for window_move", "required": False},
        "height": {"type": "integer", "description": "new window height for window_move", "required": False},
    },
    timeout=60,
)
async def computer_action_tool(action: str = "", x: int = 0, y: int = 0, dx: int = 0, dy: int = 0,
                               button: str = "1", text: str = "", key: str = "", title: str = "",
                               window: str = "", amount: int = 1, delay_ms: int = 50,
                               width: int = 0, height: int = 0) -> str:
    return await computer_action(action, x, y, dx, dy, button, text, key, title, window, amount, delay_ms, width, height)