"""Windows backend for Kim's desktop computer-agent tools.

Used only at runtime (never imported into the Linux relay's hot path). Provides
screen capture (Pillow), OCR (pytesseract or WinRT OCR), input (pyautogui), and
window control (pygetwindow) behind the same (ok, message) style as the Linux
helpers on the other side.
"""

from __future__ import annotations

import platform
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

IS_WINDOWS = platform.system().lower() == "windows"

_PIL = None
_PYAUTOGUI = None
_PYGETWINDOW = None
_PYTESSERACT = None
_tools_lock = threading.Lock()

_KEY_MAP = {
    "Return": "enter", "Escape": "esc", "BackSpace": "backspace", "Delete": "delete",
    "Home": "home", "End": "end", "Page_Up": "pageup", "Page_Down": "pagedown",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right", "space": "space",
    "Tab": "tab",
}


def _load() -> Tuple[bool, str]:
    global _PIL, _PYAUTOGUI, _PYGETWINDOW, _PYTESSERACT
    with _tools_lock:
        if _PIL is not None:
            return True, ""
        try:
            import PIL.ImageGrab  # type: ignore
            import pyautogui  # type: ignore
            import pygetwindow  # type: ignore
            _PIL, _PYAUTOGUI, _PYGETWINDOW = PIL.ImageGrab, pyautogui, pygetwindow
        except Exception as exc:  # noqa: BLE001
            return False, (
                "Windows desktop tools need Pillow, pyautogui and pygetwindow; "
                f"install them with:  py -m pip install pillow pyautogui pygetwindow ({exc})"
            )
        try:
            import pytesseract  # type: ignore
            _PYTESSERACT = pytesseract  # noqa: SG001
        except Exception:  # noqa: BLE001
            _PYTESSERACT = None
        return True, ""


def _ensure() -> Tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Windows backend requested on a non-Windows system"
    return _load()


def grab_png(path: str) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        image = _PIL.ImageGrab.grab(all_screens=True)
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(target), "PNG")
        return True, "saved screenshot: " + str(target)
    except Exception as exc:  # noqa: BLE001
        return False, f"could not capture the Windows screen: {str(exc)[:300]}"


def _ocr_words(path: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
    ok, msg = _ensure()
    if not ok:
        return False, msg, []
    if _PYTESSERACT is not None:
        try:
            data = _PYTESSERACT.image_to_data(Path(path).resolve(), output_type=_PYTESSERACT.Output.DICT)
        except Exception as exc:  # noqa: BLE001
            return False, f"pytesseract OCR failed: {str(exc)[:200]}", []
        words: List[Dict[str, Any]] = []
        for i, text in enumerate(data.get("text", []) or []):
            conf = _safe_int(data.get("conf", []), i, -1)
            if not str(text).strip() or conf < 30:
                continue
            try:
                words.append({
                    "text": str(text),
                    "x": int(float(data["left"][i])),
                    "y": int(float(data["top"][i])),
                    "w": int(float(data["width"][i])),
                    "h": int(float(data["height"][i])),
                    "conf": float(conf),
                })
            except (IndexError, TypeError, ValueError):
                continue
        text = " ".join(w["text"] for w in words)
        return True, text[:4000], words
    return _winrt_ocr(path)


def _safe_int(values: List[Any], index: int, default: int) -> int:
    try:
        return int(values[index])
    except (IndexError, TypeError, ValueError):
        return default


def _winrt_ocr(path: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
    try:
        import winsdk.windows.graphics.imaging as imaging  # type: ignore
        import winsdk.windows.media.ocr as ocr  # type: ignore
        import winsdk.windows.storage.streams as streams  # type: ignore
    except Exception:  # noqa: BLE001
        return False, (
            "Windows OCR needs pytesseract + tesseract.exe, or the WinRT bindings; "
            "install tesseract for Windows (https://github.com/UB-Mannheim/tesseract/wiki) "
            "and:  py -m pip install pytesseract"
        ), []
    try:
        from asyncio import get_event_loop
        loop = get_event_loop()
        stream = streams.InMemoryRandomAccessStream()
        writer = streams.DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(Path(path).read_bytes())
        writer.store_async().get()
        stream.seek(0)
        decoder = imaging.BitmapDecoder.create_async(stream).get()
        bitmap = decoder.get_software_bitmap_async().get()
        engine = ocr.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return False, "no Windows OCR language pack is installed", []
        result = engine.recognize_async(bitmap).get()
        words: List[Dict[str, Any]] = []
        for line in result.lines:
            for w in line.words:
                r = w.bounding_rect
                if not str(w.text).strip():
                    continue
                words.append({
                    "text": str(w.text),
                    "x": int(r.x), "y": int(r.y),
                    "w": int(r.width), "h": int(r.height),
                    "conf": 100.0,
                })
        text = " ".join(wv["text"] for wv in words)
        return True, text[:4000], words
    except Exception as exc:  # noqa: BLE001
        return False, f"WinRT OCR failed: {str(exc)[:200]}", []


def ocr(path: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
    return _ocr_words(path)


def _click_button(button: str) -> str:
    return {"1": "left", "2": "middle", "3": "right"}.get(button, "left")


def move(x: int, y: int) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        _PYAUTOGUI.moveTo(x, y)
        return True, f"pointer moved to ({x},{y})"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def click(x: int, y: int, clicks: int = 1, button: str = "1", delay_ms: int = 50) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        _PYAUTOGUI.click(x, y, clicks=clicks, button=_click_button(button), interval=delay_ms / 1000.0)
        label = "double-clicked" if clicks >= 2 else "clicked"
        return True, f"{label} at ({x},{y}) button {button}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def drag(x: int, y: int, dx: int, dy: int, button: str = "1") -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        _PYAUTOGUI.moveTo(x, y)
        _PYAUTOGUI.dragRel(dx, dy, duration=0.15, button=_click_button(button))
        return True, f"dragged from ({x},{y}) by ({dx},{dy})"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def scroll(amount: int, dx: int = 0) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        if dx:
            _PYAUTOGUI.hscroll(dx)
        _PYAUTOGUI.scroll(max(-200, min(200, amount)))
        return True, f"scrolled {amount}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def type_text(text: str) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        _PYAUTOGUI.write(text, interval=0.008)
        return True, f"typed {len(text)} chars"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def key(combo: str) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        keys = [_KEY_MAP.get(k.strip(), k.strip()) for k in combo.split("+") if k.strip()]
        if not keys:
            return False, "empty key combo"
        if len(keys) == 1:
            _PYAUTOGUI.press(keys[0])
        else:
            _PYAUTOGUI.hotkey(*keys)
        return True, f"sent key {combo}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def window_list() -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        wins = _PYGETWINDOW.getAllTitles()
        lines = []
        for title in wins:
            title = title.strip()
            if not title:
                continue
            try:
                match = _PYGETWINDOW.getWindowsWithTitle(title)
                win = match[0] if match else None
                geo = ""
                if win is not None:
                    x, y = win.left, win.top
                    geo = f" {x},{y} {win.width}x{win.height}"
            except Exception:  # noqa: BLE001
                geo = ""
            lines.append(f"{title[:70]}{geo}")
            if len(lines) >= 25:
                break
        return True, "\n".join(lines) if lines else "no visible windows found"
    except Exception as exc:  # noqa: BLE001
        return False, f"window list failed: {str(exc)[:200]}"


def window_activate(title: str) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        import pygetwindow  # type: ignore
        match = _pick_window(title)
        if match is None:
            return False, f"no visible window matching '{title}'"
        if match.isMinimized:
            match.restore()
        match.activate()
        return True, f"activated window '{title}'"
    except Exception as exc:  # noqa: BLE001
        return False, f"failed to activate '{title}': {str(exc)[:200]}"


def window_move(win_ref: str, x: int, y: int, width: int = 0, height: int = 0) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    try:
        match = _pick_window(win_ref)
        if match is None:
            return False, f"no visible window matching '{win_ref}'"
        match.moveTo(x, y)
        if width and height:
            match.resizeTo(width, height)
        return True, f"moved window '{win_ref}' to ({x},{y})" + (f" size {width}x{height}" if width and height else "")
    except Exception as exc:  # noqa: BLE001
        return False, f"failed to move window: {str(exc)[:200]}"


def _pick_window(ref: str):
    wins = [_PYGETWINDOW.getWindowsWithTitle(ref)]
    if wins and wins[0]:
        return wins[0][0]
    for title in _PYGETWINDOW.getAllTitles():
        if ref and ref.lower() in title.lower():
            found = _PYGETWINDOW.getWindowsWithTitle(title)
            if found:
                return found[0]
    return None


def launch(name: str) -> Tuple[bool, str]:
    ok, msg = _ensure()
    if not ok:
        return False, msg
    requested = name.strip()
    if not requested:
        return False, "launch needs a name"
    try:
        import os
        try:
            import shutil
            executable = shutil.which(requested)
            if executable:
                import subprocess
                subprocess.Popen([executable], close_fds=True)
                return True, f"launched {requested}"
        except Exception:  # noqa: BLE001
            pass
        os.startfile(requested)
        return True, f"opened {requested} with its default action"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not launch '{requested}': {str(exc)[:200]}"