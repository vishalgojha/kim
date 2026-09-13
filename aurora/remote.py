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
        self.audit_path = Path(str(remote.get("audit_path", "~/.aurora/remote-audit.jsonl"))).expanduser()
        self.state_path = Path(str(remote.get("state_path", "~/.aurora/remote-state.json"))).expanduser()
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.approvals_lock = threading.Lock()
        self.commands: list[Dict[str, Any]] = []
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
                if not owner._check(self):
                    return
                if self.path == "/v1/status":
                    state_path = Path.home() / ".aurora" / "voice_state"
                    try:
                        state = state_path.read_text().strip()
                    except OSError:
                        state = "offline"
                    self._reply(200, {"ok": True, "voice_state": state, "allowed_tools": sorted(owner.allowed_tools), "direct_tools": sorted(owner.direct_tools)})
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
                            if record["name"] in owner.direct_tools:
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
            if isinstance(approvals, dict):
                self.approvals = {str(k): v for k, v in approvals.items() if isinstance(v, dict)}
            if isinstance(commands, list):
                self.commands = [v for v in commands if isinstance(v, dict)]
        except (OSError, json.JSONDecodeError):
            return

    def _persist_state(self) -> None:
        """Atomically persist queue state without putting it in the audit log."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps({"approvals": self.approvals, "commands": self.commands}, ensure_ascii=False), encoding="utf-8")
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
<html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kim Control</title>
<style>
body{font:16px system-ui;background:#101216;color:#f3f4f6;max-width:680px;margin:0 auto;padding:24px}
input,textarea,button{font:inherit;border-radius:10px;border:1px solid #374151;padding:12px;background:#181b22;color:inherit;width:100%;box-sizing:border-box;margin:6px 0}
button{background:#2563eb;border:0;cursor:pointer}button.secondary{background:#374151}.row{display:flex;gap:8px}.row button{flex:1}.card{background:#181b22;padding:16px;border-radius:14px;margin:14px 0}pre{white-space:pre-wrap;overflow:auto;color:#a7f3d0}
</style>
<body><h1>Kim</h1><p>Private control panel</p>
<div class="card"><label>Kim PIN</label><input id="pin" type="password" inputmode="numeric" maxlength="6" placeholder="Enter 6-digit PIN"><button onclick="save()">Save PIN</button></div>
<div class="card"><h2>Status</h2><pre id="status">Not connected</pre><button onclick="status()">Refresh status</button><div class="row"><button class="secondary" onclick="control('pause')">Pause</button><button onclick="control('wake')">Wake</button></div></div>
<div class="card"><h2>Run approved diagnostic</h2><input id="name" value="system_info"><textarea id="params" rows="3">{}</textarea><button onclick="runTool()">Run</button><pre id="result"></pre></div>
<div class="card"><h2>Approvals</h2><button onclick="approvals()">Refresh approvals</button><div id="approvals">None loaded</div></div>
<script>
const key='kim-pin'; document.querySelector('#pin').value=localStorage.getItem(key)||'';
function save(){localStorage.setItem(key,document.querySelector('#pin').value);status()}
async function call(path,opts={}){opts.headers=Object.assign({'X-Kim-Pin':document.querySelector('#pin').value,'Content-Type':'application/json'},opts.headers||{});const r=await fetch(path,opts);const j=await r.json();if(!r.ok)throw Error(j.error||JSON.stringify(j));return j}
async function status(){try{document.querySelector('#status').textContent=JSON.stringify(await call('/v1/status'),null,2)}catch(e){document.querySelector('#status').textContent=e}}
async function control(action){try{document.querySelector('#result').textContent=JSON.stringify(await call('/v1/control',{method:'POST',body:JSON.stringify({action})}),null,2)}catch(e){document.querySelector('#result').textContent=e}}
async function runTool(){try{const parameters=JSON.parse(document.querySelector('#params').value||'{}');document.querySelector('#result').textContent=JSON.stringify(await call('/v1/tool',{method:'POST',body:JSON.stringify({name:document.querySelector('#name').value,parameters})}),null,2)}catch(e){document.querySelector('#result').textContent=e}}
async function approvals(){try{const data=await call('/v1/approvals');const box=document.querySelector('#approvals');box.innerHTML='';for(const a of data.approvals){const row=document.createElement('div');row.className='card';row.innerHTML='<b>'+a.name+'</b><br><small>'+a.summary+'</small><br><small>Status: '+a.status+'</small>';if(a.status==='pending'){for(const action of ['approve','reject']){const b=document.createElement('button');b.textContent=action[0].toUpperCase()+action.slice(1);b.className=action==='reject'?'secondary':'';b.onclick=async()=>{await call('/v1/approvals/'+a.id+'/'+action,{method:'POST',body:'{}'});approvals()};row.appendChild(b)}}box.appendChild(row)}}catch(e){document.querySelector('#approvals').textContent=e}}
</script></body></html>"""
