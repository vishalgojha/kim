import asyncio
import time
from pathlib import Path

from ..audio import AudioOutput
from ..eleven import ElevenAPI, ElevenError
from .context import get_ctx
from .registry import tool


MUSIC_DIR = Path.home() / ".aurora" / "music"


@tool(
    "make_song",
    "Generate a song with vocals or an instrumental using Eleven Music, save it locally, and optionally play it. "
    "Use a descriptive prompt with genre, mood, instruments, language, and vocal style.",
    {
        "prompt": {"type": "string", "description": "song description and lyrics/style direction", "required": True},
        "duration_seconds": {"type": "integer", "description": "song length from 3 to 600 seconds (default 30)", "required": False},
        "instrumental": {"type": "boolean", "description": "true for no vocals, false for a sung song", "required": False},
        "play": {"type": "boolean", "description": "play the generated song through the speakers (default true)", "required": False},
    },
    timeout=120,
)
async def make_song(
    prompt: str,
    duration_seconds: int = 30,
    instrumental: bool = False,
    play: bool = True,
) -> str:
    cfg = get_ctx().get("cfg")
    if not cfg:
        return "music unavailable: Kim is not initialized"
    duration = max(3, min(int(duration_seconds), 600))
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    path = MUSIC_DIR / f"kim_song_{int(time.time())}.mp3"
    api = ElevenAPI(cfg)
    try:
        await asyncio.to_thread(api.compose_music, prompt, path, duration * 1000, instrumental)
    except ElevenError as e:
        return f"song generation failed: {e}"
    finally:
        api.close()

    if play:
        output = AudioOutput(
            rate=cfg["audio"]["rate"],
            channels=cfg["audio"]["channels"],
            device=cfg["audio"].get("output_device", ""),
            gain=float(cfg["audio"].get("gain", 1.0) or 1.0),
        )
        try:
            await output.play_mp3(path)
        finally:
            await output.close()
    return f"song ready: {path}" + (" (played)" if play else "")
