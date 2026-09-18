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

from .agent import AgentCore
from .eleven_chat import ElevenTextChat
from .tools.context import get_ctx
from .tools.music import get_music_job
from .tools.registry import ToolRegistry

log = logging.getLogger("aurora.remote")

WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
_WEB_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".map": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


class RemoteServer:
    def __init__(
        self,
        cfg: Dict[str, Any],
        registry: ToolRegistry,
        loop: asyncio.AbstractEventLoop,
        control_path: Path,
        speak: Optional[Callable[[str], Awaitable[None]]] = None,
        voice_url: Optional[Callable[[], str]] = None,
        text_chat: Optional[ElevenTextChat] = None,
        hosted: bool = False,
        prefer_agent: bool = False,
    ) -> None:
        remote = cfg.get("remote", {})
        self.hosted = hosted
        self.enabled = bool(remote.get("enabled", False))
        self.host = str(remote.get("host", "127.0.0.1"))
        self.port = int(remote.get("port", 8765))
        self.token = os.environ.get(str(remote.get("token_env", "KIM_REMOTE_TOKEN")), "").strip()
        self.allowed_tools = set(remote.get("allowed_tools") or registry.names())
        configured_direct = os.environ.get("KIM_REMOTE_DIRECT_TOOLS", "")
        self.direct_tools = set(remote.get("direct_tools", [])) | {
            item.strip() for item in configured_direct.split(",") if item.strip()
        }
        if "*" in self.allowed_tools:
            self.allowed_tools = set(registry.names())
        self.cors_origins = set(remote.get("cors_origins", []))
        self.trusted_desktop_origins = set(remote.get("trusted_desktop_origins", ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"]))
        self.registry = registry
        self.loop = loop
        self.control_path = control_path
        self.speak = speak
        self.voice_url = voice_url
        self.text_chat = text_chat
        self.last_text_chat_error = ""
        self.prefer_agent = prefer_agent
        self.last_agent_error = ""
        self.audit_path = Path(os.environ.get("KIM_REMOTE_AUDIT_PATH", str(remote.get("audit_path", "~/.aurora/remote-audit.jsonl")))).expanduser()
        self.state_path = Path(os.environ.get("KIM_REMOTE_STATE_PATH", str(remote.get("state_path", "~/.aurora/remote-state.json")))).expanduser()
        self.remote_domain = str(remote.get("domain", "")).strip().rstrip("/")
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.approvals_lock = threading.Lock()
        self.commands: list[Dict[str, Any]] = []
        self.device_commands: list[Dict[str, Any]] = []
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.device_waiters: Dict[str, asyncio.Future[str]] = {}
        self.google_states: Dict[str, float] = {}
        self.propai_oauth: Dict[str, Any] = {}
        self.agent = AgentCore(registry, hosted=hosted)
        self.research_jobs: Dict[str, Dict[str, Any]] = {}
        self.research_lock = threading.Lock()
        self.commands_lock = threading.Lock()
        self._load_state()
        get_ctx()["queue_device_command"] = self.queue_device_command
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            log.info("remote API disabled")
            return
        if not self.token:
            log.warning("remote API enabled without a token — auth is unconditional")
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
                # All callers are authenticated — the PIN requirement is removed.
                return True

            def _json(self) -> Dict[str, Any]:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 2_500_000:
                    raise ValueError("request too large")
                return json.loads(self.rfile.read(size) or b"{}")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                origin = self.headers.get("Origin", "")
                if origin in owner.cors_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Headers", "Authorization, X-Kim-Pin, X-Kim-Client, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                if self.path in ("/", "/index.html"):
                    self._serve_web("index.html")
                    return
                if self.path.startswith("/assets/"):
                    self._serve_web(self.path.lstrip("/"))
                    return
                if self.path == "/healthz":
                    self._reply(200, {"ok": True, "service": "kim"})
                    return
                if self.path.startswith("/v1/google/callback"):
                    self._google_callback()
                    return
                if self.path.startswith("/v1/propai/callback"):
                    self._propai_callback()
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
                    self._reply(200, {"ok": True, "voice_state": state, "agent": owner.agent.status(), "text_brain": {"prefer_agent": owner.prefer_agent, "configured": owner.agent.configured, "last_agent_error": owner.last_agent_error[:300]}, "elevenlabs": {"text_chat_initialized": owner.text_chat is not None, "agent_id_configured": bool(getattr(owner.text_chat, "agent_id", "")), "last_error": owner.last_text_chat_error}, "allowed_tools": sorted(owner.allowed_tools), "direct_tools": sorted(owner.direct_tools)})
                    return
                if self.path.startswith("/v1/music/"):
                    job_id = self.path.removeprefix("/v1/music/").strip("/")
                    if job_id.endswith("/download"):
                        job_id = job_id.removesuffix("/download").strip("/")
                        job = get_music_job(job_id)
                        path = Path(str((job or {}).get("path", ""))) if job else None
                        if not job or job.get("status") != "completed" or not path or not path.is_file():
                            self._reply(404, {"error": "music is not ready"})
                            return
                        body = path.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "audio/mpeg")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    job = get_music_job(job_id)
                    if not job:
                        self._reply(404, {"error": "unknown music job"})
                        return
                    payload = {"ok": True, "job": job}
                    if job.get("status") == "completed": payload["download_url"] = f"/v1/music/{job_id}/download"
                    self._reply(200, payload)
                    return
                if self.path == "/v1/knowledge/sources":
                    result, is_error = owner._run(owner.registry.run("knowledge_sources", {}))
                    self._reply(500 if is_error else 200, {"ok": not is_error, "sources": result})
                    return
                if self.path.startswith("/v1/research/"):
                    job_id = self.path.removeprefix("/v1/research/").strip("/")
                    with owner.research_lock:
                        job = dict(owner.research_jobs.get(job_id, {}))
                    self._reply(404 if not job else 200, {"error": "research job not found"} if not job else {"ok": True, "job": job})
                    return
                if self.path == "/v1/research":
                    with owner.research_lock:
                        jobs = [dict(item) for item in owner.research_jobs.values()]
                    self._reply(200, {"ok": True, "jobs": jobs[-20:]})
                    return
                if self.path == "/v1/integrations":
                    google_ready = bool(os.environ.get("NANGO_SECRET_KEY", "").strip() and os.environ.get("NANGO_GMAIL_INTEGRATION_ID", "").strip() and os.environ.get("NANGO_GMAIL_CONNECTION_ID", "").strip()) or bool(os.environ.get("GOOGLE_TOKEN_JSON", "").strip()) or Path(os.environ.get("GOOGLE_TOKEN_PATH", "~/.aurora/google-token.json")).expanduser().exists()
                    whatsapp_ready = bool(os.environ.get("WHATSAPP_CLOUD_API_TOKEN", "").strip() and os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "").strip())
                    propai_ready = not owner._propai_disconnected_path().exists() and (bool(os.environ.get("PROPAI_MCP_TOKEN", "").strip()) or owner._propai_token_path().exists())
                    self._reply(200, {"ok": True, "integrations": {
                        "gmail": {"cloud": google_ready, "laptop_fallback": True},
                        "calendar": {"cloud": google_ready, "laptop_fallback": True},
                        "whatsapp": {"cloud": whatsapp_ready, "laptop_fallback": True},
                        "laptop_control": {"cloud": False, "laptop_fallback": True},
                        "propai_mcp": {"connected": propai_ready, "auth": "Supabase-authenticated MCP token required", "endpoint": os.environ.get("PROPAI_MCP_URL", "https://mcp.propai.live/mcp")},
                        "banking": {"enabled": False},
                    }})
                    return
                if self.path == "/v1/propai/start":
                    try:
                        self._reply(200, {"ok": True, "auth_url": owner._propai_auth_url()})
                    except ValueError as exc:
                        self._reply(400, {"error": str(exc)})
                    return

                if self.path == "/v1/google/start":
                    try:
                        self._reply(200, {"ok": True, "auth_url": owner._google_auth_url(self)})
                    except ValueError as exc:
                        self._reply(400, {"error": str(exc)})
                    return
                if self.path == "/v1/approvals":
                    user = self.headers.get("X-Kim-User", "").strip()
                    with owner.approvals_lock:
                        items = [dict(v, parameters=None) for v in owner.approvals.values() if not user or v.get("user", "default") in {user, "default"}]
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

            def _serve_web(self, rel: str) -> None:
                root = os.path.realpath(WEB_ROOT)
                target = os.path.realpath(os.path.join(root, rel))
                if not target.startswith(root + os.sep) or not os.path.isfile(target):
                    self.send_error(404)
                    return
                try:
                    body = Path(target).read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", _WEB_MIME.get(Path(target).suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store" if rel == "index.html" else "no-cache")
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

            def _propai_callback(self) -> None:
                query = parse_qs(urlparse(self.path).query)
                state = str((query.get("state") or [""])[0])
                code = str((query.get("code") or [""])[0])
                if not owner._consume_propai_state(state):
                    self._propai_oauth_page(
                        "PropAI authorization expired",
                        "This sign-in attempt is no longer valid. Return to Kim and start a fresh connection.",
                        error=True,
                    )
                    return
                if not code:
                    self._propai_oauth_page(
                        "PropAI authorization cancelled",
                        "No authorization code was returned. Return to Kim and try again.",
                        error=True,
                    )
                    return
                try:
                    owner._finish_propai_auth(code, state)
                except ValueError as exc:
                    self._propai_oauth_page("PropAI connection failed", str(exc), error=True)
                    return
                self._propai_oauth_page("PropAI connected", "Authorization complete. You can return to Kim.")

            def _propai_oauth_page(self, title: str, message: str, error: bool = False) -> None:
                # This page is deliberately self-contained: it must remain usable
                # even when the OAuth flow was opened in a separate WebView.
                colour = "#f07b86" if error else "#63e6a1"
                start_url = "/v1/propai/start"
                body = f"""<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{title}</title><body style='font-family:system-ui,sans-serif;background:#08090b;color:#f5f7fb;padding:48px;max-width:680px;margin:auto'>
<h2 style='color:{colour}'>{title}</h2><p style='line-height:1.6'>{message}</p>
<p style='display:flex;gap:12px;flex-wrap:wrap'>
<button onclick='window.close()' style='padding:12px 18px;border:0;border-radius:8px;background:#252832;color:white;cursor:pointer'>Close this window</button>
{"<a href='" + start_url + "' style='display:inline-block;padding:12px 18px;border-radius:8px;background:#36df91;color:#06130b;text-decoration:none'>Start PropAI again</a>" if error else ""}
</p></body>""".encode()
                self.send_response(200 if not error else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if not owner._check(self):
                    return
                try:
                    data = self._json()
                    if self.path == "/v1/propai/disconnect":
                        owner._disconnect_propai()
                        self._reply(200, {"ok": True, "connected": False})
                        return
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
                    if self.path == "/v1/device/connect":
                        device_id = str(data.get("device_id", "laptop")).strip() or "laptop"
                        if not device_id or len(device_id) > 100:
                            raise ValueError("device_id is required")
                        capabilities = data.get("capabilities", [])
                        with owner.commands_lock:
                            desktop = owner.devices.get(device_id, {})
                            online = bool(desktop and time.time() - float(desktop.get("last_seen", 0)) < 15)
                        owner._audit("device_connect_requested", {"device_id": device_id, "capabilities": capabilities}, online)
                        self._reply(200, {"ok": True, "device_id": device_id, "connected": online, "message": "desktop relay is online" if online else "start the Kim desktop relay first"})
                        return
                    if self.path == "/v1/device/command":
                        action = str(data.get("action", "")).strip().lower()
                        allowed = {
                            "open_url", "open_app", "media", "volume", "flashlight", "notify",
                            "type_text", "press_key", "screenshot", "playwright_run", "browser_action", "computer_action",
                        }
                        if action not in allowed:
                            raise ValueError(f"action must be one of {sorted(allowed)}")
                        target = str(data.get("device_id", "")).strip() or "laptop"
                        params = dict(data.get("parameters") or {})
                        for key in ("name", "app_name", "package", "url"):
                            if key in data and key not in params:
                                params[key] = data[key]
                        result = owner._run(owner.queue_device_command(action, target, params))
                        self._reply(200, {"ok": True, "result": result})
                        return
                    if self.path.startswith("/v1/device/commands/") and self.path.endswith("/result"):
                        command_id = self.path.removeprefix("/v1/device/commands/").removesuffix("/result").strip("/")
                        result = str(data.get("result", ""))[:20_000]
                        failed = bool(data.get("is_error"))
                        waiter = owner.device_waiters.pop(command_id, None)
                        if waiter is not None and not waiter.done():
                            owner.loop.call_soon_threadsafe(waiter.set_result, (f"device error: {result}" if failed else result))
                        owner._audit("device_command_result", {"id": command_id, "action": str(data.get("action", ""))[:80]}, not failed)
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
                    if self.path == "/v1/chat":
                        message = str(data.get("message", ""))
                        user = self.headers.get("X-Kim-User", "").strip() or "default"
                        session_id = f"{user}:{data.get('conversation_id', 'web')}"
                        device_context = str(data.get("device_context", "web browser"))
                        attachments = data.get("attachments") or []
                        if isinstance(attachments, list):
                            for attachment in attachments[:3]:
                                if not isinstance(attachment, dict):
                                    continue
                                name = str(attachment.get("name", "attachment"))[:160]
                                content = str(attachment.get("text", ""))[:120_000]
                                if content:
                                    message += f"\n\n[Attached file: {name}]\n{content}\n[/Attached file]"
                        if owner.prefer_agent:
                            try:
                                result = owner._run(owner.agent.chat(session_id, message, owner.create_approval))
                            except Exception as exc:  # noqa: BLE001
                                owner.last_agent_error = str(exc)[:500]
                                log.warning("Kim agent chat failed; falling back to ElevenLabs text chat: %s", exc)
                                if owner.text_chat is None:
                                    raise
                                result = owner._run(owner.text_chat.chat(session_id, message, owner.create_approval, device_context))
                        elif owner.text_chat is not None:
                            try:
                                result = owner._run(owner.text_chat.chat(session_id, message, owner.create_approval, device_context))
                            except Exception as exc:  # noqa: BLE001
                                # Text chat is an optional ElevenLabs path. Do not make
                                # ordinary Android/web chat fail when that websocket or
                                # signed URL is temporarily unavailable.
                                owner.last_text_chat_error = str(exc)[:500]
                                log.warning("ElevenLabs text chat failed; falling back to Kim agent: %s", exc)
                                result = owner._run(owner.agent.chat(session_id, message, owner.create_approval))
                        else:
                            result = owner._run(owner.agent.chat(session_id, message, owner.create_approval))
                        owner._audit("chat", {"user": user, "conversation_id": session_id[:100], "tools": result.get("tools_used", [])}, True)
                        self._reply(200, result)
                        return
                    if self.path == "/v1/research":
                        question = str(data.get("question", "")).strip()
                        if not question or len(question) > 4_000:
                            raise ValueError("question must be 1-4000 characters")
                        job_id = uuid.uuid4().hex
                        job = {"id": job_id, "question": question, "status": "queued", "created_at": time.time()}
                        with owner.research_lock:
                            owner.research_jobs[job_id] = job
                        asyncio.run_coroutine_threadsafe(owner._research_job(job_id, question, int(data.get("depth", 2)), int(data.get("max_sources", 5))), owner.loop)
                        owner._audit("research_started", {"id": job_id}, True)
                        self._reply(202, {"ok": True, "job": job})
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
                        record = {"id": approval_id, "name": name, "parameters": data.get("parameters", {}), "summary": str(data.get("summary", name))[:500], "status": "pending", "user": self.headers.get("X-Kim-User", "").strip() or "default", "created_at": time.time()}
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
                            user = self.headers.get("X-Kim-User", "").strip()
                            if user and record.get("user", "default") not in {user, "default"}:
                                self._reply(403, {"error": "approval belongs to another user"})
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
                except RuntimeError as exc:
                    log.warning("remote request failed: %s", exc)
                    status = 503 if "not configured" in str(exc).lower() else 502
                    self._reply(status, {"error": str(exc)})
                except Exception:
                    log.exception("remote request failed")
                    self._reply(500, {"error": "internal error"})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="kim-remote", daemon=True)
        self.thread.start()
        log.info("remote API listening on %s:%s", self.host, self.port)

    async def queue_device_command(self, action: str, device_id: str, parameters: Dict[str, Any]) -> str:
        allowed = {"open_url", "open_app", "type_text", "press_key", "screenshot", "playwright_run", "browser_action", "computer_action"}
        if action not in allowed:
            return f"ERROR: unsupported device action: {action}"
        with self.commands_lock:
            device = self.devices.get(device_id, {})
            online = bool(device and time.time() - float(device.get("last_seen", 0)) < 15)
            if not online:
                return f"ERROR: device {device_id} is offline; no action was executed"
            command_id = uuid.uuid4().hex
            command = {"id": command_id, "type": "device", "device_id": device_id, "action": action, "parameters": parameters, "created_at": time.time()}
            self.device_commands.append(command)
            self._persist_state()
        waiter: asyncio.Future[str] = self.loop.create_future()
        self.device_waiters[command_id] = waiter
        self._audit("device_command_queued", {"id": command_id, "action": action, "device_id": device_id}, True)
        try:
            return await asyncio.wait_for(waiter, timeout=30)
        except asyncio.TimeoutError:
            self.device_waiters.pop(command_id, None)
            return f"ERROR: device {device_id} did not report a result within 30 seconds; action may not have executed"

    def _check(self, handler: BaseHTTPRequestHandler) -> bool:
        return True  # auth is unconditional after PIN removal

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
            propai_oauth = state.get("propai_oauth", {})
            if isinstance(propai_oauth, dict):
                self.propai_oauth = {"client_id": str(propai_oauth.get("client_id", ""))}
                self.propai_oauth.update({str(k): v for k, v in propai_oauth.items() if k != "client_id" and isinstance(v, dict) and time.time() - float(v.get("created_at", 0)) < 600})
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

    def _propai_token_path(self) -> Path:
        return Path(os.environ.get("KIM_PROPAI_TOKEN_PATH", str(self.state_path.with_name("propai-token.json")))).expanduser()

    def _propai_disconnected_path(self) -> Path:
        return self.state_path.with_name("propai-disconnected")

    def _disconnect_propai(self) -> None:
        self._propai_token_path().unlink(missing_ok=True)
        self._propai_disconnected_path().write_text("disconnected\n", encoding="utf-8")
        self._audit("propai_disconnect", {}, True)

    def _propai_redirect_uri(self) -> str:
        if not self.remote_domain:
            raise ValueError("remote.domain is required for PropAI connection")
        return f"https://{self.remote_domain}/v1/propai/callback"

    def _propai_auth_url(self) -> str:
        import base64
        import hashlib
        import httpx

        redirect_uri = self._propai_redirect_uri()
        client_id = self.propai_oauth.get("client_id", "")
        if not client_id:
            try:
                response = httpx.post("https://mcp.propai.live/register", json={
                    "client_name": "Kim",
                    "redirect_uris": [redirect_uri],
                }, timeout=15)
                response.raise_for_status()
                client_id = str(response.json().get("client_id", ""))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"could not register Kim with PropAI: {exc}") from exc
            if not client_id:
                raise ValueError("PropAI did not return an OAuth client id")
            self.propai_oauth["client_id"] = client_id

        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        # Keep the OAuth transaction restart-safe. Coolify may replace the
        # container while the user is signing in, so do not depend on process
        # memory or a non-persistent filesystem for PKCE state.
        state_payload = json.dumps({"v": verifier, "c": client_id, "t": int(time.time())}, separators=(",", ":")).encode()
        state = base64.urlsafe_b64encode(state_payload).rstrip(b"=").decode()
        return "https://mcp.propai.live/authorize?" + urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })

    def _consume_propai_state(self, state: str) -> bool:
        import base64
        try:
            padded = state + "=" * (-len(state) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
            return bool(payload.get("v") and payload.get("c") and time.time() - float(payload.get("t", 0)) < 900)
        except (ValueError, TypeError, json.JSONDecodeError):
            return False

    def _finish_propai_auth(self, code: str, state: str) -> None:
        import base64
        import httpx

        try:
            padded = state + "=" * (-len(state) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("PropAI OAuth state is invalid") from exc
        verifier = str(payload.get("v", ""))
        client_id = str(payload.get("c", ""))
        if not verifier or not client_id:
            raise ValueError("PropAI OAuth verifier is missing or expired")
        response = httpx.post("https://mcp.propai.live/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": client_id,
            "redirect_uri": self._propai_redirect_uri(), "code_verifier": verifier,
        }, timeout=15)
        if response.status_code >= 400:
            raise ValueError(f"PropAI token exchange failed: {response.text[:300]}")
        token = response.json()
        if not token.get("access_token"):
            raise ValueError("PropAI did not return an access token")
        if token.get("expires_in"):
            token["expires_at"] = time.time() + float(token["expires_in"])
        path = self._propai_token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(token), encoding="utf-8")
        path.chmod(0o600)
        self._propai_disconnected_path().unlink(missing_ok=True)
        self.propai_oauth.pop(state, None)
        self._persist_state()

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

    def create_approval(self, name: str, parameters: Dict[str, Any], summary: str) -> Dict[str, Any]:
        """Queue an agent-requested side effect for the existing approval UI."""
        approval_id = uuid.uuid4().hex
        record = {"id": approval_id, "name": name, "parameters": parameters, "summary": str(summary)[:500], "status": "pending", "created_at": time.time()}
        with self.approvals_lock:
            self.approvals[approval_id] = record
            self._persist_state()
        self._audit("approval_requested", {"id": approval_id, "name": name, "source": "agent"}, True)
        return {"id": approval_id, "name": name, "status": "pending"}

    async def _research_job(self, job_id: str, question: str, depth: int, max_sources: int) -> None:
        with self.research_lock:
            if job_id in self.research_jobs:
                self.research_jobs[job_id]["status"] = "running"
        result, is_error = await self.registry.run("deep_research", {"question": question, "depth": max(1, min(depth, 3)), "max_sources": max(2, min(max_sources, 8))})
        with self.research_lock:
            if job_id in self.research_jobs:
                self.research_jobs[job_id].update({"status": "failed" if is_error else "completed", "result": result[:100_000], "finished_at": time.time()})
        self._audit("research_finished", {"id": job_id, "error": is_error}, not is_error)

    def _persist_state(self) -> None:
        """Atomically persist queue state without putting it in the audit log."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps({"approvals": self.approvals, "commands": self.commands, "device_commands": self.device_commands, "devices": self.devices, "google_states": self.google_states, "propai_oauth": self.propai_oauth}, ensure_ascii=False), encoding="utf-8")
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

