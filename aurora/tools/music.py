import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from ..audio import AudioOutput
from ..eleven import ElevenAPI, ElevenError
from .context import get_ctx
from .registry import tool


MUSIC_DIR = Path.home() / ".aurora" / "music"
_jobs: dict[str, dict[str, Any]] = {}


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
    timeout=20,
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
    job_id = f"music-{uuid.uuid4().hex[:8]}"
    _jobs[job_id] = {"status": "starting", "prompt": prompt[:160], "created": time.time()}
    asyncio.create_task(_generate(job_id, cfg, prompt, duration, instrumental, play))
    return f"music generation started ({job_id}); it will continue in the background and save the result locally"


async def _generate(job_id: str, cfg: dict[str, Any], prompt: str, duration: int, instrumental: bool, play: bool) -> None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    path = MUSIC_DIR / f"kim_song_{int(time.time())}_{job_id}.mp3"
    api = ElevenAPI(cfg)
    _jobs[job_id]["status"] = "generating"
    try:
        await asyncio.to_thread(api.compose_music, prompt, path, duration * 1000, instrumental)
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
        _jobs[job_id].update(status="completed", path=str(path), played=play)
    except ElevenError as error:
        _jobs[job_id].update(status="failed", error=str(error))
    except Exception as error:  # noqa: BLE001
        _jobs[job_id].update(status="failed", error=str(error))
    finally:
        api.close()


@tool(
    "music_status",
    "Check the status of a background Eleven Music generation started by make_song.",
    {"job_id": {"type": "string", "description": "The music job id returned by make_song", "required": True}},
    timeout=10,
)
def music_status(job_id: str) -> str:
    job = _jobs.get(job_id.strip())
    if not job:
        return f"unknown music job: {job_id}"
    return str(job)
