"""Kim's provider-neutral text agent."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List

import httpx

from .tools.registry import ToolRegistry


DIRECT_TOOLS = {
    # Safe, reversible local controls should feel immediate in the desktop app.
    "launch_app", "navigate_browser",
}

WRITE_TOOLS = {
    "gmail_send", "calendar_create", "whatsapp_send", "type_text", "press_key",
    "clipboard_set", "volume_set", "run_shell", "start_task",
    "cancel_task", "schedule_remind", "schedule_every", "schedule_cancel",
    "file_write", "file_edit", "file_delete", "knowledge_ingest", "opencode_run",
}


class AgentCore:
    """Kim-owned OpenAI-compatible tool-calling loop; ElevenLabs is optional."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}

    @property
    def configured(self) -> bool:
        return bool((os.environ.get("KIM_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")).strip())

    def status(self) -> Dict[str, Any]:
        return {"configured": self.configured, "provider": "openai-compatible", "model": os.environ.get("KIM_LLM_MODEL", "gemini-2.5-flash")}

    def _tools(self) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]}} for s in self.registry.client_schemas()]

    def _endpoint(self) -> str:
        base = os.environ.get("KIM_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def _headers(self) -> Dict[str, str]:
        key = (os.environ.get("KIM_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")).strip()
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _approval_text(self, name: str, args: Dict[str, Any]) -> str:
        return f"I prepared `{name}` and sent it to your approval queue. Details: {json.dumps(args, ensure_ascii=False)[:700]}"

    async def chat(self, session_id: str, message: str, request_approval: Callable[[str, Dict[str, Any], str], Dict[str, Any]]) -> Dict[str, Any]:
        message = message.strip()
        if not message or len(message) > 8_000:
            raise ValueError("message must be 1-8000 characters")
        if not self.configured:
            raise RuntimeError("Kim's brain is not configured. Add KIM_LLM_API_KEY (or GEMINI_API_KEY) in Coolify.")
        history = self.sessions.setdefault(session_id[:100] or "web", [])
        history.append({"role": "user", "content": message})
        history[:] = history[-20:]
        tools_used: List[str] = []
        async with httpx.AsyncClient(timeout=90) as client:
            for _ in range(8):
                body = {"model": os.environ.get("KIM_LLM_MODEL", "gemini-2.5-flash"), "messages": [{"role": "system", "content": "You are Kim, a concise practical technical personal agent. Inspect first. Never claim an action happened unless its tool succeeded. Any data change, message, device control, typing, file edit, or command requires user approval; ask for approval instead of bypassing it."}] + history, "tools": self._tools(), "tool_choice": "auto", "temperature": 0.2}
                response = await client.post(self._endpoint(), headers=self._headers(), json=body)
                if response.status_code >= 400:
                    raise RuntimeError(f"LLM request failed ({response.status_code}): {response.text[:500]}")
                choice = (response.json().get("choices") or [{}])[0]
                assistant = choice.get("message") or {}
                calls = assistant.get("tool_calls") or []
                if not calls:
                    answer = str(assistant.get("content") or "I'm ready.").strip()
                    history.append({"role": "assistant", "content": answer})
                    history[:] = history[-20:]
                    return {"ok": True, "message": answer, "tools_used": tools_used}
                history.append(assistant)
                for call in calls:
                    fn = call.get("function") or {}
                    name = str(fn.get("name", ""))
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    require_approval = os.environ.get("KIM_REQUIRE_APPROVAL", "0").strip().lower() in {"1", "true", "yes", "on"}
                    if name not in self.registry.names():
                        result, is_error = f"Unknown tool: {name}", True
                    elif require_approval and name in WRITE_TOOLS and name not in DIRECT_TOOLS:
                        approval = request_approval(name, args, f"Kim wants to run {name}")
                        result, is_error = self._approval_text(name, args) + f" Approval ID: {approval['id']}.", False
                    else:
                        result, is_error = await self.registry.run(name, args)
                    tools_used.append(name)
                    history.append({"role": "tool", "tool_call_id": call.get("id", name), "content": ("ERROR: " if is_error else "") + result[:20_000]})
        raise RuntimeError("Kim reached the tool-step limit without producing a final answer")
