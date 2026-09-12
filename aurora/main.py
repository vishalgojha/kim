import argparse
import asyncio
import logging
import signal
import sys
from typing import Any, Dict, Optional

from .audio import AudioCapture, AudioOutput
from .config import ROOT, load_config, save_config
from .eleven import ElevenAPI, ElevenError
from .log import get_logger, setup_logging
from .permissions import Policy
from .realtime import RealtimeSession
from .speak import Speaker
from .tools import REGISTRY
from .tools.context import Notifier, get_ctx, set_speaker
from .watcher import Watcher
from .host import install_autostart

log = get_logger("aurora")


def ensure_agent(cfg: Dict[str, Any], eleven: ElevenAPI, reset: bool = False) -> str:
    tools = REGISTRY.client_schemas()
    log.info("syncing %d client tools via toolbox endpoint...", len(tools))
    tool_ids = eleven.sync_tools(tools)
    log.info("%d tool_id(s) wired", len(tool_ids))
    agent_id = (cfg.get("elevenlabs") or {}).get("agent_id") or ""
    exists = False
    if agent_id and not reset:
        try:
            eleven.get_agent(agent_id)
            exists = True
        except ElevenError:
            exists = False

    if agent_id and exists:
        log.info("updating existing agent %s", agent_id)
        eleven.update_agent(agent_id, cfg, tool_ids)
        return agent_id

    log.info("creating agent (%d client tools)...", len(tool_ids))
    agent_id = eleven.create_agent(cfg, tool_ids)
    cfg["elevenlabs"]["agent_id"] = agent_id
    save_config(cfg)
    log.info("agent created: %s", agent_id)
    return agent_id


def _build_context(cfg: Dict[str, Any]) -> Notifier:
    notifier = Notifier()
    get_ctx().update({"cfg": cfg, "policy": Policy(cfg), "notifier": notifier})
    return notifier


def _setup_objects(cfg: Dict[str, Any]):
    eleven = ElevenAPI(cfg)
    notifier = _build_context(cfg)
    capture = AudioCapture(
        rate=cfg["audio"]["rate"],
        channels=cfg["audio"]["channels"],
        device=cfg["audio"].get("input_device", ""),
    )
    output = AudioOutput(
        rate=cfg["audio"]["rate"],
        channels=cfg["audio"]["channels"],
        device=cfg["audio"].get("output_device", ""),
        gain=float(cfg["audio"].get("gain", 1.0) or 1.0),
    )
    return eleven, notifier, capture, output


async def run_voice(cfg: Dict[str, Any]) -> None:
    eleven, notifier, capture, output = _setup_objects(cfg)
    speaker = Speaker(cfg, eleven, output)
    speaker.start()
    set_speaker(speaker.enqueue)

    watcher = Watcher(cfg, notifier)
    session = RealtimeSession(cfg, eleven, REGISTRY, capture, output)

    tasks = [
        asyncio.create_task(session.run_forever(), name="voice"),
        asyncio.create_task(watcher.run(), name="watcher"),
    ]
    print("Kim voice agent starting. Ctrl-C to stop.")
    stop_ev = asyncio.Event()

    def _on_sigint() -> None:
        stop_ev.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _on_sigint)
    except (NotImplementedError, RuntimeError):
        pass
    try:
        await stop_ev.wait()
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await speaker.stop()
        await capture.stop()
        await output.close()


async def run_watch(cfg: Dict[str, Any]) -> None:
    eleven, notifier, capture, output = _setup_objects(cfg)
    speaker = Speaker(cfg, eleven, output)
    speaker.start()
    set_speaker(speaker.enqueue)
    watcher = Watcher(cfg, notifier)
    log.info("watcher-only mode (microphone off)")
    try:
        await watcher.run()
    finally:
        await watcher.stop()
        await speaker.stop()
        await capture.stop()
        await output.close()


async def run_selftest(cfg: Dict[str, Any]) -> int:
    eleven = ElevenAPI(cfg)
    print(f"region: {cfg['elevenlabs'].get('region') or 'production'} @ {eleven.base_url}")
    if not cfg["elevenlabs"]["api_key"]:
        print("ELEVENLABS_API_KEY is not set (see .env)")
        return 1
    try:
        voices = eleven.list_voices()
        print(f"voices: {len(voices)} available")
        print("  " + ", ".join(v["name"] for v in voices[:5]))
        print(f"tools registered: {len(REGISTRY.names())}")
        for n in REGISTRY.names():
            print(f"  - {n}")
        agent_id = cfg["elevenlabs"].get("agent_id")
        if agent_id:
            eleven.get_agent(agent_id)
            print(f"agent: {agent_id} ok")
    except ElevenError as e:
        print(f"error: {e}")
        return 1
    return 0


async def run_say(cfg: Dict[str, Any], text: str) -> None:
    eleven = ElevenAPI(cfg)
    output = AudioOutput(
        rate=cfg["audio"]["rate"],
        channels=cfg["audio"]["channels"],
        device=cfg["audio"].get("output_device", ""),
        gain=float(cfg["audio"].get("gain", 1.0) or 1.0),
    )
    speaker = Speaker(cfg, eleven, output)
    await speaker.speak(text)
    await output.close()


def run_wake() -> int:
    import subprocess

    script = ROOT / "service.sh"
    if not script.exists():
        print("service.sh not found; run 'python -m aurora voice' directly instead (the package path is kept for compatibility).")
        return 1
    res = subprocess.run([str(script), "wake"], capture_output=True, text=True)
    out = (res.stdout or "").strip() or (res.stderr or "").strip()
    print(out)
    return res.returncode


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="kim", description="Kim: an ElevenLabs-voiced agentic laptop assistant")
    ap.add_argument("mode", nargs="?", default="voice", choices=["voice", "watch", "setup", "test", "say", "wake", "install"])
    ap.add_argument("text", nargs="*", help="for 'say': the words to speak aloud")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    ap.add_argument("--reset-agent", action="store_true", help="force-recreate the ElevenLabs agent")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    cfg = load_config(args.config)

    if args.mode == "install":
        print(install_autostart(ROOT))
        return 0

    if args.mode == "say":
        text = " ".join(args.text).strip()
        if not text:
            print("usage: python -m aurora say <text to speak>")
            return 1
        try:
            asyncio.run(run_say(cfg, text))
        except ElevenError as e:
            print(f"failed: {e}")
            return 1
        return 0

    if args.mode == "setup":
        print("tools:", ", ".join(REGISTRY.names()))
        eleven = ElevenAPI(cfg)
        try:
            agent_id = ensure_agent(cfg, eleven, reset=args.reset_agent)
        except ElevenError as e:
            print(f"failed: {e}")
            return 1
        print(f"agent_id: {agent_id}")
        print("\nnow run:  python -m aurora voice")
        return 0

    if args.mode == "wake":
        return run_wake()

    if args.mode == "test":
        return asyncio.run(run_selftest(cfg))

    try:
        ensure_agent(cfg, ElevenAPI(cfg), reset=args.reset_agent)
    except ElevenError as e:
        print(f"agent setup failed: {e}")
        return 1
    ElevenAPI(cfg).close()

    try:
        if args.mode == "watch":
            asyncio.run(run_watch(cfg))
        else:
            asyncio.run(run_voice(cfg))
    except KeyboardInterrupt:
        print("\nbye.")
    except Exception as e:  # noqa: BLE001
        log.error("fatal: %s", e)
        import traceback

        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
