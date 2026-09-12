import asyncio
import logging
import secrets
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, Dict

from .audio import AudioOutput
from .eleven import ElevenAPI

log = logging.getLogger("aurora.speak")


class Speaker:
    """Converts text to speech via REST TTS and plays it on the speakers.

    Used for proactive alerts (watcher, scheduler). The realtime voice session
    uses the conversational agent's own TTS instead.
    """

    def __init__(self, cfg: Dict[str, Any], eleven: ElevenAPI, output: AudioOutput):
        self.cfg = cfg
        self.eleven = eleven
        self.output = output
        self.voice_id = cfg["elevenlabs"].get("voice_id") or "cjVigY5qzO86Huf0OWal"
        self.model_id = cfg["elevenlabs"].get("tts_model") or "eleven_multilingual_v2"
        vs = cfg.get("tts", {})
        self.voice_settings = {
            "stability": vs.get("stability", 0.5),
            "similarity_boost": vs.get("similarity_boost", 0.8),
            "speed": vs.get("speed", 1.0),
        }
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def enqueue(self, text: str) -> None:
        text = text.strip()
        if text:
            await self._queue.put(text[:2000])

    async def _worker_loop(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                await self.speak(text)
            except Exception as e:  # noqa: BLE001
                log.error("speak failed: %s", e)

    async def speak(self, text: str) -> None:
        path = Path(tempfile.gettempdir()) / f"kim_{secrets.token_hex(4)}.mp3"
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.eleven.tts(
                    text, self.voice_id, self.model_id, path, **self.voice_settings
                ),
            )
            await self.output.play_mp3(path)
        except Exception as e:  # noqa: BLE001
            log.error("tts/playback error: %s", e)
        finally:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
