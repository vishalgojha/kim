import asyncio
import base64
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict

import websockets

from .audio import AudioCapture, AudioOutput
from .eleven import ElevenAPI, ElevenError
from .tools.context import get_ctx
from .tools.registry import ToolRegistry

log = logging.getLogger("aurora.realtime")

MAX_MSG = 16 * 1024 * 1024
STOP_PHRASES = {
    "stop",
    "quiet",
    "be quiet",
    "shut up",
    "go silent",
    "pause",
    "stop listening",
    "stop talking",
}
WAKE_PHRASES = {
    "kim",
    "hey kim",
    "wake up",
    "kim wake up",
}
VOICE_STATE_PATH = Path.home() / ".aurora" / "voice_state"


class RealtimeSession:
    def __init__(
        self,
        cfg: Dict[str, Any],
        eleven: ElevenAPI,
        registry: ToolRegistry,
        capture: AudioCapture,
        output: AudioOutput,
    ):
        self.cfg = cfg
        self.eleven = eleven
        self.registry = registry
        self.capture = capture
        self.output = output
        self.agent_id = cfg["elevenlabs"]["agent_id"]
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._last_interrupt_id = 0
        self._conversation_id = None
        self._running = True
        self._paused = False
        self._set_voice_state("offline")

    @staticmethod
    def _set_voice_state(state: str) -> None:
        try:
            VOICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            VOICE_STATE_PATH.write_text(state)
        except Exception:
            pass

    # ------------------------------------------------------------------ ws io
    async def _ws_url(self) -> str:
        try:
            return await asyncio.get_running_loop().run_in_executor(None, self.eleven.get_signed_url, self.agent_id)
        except ElevenError as e:
            log.warning("signed-url failed (%s); falling back to direct key auth", e)
            return f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={self.agent_id}"

    async def _open(self) -> None:
        url = await self._ws_url()
        timeout = min(6.0, self.cfg["turn"].get("initial_wait_time", 15.0) or 6.0)
        headers = {"origin": "http://localhost"}
        self._ws = await websockets.connect(
            url,
            max_size=MAX_MSG,
            ping_interval=20,
            ping_timeout=20,
            compression=None,
            additional_headers=headers if "?agent_id=" in url else None,
            open_timeout=30,
        )
        init = {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {},
            "dynamic_variables": {},
        }
        await self._ws.send(json.dumps(init))

    async def _send(self, obj: Dict[str, Any]) -> None:
        if not self._ws:
            return
        async with self._send_lock:
            try:
                await self._ws.send(json.dumps(obj))
            except Exception:
                pass

    async def close(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ---------------------------------------------------------------- events
    async def _handle(self, msg: Dict[str, Any]) -> None:
        mtype = msg.get("type", "")
        try:
            if mtype == "conversation_initiation_metadata":
                ev = msg.get("conversation_initiation_metadata_event", {})
                self._conversation_id = ev.get("conversation_id")
                log.info("conversation %s | in:%s out:%s", self._conversation_id,
                         ev.get("user_input_audio_format"), ev.get("agent_output_audio_format"))

            elif mtype == "ping":
                ev = msg.get("ping_event", {})
                await self._send({"type": "pong", "event_id": ev.get("event_id")})

            elif mtype == "audio":
                if self._paused:
                    return
                self._set_voice_state("responding")
                ev = msg.get("audio_event", {})
                try:
                    if int(ev.get("event_id", 0)) <= self._last_interrupt_id:
                        return
                except (TypeError, ValueError):
                    pass
                audio = base64.b64decode(ev.get("audio_base_64", ""))
                if audio:
                    await self.output.write(audio)

            elif mtype in ("interruption", "user_interruption"):
                ev = msg.get("interruption_event", {}) or {}
                try:
                    self._last_interrupt_id = int(ev.get("event_id", self._last_interrupt_id))
                except (TypeError, ValueError):
                    pass
                await self.output.interrupt()

            elif mtype == "user_transcript":
                t = msg.get("user_transcription_event", {}).get("user_transcript", "")
                if t.strip():
                    spoken = t.strip()
                    normalized = re.sub(r"[^a-z0-9 ]+", "", spoken.lower()).strip()
                    if normalized in STOP_PHRASES:
                        self._paused = True
                        self._set_voice_state("paused")
                        await self.output.interrupt()
                        log.info("voice paused by stop phrase: %s", spoken)
                        print("\n  Kim paused. Say 'Kim' or 'wake up' to resume.")
                    elif self._paused and normalized in WAKE_PHRASES:
                        self._paused = False
                        self._set_voice_state("listening")
                        log.info("voice resumed by wake phrase: %s", spoken)
                        print("\n  Kim listening.")
                    elif not self._paused:
                        print(f"\n  you: {spoken}")

            elif mtype in ("agent_response", "agent_response_correction"):
                pass

            elif mtype == "agent_chat_response_part":
                part = msg.get("text_response_part", {})
                if part.get("type") == "start":
                    pass

            elif mtype == "client_tool_call":
                call = msg.get("client_tool_call", {})
                asyncio.create_task(self._run_tool(call))

            elif mtype == "agent_response_complete":
                if not self._paused:
                    self._set_voice_state("listening")
                log.debug("agent response complete")

            elif mtype in ("client_error",):
                ev = msg.get("client_error", {})
                log.warning("client_error: %s", ev)
        except Exception as e:  # noqa: BLE001
            log.exception("error handling %s", mtype)

    async def _run_tool(self, call: Dict[str, Any]) -> None:
        tool_name = call.get("tool_name", "")
        tool_call_id = call.get("tool_call_id", "")
        params = call.get("parameters", {}) or {}
        log.info("tool call: %s %s", tool_name, tool_call_id)
        result, is_error = await self.registry.run(tool_name, params)
        await self._send(
            {
                "type": "client_tool_result",
                "tool_call_id": tool_call_id,
                "result": result,
                "is_error": is_error,
            }
        )
        log.info("tool result (%s, err=%s): %.200s", tool_name, is_error, result)

    async def _receive_loop(self) -> None:
        assert self._ws
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._handle(msg)

    async def _run_once(self) -> None:
        await self._open()
        async def mic_callback(chunk: bytes) -> None:
            await self._send({"user_audio_chunk": base64.b64encode(chunk).decode()})

        await self.capture.start(mic_callback)
        self._set_voice_state("listening")
        log.info("voice session live (agent %s)", self.agent_id)
        await self._receive_loop()
        await self.capture.stop()

    async def run_forever(self) -> None:
        backoff = 2
        while self._running:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("session dropped: %s", e)
            await self.close()
            self._set_voice_state("offline")
            await self.capture.stop()
            await self.output.interrupt()
            if not self._running:
                break
            log.info("reconnecting in %ss...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        log.info("voice session ended")
