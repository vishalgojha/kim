import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Any, Dict, Optional

from .audio import AudioCapture, AudioOutput
from .config import ROOT, load_config, save_config
from .agent import HOSTED_LAPTOP_TOOLS
from .eleven import ElevenAPI, ElevenError
from .eleven_chat import ElevenTextChat
from .log import get_logger, setup_logging
from .permissions import Policy
from .realtime import RealtimeSession
from .speak import Speaker
from .tools import REGISTRY
from .tools.context import Notifier, get_ctx, set_speaker
from .watcher import Watcher
from .host import install_autostart
from .remote import RemoteServer
from pathlib import Path

log = get_logger("aurora")


def ensure_agent(cfg: Dict[str, Any], eleven: ElevenAPI, reset: bool = False, schemas: Optional[list] = None) -> str:
    tools = schemas if schemas is not None else REGISTRY.client_schemas()
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
    allowed_tools = os.environ.get("KIM_REMOTE_ALLOWED_TOOLS", "").strip()
    if allowed_tools:
        cfg["remote"] = dict(cfg.get("remote") or {})
        cfg["remote"]["allowed_tools"] = [item.strip() for item in allowed_tools.split(",") if item.strip()]
    remote = RemoteServer(
        cfg,
        REGISTRY,
        asyncio.get_running_loop(),
        Path.home() / ".aurora" / "voice_command",
        speaker.speak,
        text_chat=ElevenTextChat(cfg, eleven, REGISTRY),
    )
    remote.start()

    tasks = [
        asyncio.create_task(session.run_forever(), name="voice"),
        asyncio.create_task(watcher.run(), name="watcher"),
        asyncio.create_task(_remote_command_loop(cfg), name="remote-command-relay"),
        asyncio.create_task(_desktop_device_command_loop(cfg), name="desktop-device-relay"),
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
        remote.stop()
        await capture.stop()
        await output.close()


async def _remote_command_loop(cfg: Dict[str, Any]) -> None:
    """Pull commands from the cloud control plane into the local voice session."""
    import httpx

    remote = cfg.get("remote", {})
    domain = str(remote.get("domain", "")).strip().rstrip("/")
    pin_env = str(remote.get("pin_env", "KIM_REMOTE_PIN"))
    pin = os.environ.get(pin_env, "").strip()
    if not domain or not pin:
        log.warning("remote command relay disabled: domain or KIM_REMOTE_PIN missing")
        return
    command_path = Path.home() / ".aurora" / "voice_command"
    async with httpx.AsyncClient(timeout=8.0) as client:
        while True:
            try:
                response = await client.get(f"https://{domain}/v1/commands/next", headers={"X-Kim-Pin": pin})
                if response.is_success:
                    command = (response.json() or {}).get("command") or {}
                    action = command.get("action")
                    if command.get("type") == "control" and action in {"pause", "wake"}:
                        command_path.parent.mkdir(parents=True, exist_ok=True)
                        command_path.write_text(action)
                    elif command.get("type") == "tool":
                        result, is_error = await REGISTRY.run(command.get("name", ""), command.get("parameters", {}))
                        await client.post(
                            f"https://{domain}/v1/commands/{command.get('approval_id', '')}/result",
                            headers={"X-Kim-Pin": pin},
                            json={"result": result, "is_error": is_error},
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("remote command relay unavailable: %s", exc)
            await asyncio.sleep(2)


async def _desktop_device_command_loop(cfg: Dict[str, Any]) -> None:
    """Pull desktop actions from the public Kim relay into this laptop."""
    import httpx

    remote = cfg.get("remote", {})
    domain = str(remote.get("domain", "")).strip().rstrip("/")
    pin_env = str(remote.get("pin_env", "KIM_REMOTE_PIN"))
    pin = os.environ.get(pin_env, "").strip()
    if not domain or not pin:
        log.warning("desktop device relay disabled: domain or KIM_REMOTE_PIN missing")
        return
    device_id = os.environ.get("KIM_DESKTOP_DEVICE_ID", "laptop").strip() or "laptop"
    base = f"https://{domain}"
    capabilities = ["open_url", "open_app", "type_text", "press_key", "screenshot", "playwright_run", "browser_action", "computer_action"]
    async with httpx.AsyncClient(timeout=12.0) as client:
        while True:
            try:
                headers = {"X-Kim-Pin": pin}
                await client.post(
                    f"{base}/v1/device/heartbeat",
                    headers=headers,
                    json={"device_id": device_id, "capabilities": capabilities},
                )
                response = await client.get(
                    f"{base}/v1/device/commands/next",
                    params={"device_id": device_id},
                    headers=headers,
                )
                if response.is_success:
                    command = (response.json() or {}).get("command") or {}
                    if command:
                        action = str(command.get("action", "")).strip().lower()
                        params = command.get("parameters") if isinstance(command.get("parameters"), dict) else {}
                        tool_name = {
                            "open_url": "launch_app",
                            "open_app": "launch_app",
                            "type_text": "type_text",
                            "press_key": "press_key",
                            "screenshot": "screenshot",
                            "playwright_run": "playwright_run",
                            "browser_action": "browser_action",
                            "computer_action": "computer_action",
                        }.get(action)
                        tool_params = params
                        if action == "open_url":
                            tool_params = {"name": params.get("url") or params.get("name") or ""}
                        elif action == "open_app":
                            tool_params = {"name": params.get("name") or params.get("app_name") or params.get("package") or ""}
                        if action in {"open_url", "open_app"} and not tool_params.get("name"):
                            result = f"ERROR: no {'URL' if action == 'open_url' else 'app name'} was provided for {action}; nothing was opened"
                            is_error = True
                        elif tool_name:
                            result, is_error = await REGISTRY.run(tool_name, tool_params)
                        else:
                            result, is_error = f"unsupported desktop action: {action}", True
                        try:
                            from .tools.tasks import record_action
                            await record_action(f"device.{action}", not is_error, str(result)[:200])
                        except Exception:  # noqa: BLE001
                            pass
                        await client.post(
                            f"{base}/v1/device/commands/{command.get('id', '')}/result",
                            headers=headers,
                            json={"action": action, "result": result, "is_error": is_error},
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("desktop device relay unavailable: %s", exc)
            await asyncio.sleep(2)


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


async def run_remote(cfg: Dict[str, Any]) -> None:
    """Run Kim's headless authenticated API for a server/container deployment."""
    cfg = dict(cfg)
    cfg["remote"] = dict(cfg.get("remote") or {})
    cfg["remote"].update({
        "enabled": True,
        "host": os.environ.get("KIM_REMOTE_HOST", "0.0.0.0"),
        "port": int(os.environ.get("KIM_PORT", cfg["remote"].get("port", 3000))),
    })
    # OAuth tokens obtained through the hosted Connect Google flow live beside
    # the persistent remote state, never in the image or source tree.
    os.environ.setdefault("GOOGLE_TOKEN_PATH", str(Path(os.environ.get("KIM_REMOTE_STATE_PATH", "/data/remote-state.json")).expanduser().with_name("google-token.json")))
    allowed_tools = os.environ.get("KIM_REMOTE_ALLOWED_TOOLS", "").strip()
    if allowed_tools:
        cfg["remote"]["allowed_tools"] = [item.strip() for item in allowed_tools.split(",") if item.strip()]
    _build_context(cfg)
    eleven = ElevenAPI(cfg) if cfg["elevenlabs"].get("api_key") and cfg["elevenlabs"].get("agent_id") else None
    hosted_schemas = [s for s in REGISTRY.client_schemas() if s["name"] not in HOSTED_LAPTOP_TOOLS]
    if eleven:
        # Keep the hosted ElevenLabs agent aligned with Kim's local registry and
        # Onyx-like operating prompt. Eleven remains the conversational brain;
        # Kim owns the tools, approvals, memory, and device routing. Laptop-only
        # tools are hidden from the hosted agent: the laptop is reached through
        # device_command so Kim never claims an action that ran in the container.
        try:
            ensure_agent(cfg, eleven, schemas=hosted_schemas)
        except ElevenError:
            log.exception("could not sync hosted ElevenLabs agent; using existing version")
    voice_url = (lambda: eleven.get_signed_url(cfg["elevenlabs"]["agent_id"])) if eleven else None
    prefer_agent = os.environ.get("KIM_TEXT_BRAIN", "agent").strip().lower() != "elevenlabs"
    remote = RemoteServer(
        cfg,
        REGISTRY,
        asyncio.get_running_loop(),
        Path.home() / ".aurora" / "voice_command",
        voice_url=voice_url,
        text_chat=ElevenTextChat(cfg, eleven, REGISTRY, hosted=True) if eleven else None,
        hosted=True,
        prefer_agent=prefer_agent,
    )
    remote.start()
    stop_ev = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_ev.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop_ev.wait()
    finally:
        remote.stop()
        if eleven:
            eleven.close()


async def run_desktop(cfg: Dict[str, Any]) -> None:
    """Run only the laptop command relay; never opens the microphone."""
    _build_context(cfg)
    log.info("desktop relay starting (microphone disabled)")
    await _desktop_device_command_loop(cfg)


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
    ap.add_argument("mode", nargs="?", default="voice", choices=["voice", "watch", "serve", "desktop", "setup", "test", "say", "wake", "install"])
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

    if args.mode == "desktop":
        try:
            asyncio.run(run_desktop(cfg))
        except KeyboardInterrupt:
            pass
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

    if args.mode == "serve":
        try:
            asyncio.run(run_remote(cfg))
        except Exception as e:  # noqa: BLE001
            log.error("remote server failed: %s", e)
            return 1
        return 0

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
