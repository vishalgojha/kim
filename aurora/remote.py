"""Authenticated remote control plane for Kim.

The API is deliberately opt-in and binds to localhost by default. Put it behind
an authenticated private tunnel/reverse proxy before exposing it on the public
internet.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from .tools.registry import ToolRegistry

log = logging.getLogger("aurora.remote")


class RemoteServer:
    def __init__(
        self,
        cfg: Dict[str, Any],
        registry: ToolRegistry,
        loop: asyncio.AbstractEventLoop,
        control_path: Path,
        speak: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        remote = cfg.get("remote", {})
        self.enabled = bool(remote.get("enabled", False))
        self.host = str(remote.get("host", "127.0.0.1"))
        self.port = int(remote.get("port", 8765))
        self.token = os.environ.get(str(remote.get("token_env", "KIM_REMOTE_TOKEN")), "").strip()
        self.allowed_tools = set(remote.get("allowed_tools", []))
        self.cors_origins = set(remote.get("cors_origins", []))
        self.registry = registry
        self.loop = loop
        self.control_path = control_path
        self.speak = speak
        self.audit_path = Path(str(remote.get("audit_path", "~/.aurora/remote-audit.jsonl"))).expanduser()
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            log.info("remote API disabled")
            return
        if not self.token:
            raise RuntimeError("remote.enabled is true but KIM_REMOTE_TOKEN is not set")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "KimRemote/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                log.info("remote %s - %s", self.address_string(), fmt % args)

            def _reply(self, status: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                origin = self.headers.get("Origin", "")
                if origin in owner.cors_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(body)

            def _auth(self) -> bool:
                supplied = self.headers.get("Authorization", "")
                token = supplied.removeprefix("Bearer ").strip()
                return bool(token) and hmac.compare_digest(token, owner.token)

            def _json(self) -> Dict[str, Any]:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 256_000:
                    raise ValueError("request too large")
                return json.loads(self.rfile.read(size) or b"{}")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                origin = self.headers.get("Origin", "")
                if origin in owner.cors_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/healthz":
                    self._reply(200, {"ok": True, "service": "kim"})
                    return
                if not owner._check(self):
                    return
                if self.path == "/v1/status":
                    state_path = Path.home() / ".aurora" / "voice_state"
                    try:
                        state = state_path.read_text().strip()
                    except OSError:
                        state = "offline"
                    self._reply(200, {"ok": True, "voice_state": state, "allowed_tools": sorted(owner.allowed_tools)})
                    return
                self._reply(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if not owner._check(self):
                    return
                try:
                    data = self._json()
                    if self.path == "/v1/control":
                        action = str(data.get("action", "")).lower()
                        if action not in {"pause", "wake"}:
                            raise ValueError("action must be pause or wake")
                        owner.control_path.parent.mkdir(parents=True, exist_ok=True)
                        owner.control_path.write_text(action)
                        owner._audit("control", {"action": action}, True)
                        self._reply(200, {"ok": True, "action": action})
                        return
                    if self.path == "/v1/say":
                        text = str(data.get("text", "")).strip()
                        if not text or len(text) > 2_000:
                            raise ValueError("text must be 1-2000 characters")
                        if owner.speak is None:
                            raise ValueError("speech is unavailable")
                        owner._run(owner.speak(text))
                        owner._audit("say", {"chars": len(text)}, True)
                        self._reply(200, {"ok": True})
                        return
                    if self.path == "/v1/tool":
                        name = str(data.get("name", ""))
                        if name not in owner.allowed_tools:
                            self._reply(403, {"error": "tool is not enabled for remote use"})
                            return
                        result, is_error = owner._run(owner.registry.run(name, data.get("parameters", {})))
                        owner._audit("tool", {"name": name, "error": is_error}, not is_error)
                        self._reply(500 if is_error else 200, {"ok": not is_error, "result": result})
                        return
                    self._reply(404, {"error": "not found"})
                except (ValueError, json.JSONDecodeError) as exc:
                    self._reply(400, {"error": str(exc)})
                except Exception:
                    log.exception("remote request failed")
                    self._reply(500, {"error": "internal error"})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="kim-remote", daemon=True)
        self.thread.start()
        log.info("remote API listening on %s:%s", self.host, self.port)

    def _check(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.token:
            handler._reply(503, {"error": "remote token is not configured"})  # type: ignore[attr-defined]
            return False
        if not handler._auth():  # type: ignore[attr-defined]
            handler._reply(401, {"error": "missing or invalid bearer token"})  # type: ignore[attr-defined]
            return False
        return True

    def _audit(self, action: str, details: Dict[str, Any], ok: bool) -> None:
        """Append a small local audit record; never persist bearer tokens or payloads."""
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.time(), "action": action, "details": details, "ok": ok}) + "\n")
        except OSError:
            log.warning("could not write remote audit record", exc_info=True)

    def _run(self, awaitable: Awaitable[Any]) -> Any:
        future = asyncio.run_coroutine_threadsafe(awaitable, self.loop)
        return future.result(timeout=120)

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None


def generate_token() -> str:
    """Generate a token for first-time setup without persisting it in source/config."""
    return secrets.token_urlsafe(32)
