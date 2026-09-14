import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from elevenlabs import AgentConfig, ConversationalConfig, ElevenLabs
from elevenlabs import types as T

log = logging.getLogger("aurora.eleven")

AGENT_NAME = "Kim"
DEFAULT_TTS_MODEL = "eleven_multilingual_v2"
CONVERSATIONAL_TTS_MODEL = "eleven_flash_v2"
MAX_TOOL_TIMEOUT = 120


class ElevenError(Exception):
    pass


def _base_url(region: str) -> Optional[str]:
    r = (region or "").strip().lower()
    if not r or r == "us":
        return None
    return f"https://{r}.api.elevenlabs.io"


class ElevenAPI:
    """Thin wrapper over the official `elevenlabs` SDK.

    Handles agent create/update (including the toolbox endpoint for client
    tools and the modern ``prompt.tool_ids`` wiring), TTS for proactive
    speech, voices and signed conversation URLs. The realtime WebSocket
    session lives in realtime.py and consumes get_signed_url().
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.api_key = cfg["elevenlabs"]["api_key"]
        self.region = (cfg["elevenlabs"].get("region") or "").strip().lower()
        self.tts_model = cfg["elevenlabs"].get("tts_model") or DEFAULT_TTS_MODEL
        self.conv_tts_model = cfg["elevenlabs"].get("conversational_tts_model") or CONVERSATIONAL_TTS_MODEL
        self.base_url = _base_url(self.region) or "https://api.elevenlabs.io"
        try:
            self._sdk = ElevenLabs(api_key=self.api_key, base_url=_base_url(self.region))
        except Exception as e:  # noqa: BLE001
            raise ElevenError(f"failed to init SDK: {e}") from e

    def close(self) -> None:
        close = getattr(self._sdk, "close", None)
        if callable(close):
            try:
                close()
            except TypeError:
                pass

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ElevenError:
            raise
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "status_code", None)
            detail = getattr(e, "body", None) or str(e)
            raise ElevenError(f"api error {status or ''}: {detail}") from e

    # ------------------------------------------------------------------ voices
    def list_voices(self) -> List[Dict[str, str]]:
        resp = self._call(self._sdk.voices.get_all)
        return [{"id": v.voice_id, "name": v.name} for v in resp.voices]

    # ------------------------------------------------------------------ tools
    @staticmethod
    def _clean_params(local: Dict[str, Any], remote: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(remote, dict):
            return remote or {}
        cleaned: Dict[str, Any] = {}
        for key, lval in (local or {}).items():
            if key in remote:
                cleaned[key] = remote[key]
        props = cleaned.get("properties")
        if isinstance(props, dict):
            local_props = (local or {}).get("properties") or {}
            cleaned_props: Dict[str, Any] = {}
            for pname, pval in props.items():
                lprop = local_props.get(pname)
                if isinstance(pval, dict) and isinstance(lprop, dict):
                    cleaned_props[pname] = {k: pval[k] for k in lprop.keys() if k in pval}
                elif isinstance(pval, dict):
                    cleaned_props[pname] = pval
                else:
                    cleaned_props[pname] = pval
            cleaned["properties"] = cleaned_props
        return cleaned

    @staticmethod
    def _local_fields(schema: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": schema["name"],
            "description": schema.get("description") or "",
            "parameters": schema.get("parameters"),
            "expects_response": bool(schema.get("expects_response", True)),
            "response_timeout_secs": int(schema.get("response_timeout_secs", 60)),
        }

    @classmethod
    def _remote_fields(cls, tool: Any, schema: Dict[str, Any]) -> Dict[str, Any]:
        d = tool.tool_config.model_dump(exclude_none=True)
        return cls._local_fields(
            {
                "name": d.get("name", ""),
                "description": d.get("description") or "",
                "parameters": cls._clean_params(schema.get("parameters"), d.get("parameters")),
                "expects_response": d.get("expects_response", True),
                "response_timeout_secs": d.get("response_timeout_secs", 60),
            }
        )

    @staticmethod
    def _fp(data: Dict[str, Any]) -> str:
        return json.dumps(data, sort_keys=True, default=str)

    def _list_remote_tools(self) -> Dict[str, Any]:
        remote: Dict[str, Any] = {}
        cursor: Optional[str] = None
        while True:
            resp = self._call(
                self._sdk.conversational_ai.tools.list,
                page_size=100,
                cursor=cursor,
            )
            for t in (getattr(resp, "tools", None) or []):
                name = getattr(t.tool_config, "name", None)
                if name:
                    remote[name] = t
            if not getattr(resp, "has_more", False):
                break
            cursor = getattr(resp, "next_cursor", None)
            if not cursor:
                break
        return remote

    def sync_tools(self, schemas: List[Dict[str, Any]]) -> List[str]:
        """Create/update standalone client tool records, return tool_ids in order."""
        remote = self._list_remote_tools()
        tool_ids: List[str] = []
        for schema in schemas:
            name = schema["name"]
            local = self._local_fields(schema)
            existing = remote.get(name)
            if existing is None:
                r = self._call(
                    self._sdk.conversational_ai.tools.create,
                    request={"tool_config": schema},
                )
                tool_ids.append(r.id)
                log.info("created tool %s (%s)", name, r.id)
            elif self._fp(local) != self._fp(self._remote_fields(existing, schema)):
                r = self._call(
                    self._sdk.conversational_ai.tools.update,
                    existing.id,
                    request={"tool_config": schema},
                )
                tool_ids.append(r.id)
                log.info("updated tool %s (%s)", name, r.id)
            else:
                tool_ids.append(existing.id)
        return tool_ids

    # ----------------------------------------------------------------- agent
    def build_config(self, cfg: Dict[str, Any], tool_ids: List[str]) -> ConversationalConfig:
        agent_cfg = cfg["agent"]
        el = cfg["elevenlabs"]
        turn_cfg = cfg.get("turn", {})
        prompt_block = T.PromptAgentApiModelOutput(
            prompt=agent_cfg.get("prompt") or "",
            llm=agent_cfg.get("llm") or "gpt-5-mini",
            tool_ids=tool_ids,
        )
        if agent_cfg.get("reasoning"):
            prompt_block.reasoning_effort = "low"
        agent = AgentConfig(
            first_message=agent_cfg.get("first_message", ""),
            language=agent_cfg.get("language"),
            prompt=prompt_block,
        )
        config = ConversationalConfig(
            turn=T.TurnConfig(
                turn_timeout=float(turn_cfg.get("turn_timeout", 2.0)),
                turn_eagerness=turn_cfg.get("turn_eagerness", "normal"),
                silence_end_call_timeout=float(turn_cfg.get("silence_end_call_timeout", -1.0)),
                initial_wait_time=float(turn_cfg.get("initial_wait_time", 15.0)),
            ),
            tts=T.TtsConversationalConfigOutput(
                model_id=self.conv_tts_model,
                voice_id=el.get("voice_id"),
                agent_output_audio_format="pcm_16000",
            ),
            agent=agent,
        )
        return config

    def create_agent(self, cfg: Dict[str, Any], tool_ids: List[str]) -> str:
        r = self._call(
            self._sdk.conversational_ai.agents.create,
            name=AGENT_NAME,
            conversation_config=self.build_config(cfg, tool_ids),
        )
        agent_id = getattr(r, "agent_id", "")
        if not agent_id:
            raise ElevenError(f"create_agent returned no agent_id: {r}")
        return agent_id

    def update_agent(self, agent_id: str, cfg: Dict[str, Any], tool_ids: List[str]) -> None:
        self._call(
            self._sdk.conversational_ai.agents.update,
            agent_id,
            name=AGENT_NAME,
            conversation_config=self.build_config(cfg, tool_ids),
        )

    def get_agent(self, agent_id: str) -> Any:
        return self._call(self._sdk.conversational_ai.agents.get, agent_id)

    def get_signed_url(self, agent_id: str) -> str:
        r = self._call(
            self._sdk.conversational_ai.conversations.get_signed_url,
            agent_id=agent_id,
        )
        url = getattr(r, "signed_url", "") or ""
        if not url:
            raise ElevenError(f"no signed_url returned for agent {agent_id}")
        return url

    # -------------------------------------------------------------------- tts
    def tts(self, text: str, voice_id: str, model_id: str, out_path: Path, **voice_settings: Any) -> Path:
        chunks = self._call(
            self._sdk.text_to_speech.convert,
            voice_id=voice_id,
            text=text[:5000],
            model_id=model_id,
            output_format="mp3_44100_128",
            voice_settings=voice_settings or None,
        )
        out_path.write_bytes(b"".join(chunks))
        return out_path

    def compose_music(
        self,
        prompt: str,
        out_path: Path,
        length_ms: int = 30000,
        instrumental: bool = False,
    ) -> Path:
        """Generate a song with Eleven Music and save the returned MP3."""
        chunks = self._call(
            self._sdk.music.compose,
            prompt=prompt[:4100],
            music_length_ms=max(3000, min(int(length_ms), 600000)),
            model_id="music_v2",
            force_instrumental=bool(instrumental),
        )
        out_path.write_bytes(b"".join(chunks))
        return out_path
