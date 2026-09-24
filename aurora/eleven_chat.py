"""Text chat bridge for the ElevenLabs conversational agent."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List

import websockets

from .agent import DIRECT_TOOLS, HOSTED_LAPTOP_TOOLS, WRITE_TOOLS, route_laptop_tool
from .eleven import ElevenAPI, ElevenError
from .tools.context import get_ctx
from .tools.registry import ToolRegistry

log = logging.getLogger("aurora.eleven_chat")

MAX_MSG = 16 * 1024 * 1024


class ElevenTextChat:
    def __init__(self, cfg: Dict[str, Any], eleven: ElevenAPI, registry: ToolRegistry, hosted: bool = False) -> None:
        self.cfg = cfg
        self.eleven = eleven
        self.registry = registry
        self.hosted = hosted
        self.agent_id = cfg["elevenlabs"]["agent_id"]

    async def _ws_url(self) -> str:
        try:
            return await asyncio.get_running_loop().run_in_executor(None, self.eleven.get_signed_url, self.agent_id)
        except ElevenError as exc:
            log.warning("signed-url failed (%s); falling back to direct agent URL", exc)
            return f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={self.agent_id}"

    @staticmethod
    def _part_text(msg: Dict[str, Any]) -> str:
        if msg.get("type") == "agent_chat_response_part":
            part = msg.get("text_response_part", {})
            return str(part.get("text", "") or part.get("content", "") or "")
        if msg.get("type") in {"agent_response", "agent_response_correction"}:
            return ElevenTextChat._find_text(msg)
        return ""

    @staticmethod
    def _find_text(value: Any) -> str:
        if isinstance(value, str):
            return value if value.strip() else ""
        if isinstance(value, dict):
            for key in ("agent_response_event", "agent_response", "text_response_part", "text", "content"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    return text
            for key, nested in value.items():
                if key in {"type", "event_id", "audio_base_64", "audio", "timestamp"}:
                    continue
                text = ElevenTextChat._find_text(nested)
                if text:
                    return text
        if isinstance(value, list):
            for nested in value:
                text = ElevenTextChat._find_text(nested)
                if text:
                    return text
        return ""

    @staticmethod
    def _approval_text(name: str, args: Dict[str, Any], approval: Dict[str, Any]) -> str:
        preview = json.dumps(args, ensure_ascii=False)[:700]
        return (
            f"NOT EXECUTED: `{name}` was sent to the approval queue and nothing ran. "
            f"Details: {preview} Approval ID: {approval['id']}."
        )

    async def chat(
        self,
        session_id: str,
        message: str,
        request_approval: Callable[[str, Dict[str, Any], str], Dict[str, Any]],
        device_context: str = "web browser",
    ) -> Dict[str, Any]:
        message = message.strip()
        if not message or len(message) > 8_000:
            raise ValueError("message must be 1-8000 characters")
        if not self.eleven.api_key or not self.agent_id:
            raise RuntimeError("ElevenLabs agent is not configured. Add ELEVENLABS_API_KEY and elevenlabs.agent_id.")

        tools_used: List[str] = []
        chunks: List[str] = []
        url = await self._ws_url()
        headers = {"origin": "http://localhost"}
        async with websockets.connect(
            url,
            max_size=MAX_MSG,
            ping_interval=20,
            ping_timeout=20,
            compression=None,
            additional_headers=headers if "?agent_id=" in url else None,
            open_timeout=30,
        ) as ws:
            context = json.dumps(get_ctx(), ensure_ascii=False, default=str)[-4000:]
            await ws.send(json.dumps({
                "type": "conversation_initiation_client_data",
                "conversation_config_override": {},
                "dynamic_variables": {"last_context": context, "device_context": device_context[:500]},
            }))

            started = False
            deadline = asyncio.get_running_loop().time() + 45
            quiet_deadline = None
            seen_events: List[str] = []
            while True:
                now = asyncio.get_running_loop().time()
                end = min(deadline, quiet_deadline) if quiet_deadline else deadline
                timeout = max(1.0, end - now)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError as exc:
                    if chunks:
                        return {"ok": True, "message": "".join(chunks).strip(), "tools_used": tools_used, "tool_results_completed": bool(tools_used)}
                    raise RuntimeError(f"ElevenLabs chat timed out before a text response was completed; events: {', '.join(seen_events) or 'none'}") from exc
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type", "")
                if len(seen_events) < 16:
                    seen_events.append(str(mtype))
                    log.debug("eleven text event: %s", mtype)
                if mtype == "conversation_initiation_metadata" and not started:
                    started = True
                    await ws.send(json.dumps({"type": "user_message", "text": message}))
                    log.debug("eleven text user_message sent")
                    continue

                if mtype == "ping":
                    ev = msg.get("ping_event", {})
                    await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
                    continue

                if mtype == "client_tool_call":
                    call = msg.get("client_tool_call", {})
                    name = str(call.get("tool_name", ""))
                    tool_call_id = str(call.get("tool_call_id", name))
                    params = call.get("parameters", {}) or {}
                    if not isinstance(params, dict):
                        params = {}
                    require_approval = bool((self.cfg.get("permissions") or {}).get("require_approval", False))
                    if self.hosted and name in HOSTED_LAPTOP_TOOLS:
                        result, is_error = await route_laptop_tool(name, params)
                    elif require_approval and name in WRITE_TOOLS and name not in DIRECT_TOOLS:
                        approval = request_approval(name, params, f"Kim wants to run {name}")
                        result, is_error = self._approval_text(name, params, approval), True
                    else:
                        result, is_error = await self.registry.run(name, params)
                    tools_used.append(name)
                    quiet_deadline = None
                    await ws.send(json.dumps({
                        "type": "client_tool_result",
                        "tool_call_id": tool_call_id,
                        "result": result[:20_000],
                        "is_error": is_error,
                    }))
                    # The agent needs time to digest the tool result and produce
                    # a final answer, so a fresh window starts after each call
                    # instead of burning the one global budget.
                    deadline = asyncio.get_running_loop().time() + 45
                    continue

                text = self._part_text(msg)
                if text:
                    chunks.append(text)
                    if mtype in {"agent_response", "agent_response_correction"}:
                        # ElevenLabs may emit an interim acknowledgement (for
                        # example, “Searching…”) before the client tool call.
                        # Keep the socket open so the actual result can follow.
                        quiet_deadline = asyncio.get_running_loop().time() + 5
                    continue

                if mtype == "agent_response_complete":
                    answer = "".join(chunks).strip()
                    return {"ok": True, "message": answer or "I'm ready.", "tools_used": tools_used, "tool_results_completed": bool(tools_used)}

                if mtype == "client_error":
                    raise RuntimeError(f"ElevenLabs chat failed: {msg.get('client_error', msg)}")
