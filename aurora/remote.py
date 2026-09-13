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
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

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
        voice_url: Optional[Callable[[], str]] = None,
    ) -> None:
        remote = cfg.get("remote", {})
        self.enabled = bool(remote.get("enabled", False))
        self.host = str(remote.get("host", "127.0.0.1"))
        self.port = int(remote.get("port", 8765))
        self.token = os.environ.get(str(remote.get("token_env", "KIM_REMOTE_TOKEN")), "").strip()
        self.pin = os.environ.get(str(remote.get("pin_env", "KIM_REMOTE_PIN")), "").strip()
        self.allowed_tools = set(remote.get("allowed_tools", []))
        configured_direct = os.environ.get("KIM_REMOTE_DIRECT_TOOLS", "")
        self.direct_tools = set(remote.get("direct_tools", [])) | {
            item.strip() for item in configured_direct.split(",") if item.strip()
        }
        self.cors_origins = set(remote.get("cors_origins", []))
        self.registry = registry
        self.loop = loop
        self.control_path = control_path
        self.speak = speak
        self.voice_url = voice_url
        self.audit_path = Path(os.environ.get("KIM_REMOTE_AUDIT_PATH", str(remote.get("audit_path", "~/.aurora/remote-audit.jsonl")))).expanduser()
        self.state_path = Path(os.environ.get("KIM_REMOTE_STATE_PATH", str(remote.get("state_path", "~/.aurora/remote-state.json")))).expanduser()
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.approvals_lock = threading.Lock()
        self.commands: list[Dict[str, Any]] = []
        self.device_commands: list[Dict[str, Any]] = []
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.google_states: Dict[str, float] = {}
        self.commands_lock = threading.Lock()
        self._load_state()
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            log.info("remote API disabled")
            return
        if not self.pin and not self.token:
            raise RuntimeError("remote.enabled is true but KIM_REMOTE_PIN is not set")
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
                pin = self.headers.get("X-Kim-Pin", "").strip()
                if pin and owner.pin:
                    return hmac.compare_digest(pin, owner.pin)
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
                self.send_header("Access-Control-Allow-Headers", "Authorization, X-Kim-Pin, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/":
                    self._dashboard()
                    return
                if self.path == "/healthz":
                    self._reply(200, {"ok": True, "service": "kim"})
                    return
                if self.path.startswith("/v1/google/callback"):
                    self._google_callback()
                    return
                if not owner._check(self):
                    return
                if self.path == "/v1/my-day":
                    sources = [
                        ("email", "gmail_search", {"query": "newer_than:2d", "max_results": 12}),
                        ("calendar", "calendar_upcoming", {"days": 1}),
                        ("WhatsApp", "whatsapp_recent", {"limit": 12}),
                    ]
                    results = []
                    for label, name, parameters in sources:
                        if name not in owner.allowed_tools:
                            results.append({"source": label, "status": "not_enabled", "text": "Connect this source in Kim settings."})
                            continue
                        text, is_error = owner._run(owner.registry.run(name, parameters))
                        results.append({"source": label, "status": "error" if is_error else "ready", "text": text[:12000]})
                    with owner.approvals_lock:
                        pending = [
                            {"summary": str(item.get("summary", item.get("name", "Approval"))), "id": item.get("id")}
                            for item in owner.approvals.values() if item.get("status") == "pending"
                        ]
                    self._reply(200, {
                        "ok": True,
                        "generated_at": time.time(),
                        "results": results,
                        "pending_approvals": pending,
                    })
                    owner._audit("my_day", {"sources": [item["source"] for item in results]}, True)
                    return
                if self.path == "/v1/status":
                    state_path = Path.home() / ".aurora" / "voice_state"
                    try:
                        state = state_path.read_text().strip()
                    except OSError:
                        state = "offline"
                    self._reply(200, {"ok": True, "voice_state": state, "allowed_tools": sorted(owner.allowed_tools), "direct_tools": sorted(owner.direct_tools)})
                    return
                if self.path == "/v1/integrations":
                    google_ready = bool(os.environ.get("NANGO_SECRET_KEY", "").strip() and os.environ.get("NANGO_GMAIL_INTEGRATION_ID", "").strip() and os.environ.get("NANGO_GMAIL_CONNECTION_ID", "").strip()) or bool(os.environ.get("GOOGLE_TOKEN_JSON", "").strip()) or Path(os.environ.get("GOOGLE_TOKEN_PATH", "~/.aurora/google-token.json")).expanduser().exists()
                    whatsapp_ready = bool(os.environ.get("WHATSAPP_CLOUD_API_TOKEN", "").strip() and os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "").strip())
                    self._reply(200, {"ok": True, "integrations": {
                        "gmail": {"cloud": google_ready, "laptop_fallback": True},
                        "calendar": {"cloud": google_ready, "laptop_fallback": True},
                        "whatsapp": {"cloud": whatsapp_ready, "laptop_fallback": True},
                        "laptop_control": {"cloud": False, "laptop_fallback": True},
                        "banking": {"enabled": False},
                    }})
                    return
                if self.path == "/v1/google/start":
                    try:
                        self._reply(200, {"ok": True, "auth_url": owner._google_auth_url(self)})
                    except ValueError as exc:
                        self._reply(400, {"error": str(exc)})
                    return
                if self.path == "/v1/approvals":
                    with owner.approvals_lock:
                        items = [dict(v, parameters=None) for v in owner.approvals.values()]
                    self._reply(200, {"ok": True, "approvals": items})
                    return
                if self.path == "/v1/commands/next":
                    with owner.commands_lock:
                        command = owner.commands.pop(0) if owner.commands else None
                        owner._persist_state()
                    self._reply(200, {"ok": True, "command": command})
                    return
                if self.path.startswith("/v1/device/commands/next"):
                    query = parse_qs(urlparse(self.path).query)
                    device_id = str((query.get("device_id") or [""])[0]).strip()
                    if not device_id:
                        self._reply(400, {"error": "device_id is required"})
                        return
                    with owner.commands_lock:
                        command = next((item for item in owner.device_commands if item.get("device_id") in {None, device_id}), None)
                        if command:
                            owner.device_commands.remove(command)
                            owner._persist_state()
                    self._reply(200, {"ok": True, "command": command})
                    return
                if self.path == "/v1/device/status":
                    with owner.commands_lock:
                        devices = {k: dict(v) for k, v in owner.devices.items()}
                    self._reply(200, {"ok": True, "devices": devices})
                    return
                if self.path == "/v1/voice/session":
                    if owner.voice_url is None:
                        self._reply(503, {"error": "voice service is not configured"})
                        return
                    self._reply(200, {"ok": True, "url": owner.voice_url()})
                    return
                self._reply(404, {"error": "not found"})

            def _dashboard(self) -> None:
                body = DASHBOARD_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _google_callback(self) -> None:
                query = parse_qs(urlparse(self.path).query)
                state = str((query.get("state") or [""])[0])
                code = str((query.get("code") or [""])[0])
                if not owner._consume_google_state(state):
                    self._reply(400, {"error": "invalid or expired Google OAuth state"})
                    return
                if not code:
                    self._reply(400, {"error": "Google authorization was not completed"})
                    return
                try:
                    owner._finish_google_auth(code, self)
                except ValueError as exc:
                    self._reply(400, {"error": str(exc)})
                    return
                self.send_response(302)
                self.send_header("Location", "/?google=connected")
                self.end_headers()

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
                        with owner.commands_lock:
                            owner.commands.append({"type": "control", "action": action})
                            owner._persist_state()
                        owner._audit("control", {"action": action}, True)
                        self._reply(200, {"ok": True, "action": action})
                        return
                    if self.path == "/v1/device/heartbeat":
                        device_id = str(data.get("device_id", "")).strip()
                        if not device_id or len(device_id) > 100:
                            raise ValueError("device_id is required")
                        with owner.commands_lock:
                            owner.devices[device_id] = {"device_id": device_id, "last_seen": time.time(), "capabilities": data.get("capabilities", [])}
                            owner._persist_state()
                        self._reply(200, {"ok": True, "device_id": device_id})
                        return
                    if self.path == "/v1/device/command":
                        action = str(data.get("action", "")).strip().lower()
                        allowed = {"open_url", "open_app", "media", "volume", "flashlight", "notify"}
                        if action not in allowed:
                            raise ValueError(f"action must be one of {sorted(allowed)}")
                        command = {"id": uuid.uuid4().hex, "type": "device", "device_id": str(data.get("device_id", "")).strip() or None, "action": action, "parameters": data.get("parameters", {}), "created_at": time.time()}
                        with owner.commands_lock:
                            owner.device_commands.append(command)
                            owner._persist_state()
                        owner._audit("device_command_queued", {"id": command["id"], "action": action, "device_id": command["device_id"]}, True)
                        self._reply(202, {"ok": True, "command_id": command["id"]})
                        return
                    if self.path.startswith("/v1/device/commands/") and self.path.endswith("/result"):
                        command_id = self.path.removeprefix("/v1/device/commands/").removesuffix("/result").strip("/")
                        owner._audit("device_command_result", {"id": command_id, "action": str(data.get("action", ""))[:80]}, not bool(data.get("is_error")))
                        self._reply(200, {"ok": True})
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
                    if self.path == "/v1/approvals":
                        name = str(data.get("name", ""))
                        if name not in owner.registry.names():
                            raise ValueError("unknown tool")
                        approval_id = uuid.uuid4().hex
                        record = {"id": approval_id, "name": name, "parameters": data.get("parameters", {}), "summary": str(data.get("summary", name))[:500], "status": "pending", "created_at": time.time()}
                        with owner.approvals_lock:
                            owner.approvals[approval_id] = record
                            owner._persist_state()
                        owner._audit("approval_requested", {"id": approval_id, "name": name}, True)
                        self._reply(202, {"ok": True, "approval": {k: v for k, v in record.items() if k != "parameters"}})
                        return
                    if self.path.startswith("/v1/approvals/"):
                        approval_id, action = self.path.removeprefix("/v1/approvals/").split("/", 1)
                        if action not in {"approve", "reject"}:
                            self._reply(400, {"error": "action must be approve or reject"})
                            return
                        with owner.approvals_lock:
                            record = owner.approvals.get(approval_id)
                            if not record:
                                self._reply(404, {"error": "approval not found"})
                                return
                            if record["status"] != "pending":
                                self._reply(409, {"error": "approval already resolved"})
                                return
                            record["status"] = "approved" if action == "approve" else "rejected"
                            owner._persist_state()
                        if action == "approve":
                            if record["name"] in owner.direct_tools and owner._can_execute_direct(record["name"]):
                                result, is_error = owner._run(owner.registry.run(record["name"], record["parameters"]))
                                with owner.approvals_lock:
                                    record["status"] = "failed" if is_error else "completed"
                                    record["result"] = result[:20_000]
                                    owner._persist_state()
                                owner._audit("approval_result", {"id": approval_id, "name": record["name"], "error": is_error, "execution": "cloud"}, not is_error)
                                self._reply(200, {"ok": not is_error, "status": record["status"], "approval_id": approval_id, "result": result})
                            else:
                                with owner.commands_lock:
                                    owner.commands.append({"type": "tool", "approval_id": approval_id, "name": record["name"], "parameters": record["parameters"]})
                                    owner._persist_state()
                                owner._audit("approval_queued", {"id": approval_id, "name": record["name"]}, True)
                                self._reply(202, {"ok": True, "status": "approved_queued", "approval_id": approval_id})
                        elif action == "reject":
                            owner._audit("approval_rejected", {"id": approval_id, "name": record["name"]}, True)
                            self._reply(200, {"ok": True, "status": "rejected"})
                        return
                    if self.path.startswith("/v1/commands/") and self.path.endswith("/result"):
                        approval_id = self.path.removeprefix("/v1/commands/").removesuffix("/result").strip("/")
                        with owner.approvals_lock:
                            record = owner.approvals.get(approval_id)
                            if not record:
                                self._reply(404, {"error": "approval not found"})
                                return
                            record["status"] = "failed" if data.get("is_error") else "completed"
                            record["result"] = str(data.get("result", ""))[:20_000]
                            owner._persist_state()
                        owner._audit("approval_result", {"id": approval_id, "name": record["name"], "error": bool(data.get("is_error"))}, not data.get("is_error"))
                        self._reply(200, {"ok": True, "status": record["status"]})
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
        if not self.pin and not self.token:
            handler._reply(503, {"error": "remote PIN is not configured"})  # type: ignore[attr-defined]
            return False
        if not handler._auth():  # type: ignore[attr-defined]
            handler._reply(401, {"error": "missing or invalid PIN"})  # type: ignore[attr-defined]
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

    def _load_state(self) -> None:
        """Restore queued approvals/commands after a process restart."""
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            approvals = state.get("approvals", {})
            commands = state.get("commands", [])
            device_commands = state.get("device_commands", [])
            devices = state.get("devices", {})
            if isinstance(approvals, dict):
                self.approvals = {str(k): v for k, v in approvals.items() if isinstance(v, dict)}
            if isinstance(commands, list):
                self.commands = [v for v in commands if isinstance(v, dict)]
            if isinstance(device_commands, list):
                self.device_commands = [v for v in device_commands if isinstance(v, dict)]
            if isinstance(devices, dict):
                self.devices = {str(k): v for k, v in devices.items() if isinstance(v, dict)}
            states = state.get("google_states", {})
            if isinstance(states, dict):
                self.google_states = {str(k): float(v) for k, v in states.items() if time.time() - float(v) < 600}
        except (OSError, json.JSONDecodeError):
            return

    def _google_redirect_uri(self, handler: BaseHTTPRequestHandler) -> str:
        configured = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
        if configured:
            return configured.rstrip("/")
        proto = handler.headers.get("X-Forwarded-Proto", "https").split(",")[0].strip()
        host = handler.headers.get("Host", "").split(",")[0].strip()
        if not host:
            raise ValueError("GOOGLE_REDIRECT_URI is required")
        return f"{proto}://{host}/v1/google/callback"

    @staticmethod
    def _google_scopes() -> list[str]:
        return [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/calendar",
        ]

    def _google_auth_url(self, handler: BaseHTTPRequestHandler) -> str:
        client_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
        if not client_json:
            raise ValueError("add GOOGLE_CREDENTIALS_JSON to Coolify first")
        try:
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_config(json.loads(client_json), scopes=self._google_scopes())
        except (ImportError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Google OAuth client JSON is invalid: {exc}") from exc
        flow.redirect_uri = self._google_redirect_uri(handler)
        state = secrets.token_urlsafe(32)
        with self.approvals_lock:
            self.google_states[state] = time.time()
            self._persist_state()
        return flow.authorization_url(access_type="offline", prompt="consent", state=state, include_granted_scopes="true")[0]

    def _consume_google_state(self, state: str) -> bool:
        with self.approvals_lock:
            created = self.google_states.pop(state, 0)
            self._persist_state()
        return bool(created and time.time() - created < 600)

    def _finish_google_auth(self, code: str, handler: BaseHTTPRequestHandler) -> None:
        client_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
        try:
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_config(json.loads(client_json), scopes=self._google_scopes())
            flow.redirect_uri = self._google_redirect_uri(handler)
            flow.fetch_token(code=code)
            token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", str(self.state_path.with_name("google-token.json")))).expanduser()
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(flow.credentials.to_json(), encoding="utf-8")
            token_path.chmod(0o600)
        except Exception as exc:  # noqa: BLE001
            log.exception("Google OAuth callback failed")
            raise ValueError(f"Google authorization failed: {exc}") from exc

    def _can_execute_direct(self, name: str) -> bool:
        """Only use cloud execution when that connector's secret is present."""
        if name in {"gmail_send", "gmail_search", "gmail_read", "gmail_today", "gmail_unanswered", "gmail_contacts"}:
            return (bool(os.environ.get("NANGO_SECRET_KEY", "").strip()) and bool(os.environ.get("NANGO_GMAIL_INTEGRATION_ID", "").strip()) and bool(os.environ.get("NANGO_GMAIL_CONNECTION_ID", "").strip())) or bool(os.environ.get("GOOGLE_TOKEN_JSON", "").strip()) or Path(os.environ.get("GOOGLE_TOKEN_PATH", "~/.aurora/google-token.json")).expanduser().exists()
        if name in {"calendar_create", "calendar_upcoming"}:
            return (bool(os.environ.get("NANGO_SECRET_KEY", "").strip()) and bool(os.environ.get("NANGO_CALENDAR_INTEGRATION_ID", "").strip()) and bool(os.environ.get("NANGO_CALENDAR_CONNECTION_ID", "").strip())) or bool(os.environ.get("GOOGLE_TOKEN_JSON", "").strip()) or Path(os.environ.get("GOOGLE_TOKEN_PATH", "~/.aurora/google-token.json")).expanduser().exists()
        if name == "whatsapp_send":
            return bool(os.environ.get("WHATSAPP_CLOUD_API_TOKEN", "").strip() and os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "").strip())
        return True

    def _persist_state(self) -> None:
        """Atomically persist queue state without putting it in the audit log."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps({"approvals": self.approvals, "commands": self.commands, "device_commands": self.device_commands, "devices": self.devices, "google_states": self.google_states}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.state_path)
            try:
                self.state_path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            log.warning("could not persist remote queue state", exc_info=True)

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


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kim</title>
<style>
:root{--ink:#17172b;--muted:#77768b;--lav:#7669f5;--cream:#fffdf5}*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#f5fbfa,#fff9ee 58%,#f1efff);color:var(--ink);font:15px system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:560px;margin:auto;padding:32px 20px 24px;min-height:100vh}header{text-align:center}h1{font-size:30px;margin:0;letter-spacing:-.06em}header p{margin:4px 0;color:var(--muted)}.orb{width:156px;height:156px;margin:18px auto 8px;border-radius:48%;background:radial-gradient(circle at 35% 25%,#bdfcff,transparent 30%),linear-gradient(145deg,#09dfe9,#405cff 58%,#c557ff);box-shadow:0 12px 42px #6c9cff88;position:relative}.orb:after{content:"";position:absolute;inset:34px 25px;border-radius:42%;background:#121832;box-shadow:inset 0 0 0 2px #3a65aa}.orb:before{content:"•  •";position:absolute;z-index:1;left:48px;top:60px;color:#83f5ff;font-size:27px;letter-spacing:10px}h2{font-size:34px;line-height:1.05;text-align:center;margin:8px 0 18px;letter-spacing:-.06em}.composer{background:#fffdfb;border:1px solid #ebe6dc;border-radius:24px;padding:8px;display:flex;box-shadow:0 8px 24px #8b8b9b18}.composer input{border:0;background:transparent;outline:0;padding:12px 14px;flex:1;font:inherit;color:var(--ink)}button{font:inherit;border:0;border-radius:16px;padding:12px 16px;background:#17172b;color:white;font-weight:650;cursor:pointer}button:hover{opacity:.85}.mic{border-radius:50%;width:48px;padding:0;background:var(--lav);font-size:20px}.label{color:var(--muted);text-align:center;margin:12px 0 7px}.chips{display:flex;gap:8px;justify-content:center;flex-wrap:wrap}.chip{background:#ffffffbb;color:var(--ink);border:1px solid #e7e3dc;border-radius:20px;padding:10px 14px;font-weight:500}.section{margin-top:22px}.section-title{font-size:20px;font-weight:700;margin-bottom:8px}.requests{background:#ffffff99;border:1px solid #ebe6dc;border-radius:20px;padding:14px}.request{padding:10px 0;border-bottom:1px solid #eee9df}.request:last-child{border-bottom:0}.request small{color:var(--muted)}.actions{display:flex;gap:8px;margin-top:8px}.actions button{flex:1}.secondary{background:#fff;color:var(--ink);border:1px solid #ddd8ce}.bottom{display:flex;justify-content:space-around;margin-top:24px;color:#9895ad;font-size:25px}.setup{background:#fffdfb;border:1px solid #ebe6dc;border-radius:20px;padding:14px;margin-top:18px}.setup input{width:100%;padding:12px;border:1px solid #ddd8ce;border-radius:12px;font:inherit;margin:8px 0}.error{color:var(--muted);text-align:center;min-height:22px}
</style>
<body><main><header><h1>Kim</h1><p>Your personal AI companion</p><div class="orb"></div></header><h2>How can I<br>help you today?</h2>
<div class="composer"><input id="ask" placeholder="Ask anything…"><button class="mic" onclick="talk()">⌁</button></div><div id="message" class="error"></div>
<div class="label">Try asking</div><div class="chips"><button class="chip" onclick="myDay()">My day</button><button class="chip" onclick="quick('calendar_upcoming','Checking your calendar…')">Calendar</button><button class="chip" onclick="email()">Send an email</button></div>
<div id="setup" class="setup" hidden><b>Connect Kim to this phone</b><input id="pin" type="password" inputmode="numeric" maxlength="6" placeholder="Private PIN"><button onclick="save()">Connect securely</button></div>
<div class="section"><div class="section-title">Kim’s briefing</div><div id="briefing" class="requests">Ask Kim to prepare your day.</div></div><div class="section"><div class="section-title">Requests</div><div id="approvals" class="requests">No requests waiting</div></div><div class="bottom"><span>⌂</span><span>✦</span><span>◷</span><span onclick="settings()">⚙</span></div>
<dialog id="emailDialog"><form method="dialog" class="setup"><h3>Prepare an email</h3><input id="emailTo" placeholder="To" type="email" required><input id="emailSubject" placeholder="Subject" required><textarea id="emailBody" placeholder="Message" rows="5" required></textarea><div class="actions"><button value="cancel" class="secondary">Cancel</button><button value="send" onclick="submitEmail(event)">Ask Kim to send</button></div></form></dialog>
<script>
const key='kim-pin';const pin=document.querySelector('#pin');pin.value=localStorage.getItem(key)||'';if(!pin.value)document.querySelector('#setup').hidden=false;
async function call(path,opts={}){opts.headers=Object.assign({'X-Kim-Pin':pin.value,'Content-Type':'application/json'},opts.headers||{});const r=await fetch(path,opts);const j=await r.json();if(!r.ok)throw Error(j.error||'Connection failed');return j}
async function save(){try{await call('/v1/status');localStorage.setItem(key,pin.value);document.querySelector('#setup').hidden=true;document.querySelector('#message').textContent='Connected to Kim'}catch(e){document.querySelector('#message').textContent='Could not connect — check your PIN'}}
function present(value){try{const data=typeof value==='string'?JSON.parse(value):value;if(Array.isArray(data.items)){if(!data.items.length)return'No upcoming events';return data.items.map(event=>{const start=event.start?.dateTime||event.start?.date||'';return '• '+(start?new Date(start).toLocaleString([], {weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'All day')+' — '+(event.summary||'(untitled)')}).join('\n')}if(Array.isArray(data.messages)){return data.messages.length+' email message(s) found'}return JSON.stringify(data,null,2)}catch(_){return String(value||'Done')}}\n+async function quick(name,label){document.querySelector('#message').textContent=label;try{const j=await call('/v1/tool',{method:'POST',body:JSON.stringify({name,parameters:{}})});document.querySelector('#message').textContent=present(j.result)}catch(e){document.querySelector('#message').textContent=e.message}}
async function myDay(){const box=document.querySelector('#briefing');box.textContent='Kim is reviewing your day…';document.querySelector('#message').textContent='';try{const j=await call('/v1/my-day');box.innerHTML='';for(const item of j.results){const section=document.createElement('div');section.className='request';const title=document.createElement('b');title.textContent=item.source+(item.status==='ready'?'':' · '+item.status.replace('_',' '));const pre=document.createElement('div');pre.style='white-space:pre-wrap;margin-top:6px;font-size:13px';pre.textContent=item.text;section.append(title,pre);box.appendChild(section)}if(j.pending_approvals.length){const p=document.createElement('div');p.style='margin-top:10px';p.textContent=j.pending_approvals.length+' action approval(s) waiting';box.appendChild(p)}document.querySelector('#message').textContent='Kim prepared your briefing'}catch(e){box.textContent='Kim could not prepare the briefing';document.querySelector('#message').textContent=e.message}}
function talk(){document.querySelector('#message').textContent='Tap the microphone in the Kim app to start a voice session.'}
function email(){document.querySelector('#emailDialog').showModal()}
async function submitEmail(event){event.preventDefault();try{await call('/v1/approvals',{method:'POST',body:JSON.stringify({name:'gmail_send',parameters:{to:document.querySelector('#emailTo').value,subject:document.querySelector('#emailSubject').value,body:document.querySelector('#emailBody').value},summary:'Send email to '+document.querySelector('#emailTo').value})});document.querySelector('#emailDialog').close();document.querySelector('#message').textContent='Email request created — review it below';approvals()}catch(e){document.querySelector('#message').textContent=e.message}}
function settings(){document.querySelector('#setup').hidden=false;pin.focus()}
async function approvals(){try{const data=await call('/v1/approvals');const box=document.querySelector('#approvals');box.innerHTML='';if(!data.approvals.length){box.textContent='No requests waiting';return}for(const a of data.approvals){const row=document.createElement('div');row.className='request';row.innerHTML='<b>'+a.summary+'</b><br><small>'+a.status+'</small>';if(a.status==='pending'){const actions=document.createElement('div');actions.className='actions';for(const action of ['approve','reject']){const b=document.createElement('button');b.textContent=action==='approve'?'Approve':'Not now';b.className=action==='reject'?'secondary':'';b.onclick=async()=>{await call('/v1/approvals/'+a.id+'/'+action,{method:'POST',body:'{}'});approvals()};actions.appendChild(b)}row.appendChild(actions)}box.appendChild(row)}}catch(e){}}
if(pin.value)approvals();
</script></body></html>"""
