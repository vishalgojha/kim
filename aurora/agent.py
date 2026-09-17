"""Kim's provider-neutral text agent."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List

import httpx

from .tools.context import get_ctx
from .tools.registry import ToolRegistry

log = logging.getLogger("aurora.agent")


DIRECT_TOOLS = {
    # Safe, reversible local controls should feel immediate in the desktop app.
    "launch_app", "navigate_browser",
}

WRITE_TOOLS = {
    "gmail_send", "calendar_create", "whatsapp_send", "type_text", "press_key", "browser_action",
    "computer_action",
    "clipboard_set", "volume_set", "run_shell", "start_task",
    "cancel_task", "schedule_remind", "schedule_every", "schedule_cancel",
    "file_write", "file_edit", "file_delete", "knowledge_ingest", "opencode_run",
}

# Tools that only act on the physical laptop. The hosted brain must not run
# these inside the cloud container (they would "succeed" with no visible
# effect on the user's machine). When running hosted, they are hidden from the
# schema and the laptop is only reachable through device_command.
HOSTED_LAPTOP_TOOLS = {
    "launch_app", "navigate_browser", "browser_action", "computer_action", "known_apps",
    "run_shell", "start_task", "task_log", "list_tasks", "cancel_task",
    "screenshot", "type_text", "press_key",
    "clipboard_get", "clipboard_set", "volume_get", "volume_set", "desktop_notify",
    "system_info", "battery", "disk_usage", "running_processes",
    "read_file", "write_file", "edit_file", "file_info", "files_list", "files_search", "document_extract",
    "opencode_run",
    "schedule_remind", "schedule_every", "list_schedules", "schedule_cancel",
    "whatsapp_search", "whatsapp_recent", "whatsapp_chats",
}

# Which device_command action can carry a laptop tool from the hosted brain.
LAPTOP_DEVICE_ACTION = {
    "launch_app": ("open_app", lambda p: {"name": p.get("name") or p.get("app_name") or ""}),
    "navigate_browser": ("open_url", lambda p: {"url": p.get("url") or p.get("name") or ""}),
    "browser_action": ("browser_action", lambda p: dict(p)),
    "computer_action": ("computer_action", lambda p: dict(p)),
    "type_text": ("type_text", lambda p: {"text": p.get("text", "")}),
    "press_key": ("press_key", lambda p: {"key": p.get("key", "")}),
    "screenshot": ("screenshot", lambda p: {}),
}

# Cloud plus routing tools: what the hosted brain may see and run by itself.
CLOUD_TOOLS = {"device_command"}


async def route_laptop_tool(name: str, kwargs: Dict[str, Any]) -> tuple[str, bool]:
    """Send a laptop-bound tool call to the connected laptop relay and wait.

    Returns the laptop's real result, or a clear error when it is offline.
    """
    queue = get_ctx().get("queue_device_command")
    mapping = LAPTOP_DEVICE_ACTION.get(name)
    if mapping is None or queue is None:
        return (
            f"Tool '{name}' only runs on the laptop, which is not reachable from this hosted server; "
            "no action was executed. Ask the user to start the Kim desktop relay, or run the request from the laptop directly.",
            True,
        )
    action, map_params = mapping
    result = await queue(action, "laptop", map_params(kwargs or {}))
    return result, False


class AgentCore:
    """Kim-owned OpenAI-compatible tool-calling loop; ElevenLabs is optional.

    Providers are tried in order: the configured default LLM first, then
    Sarvam AI as a fallback for text-based tool calling.
    """

    def __init__(self, registry: ToolRegistry, hosted: bool = False) -> None:
        self.registry = registry
        self.hosted = hosted
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}

    def _providers(self) -> List[Dict[str, Any]]:
        primary_key = (os.environ.get("KIM_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")).strip()
        sarvam_key = os.environ.get("SARVAM_API_KEY", "").strip()
        providers: List[Dict[str, Any]] = []
        if primary_key:
            base = os.environ.get("KIM_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
            providers.append({
                "name": "default",
                "model": os.environ.get("KIM_LLM_MODEL", "gemini-2.5-flash"),
                "endpoint": base if base.endswith("/chat/completions") else base + "/chat/completions",
                "headers": {"Authorization": f"Bearer {primary_key}", "Content-Type": "application/json"},
            })
        if sarvam_key:
            base = os.environ.get("SARVAM_BASE_URL", "https://api.sarvam.ai").rstrip("/")
            providers.append({
                "name": "sarvam",
                "model": os.environ.get("SARVAM_MODEL", "sarvam-105b"),
                "endpoint": base if base.endswith("/chat/completions") else base + "/chat/completions",
                "headers": {"Authorization": f"Bearer {sarvam_key}", "api-subscription-key": sarvam_key, "Content-Type": "application/json"},
            })
        return providers

    @property
    def configured(self) -> bool:
        return bool(self._providers())

    def status(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "providers": [p["name"] for p in self._providers()],
            "sarvam_fallback": bool(os.environ.get("SARVAM_API_KEY", "").strip()),
            "model": os.environ.get("KIM_LLM_MODEL", "gemini-2.5-flash"),
        }

    def _tools(self) -> List[Dict[str, Any]]:
        schemas = self.registry.client_schemas()
        if self.hosted:
            schemas = [s for s in schemas if s["name"] not in HOSTED_LAPTOP_TOOLS]
        return [{"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]}} for s in schemas]

    def _approval_text(self, name: str, args: Dict[str, Any]) -> str:
        return (
            f"NOT EXECUTED: `{name}` was sent to the approval queue and nothing ran. "
            f"Details: {json.dumps(args, ensure_ascii=False)[:700]}"
        )

    async def chat(self, session_id: str, message: str, request_approval: Callable[[str, Dict[str, Any], str], Dict[str, Any]]) -> Dict[str, Any]:
        message = message.strip()
        if not message or len(message) > 8_000:
            raise ValueError("message must be 1-8000 characters")
        providers = self._providers()
        if not providers:
            raise RuntimeError("Kim's brain is not configured. Add KIM_LLM_API_KEY (or GEMINI_API_KEY), or set SARVAM_API_KEY as a fallback.")
        history = self.sessions.setdefault(session_id[:100] or "web", [])
        base_history = (history + [{"role": "user", "content": message}])[-20:]
        errors: List[str] = []
        for provider in providers:
            try:
                answer, tools_used, final_history = await self._run_provider(provider, base_history, request_approval)
            except Exception as exc:  # noqa: BLE001
                log.warning("LLM provider %s failed; %s", provider["name"], exc)
                errors.append(f"{provider['name']}: {str(exc)[:300]}")
                continue
            history[:] = final_history[-20:]
            return {"ok": True, "message": answer, "tools_used": tools_used}
        raise RuntimeError("Kim's brain failed on all configured providers: " + "; ".join(errors))

    async def _run_provider(
        self,
        provider: Dict[str, Any],
        base_history: List[Dict[str, Any]],
        request_approval: Callable[[str, Dict[str, Any], str], Dict[str, Any]],
    ) -> tuple[str, List[str], List[Dict[str, Any]]]:
        tools_used: List[str] = []
        history = list(base_history)
        max_steps = int(os.environ.get("KIM_MAX_TOOL_STEPS", "20"))
        async with httpx.AsyncClient(timeout=90) as client:
            for _ in range(max_steps):
                body = {"model": provider["model"], "messages": [{"role": "system", "content": "You are Kim, a concise practical technical personal agent. Inspect first. Never claim an action happened unless its tool succeeded. Any data change, message, device control, typing, file edit, or command requires user approval; ask for approval instead of bypassing it. For on-screen/desktop work, verify each step before moving on: after an action, run computer_action see (or describe) again to check the screen, then either proceed to the next step or retry with a corrected action. Keep taking steps until the user's goal is finished; only stop when you can show a verified result."}] + history, "tools": self._tools(), "tool_choice": "auto", "temperature": 0.2}
                response = await client.post(provider["endpoint"], headers=provider["headers"], json=body)
                if response.status_code >= 400:
                    raise RuntimeError(f"LLM request failed ({response.status_code}): {response.text[:500]}")
                choice = (response.json().get("choices") or [{}])[0]
                assistant = choice.get("message") or {}
                calls = assistant.get("tool_calls") or []
                if not calls:
                    answer = str(assistant.get("content") or "I'm ready.").strip()
                    history.append({"role": "assistant", "content": answer})
                    return answer, tools_used, history
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
                    elif self.hosted and name in HOSTED_LAPTOP_TOOLS:
                        result, is_error = await route_laptop_tool(name, args)
                    elif require_approval and name in WRITE_TOOLS and name not in DIRECT_TOOLS:
                        approval = request_approval(name, args, f"Kim wants to run {name}")
                        result, is_error = self._approval_text(name, args) + f" Approval ID: {approval['id']}.", True
                    else:
                        result, is_error = await self.registry.run(name, args)
                    tools_used.append(name)
                    history.append({"role": "tool", "tool_call_id": call.get("id", name), "content": ("ERROR: " if is_error else "") + result[:20_000]})
        raise RuntimeError("Kim reached the tool-step limit without producing a final answer")
