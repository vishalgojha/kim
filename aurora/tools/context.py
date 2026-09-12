import asyncio
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


class Notifier:
    """Bridge between tools, watcher, and the voice/TTS layer."""

    def __init__(self) -> None:
        self._speak_coro: Callable[[str], Any] | None = None
        self.notify: Callable[[str, str], Any] | None = None
        self.scheduled: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._seq = 0

    async def speak(self, text: str) -> None:
        fn = self._speak_coro
        if fn:
            try:
                await fn(text)
            except Exception:
                pass


CTX: Dict[str, Any] = {}


def get_ctx() -> Dict[str, Any]:
    return CTX


def set_speaker(coro: Callable[[str], Any]) -> None:
    n = get_ctx().get("notifier")
    if n:
        n._speak_coro = coro


def schedule_id(kind: str) -> str:
    with get_ctx()["notifier"]._lock:
        get_ctx()["notifier"]._seq += 1
        return f"{kind}_{get_ctx()['notifier']._seq}"


def speak(text: str) -> None:
    n = get_ctx().get("notifier")
    if n:
        n.speak(text)


def notify(title: str, body: str) -> None:
    n = get_ctx().get("notifier")
    if n and n.notify:
        n.notify(title, body)