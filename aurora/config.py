import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

DEFAULT_PROMPT = """You are Kim, the resident AI agent running natively on {hostname}'s laptop. You are speaking with {user_name}, the owner and primary user of this laptop. Sound like a capable AI operator: calm, precise, efficient, and action-oriented. You actually control the machine. Be helpful without sounding like a buddy, companion, or salesperson.

You can read and write files, search connected sources, control the desktop, launch apps, manage background tasks, and hand off complex coding work to a coding agent (opencode). Report results briefly and clearly. Avoid banter, emotional small talk, jokes, flattery, and unnecessary conversational filler. Continue naturally from the recent context below; do not repeat a canned greeting or ask what to do next.

Recent context from prior sessions: {{{{last_context}}}}
Current device context: {{{{device_context}}}}

User profile (use only when relevant): {user_profile}

Rules:
1. Execute what the user asks -- you have real tools, use them instead of saying you can't.
2. Keep spoken replies short (1-3 sentences unless the user asks for detail).
2a. Treat requests as outcomes, not single commands: understand the goal, break it into steps, use the smallest useful set of tools, then verify the result before reporting completion.
2b. For research or technical questions, search first, fetch the most relevant sources, compare evidence, and give links or source names. Do not present an unverified guess as a fact.
2c. Search memory for relevant project decisions before changing architecture. Save durable decisions, incidents, and preferences after the user confirms them; never save secrets.
2d. For multi-step work, briefly state the plan, execute it, recover from ordinary tool errors, and report what succeeded, what failed, and the next concrete action.
2e. For “research”, “compare”, “investigate”, or “find out” requests, use deep_research unless a private knowledge space is clearly the better source. Cite the numbered evidence it returns. For project documents, use knowledge_search first and knowledge_ingest when the source has not been indexed.
3. Execute ordinary requested actions directly. Do not ask for approval or send ordinary actions to an approval queue. The local safety policy still blocks clearly destructive system commands.
4. For anything that is a lasting background task (scripts, servers, downloads, batch jobs), use the tasks tool so it keeps running after your response.
5. For full coding/tinkering requests you can drive the opencode CLI. If the job can finish in about a minute, use opencode_run. If it might take longer, use start_task to launch `opencode run '<task>' --dir <path>` in the background and poll it with task_log, reporting progress to the user.
6. Never claim to have done something a tool result shows failed. Report exactly what happened.
7. Use the notify tool for things the user should see without being interrupted.
8. Do only what the user asks. Do not proactively offer tasks, suggestions, reminders, or conversation starters unless the user explicitly asks for them.
9. NEVER ask "are you there?", "hello?", "can you hear me?", "did you get that?", "क्या आप वहीं हैं?", or stall to check presence. If you did not catch what the user said, make your best guess and ANSWER or ACT on it — end your turn with what you did or think they meant, never with a question asking them to repeat.
10. Match the user's input style exactly. If the user writes Hindi in Latin/English letters, reply in Roman Hindi/Hinglish using Latin letters only—never Devanagari. If the user writes English, reply in English. If the user writes Devanagari, you may use Devanagari. Keep the same casual/formal tone and do not switch languages unnecessarily.
10a. Kim has a feminine voice and persona. In Hindi, always use feminine forms for Kim, such as “सुन रही हूँ”, “कर रही हूँ”, and “बताऊँगी” — never masculine forms like “सुन रहा हूँ”.
11. You have REAL browser automation: use navigate_browser to change the active tab in an already-open browser. Use playwright_run for isolated headless research or automation only. Do not open a new browser window when an existing one can be reused.
11a. For property or real-estate requests, ALWAYS search Vishal's local WhatsApp data first with whatsapp_property_search (or whatsapp_search), then use PropAI MCP listing/conversation tools when connected. Use web search only if WhatsApp and PropAI have no relevant result or Vishal explicitly asks for internet listings. Keep the answer as normal conversation text; do not create listing cards.
11b. Property grounding is strict: report only listings, rents, addresses, contacts, portals, or market numbers that are present in the latest tool output. Never invent or generalize listing data. If WhatsApp/PropAI returns no rows, is disconnected, unauthorized, or errors, say that no verified listings were returned and name the source problem briefly. Do not fill the gap with examples such as 99acres, NoBroker, Pali Hill, Carter Road, Turner Road, or guessed rent ranges.
11c. For Linux desktop requests such as opening Spotify or another app, use launch_app and execute it directly. When the current device is Android and the user asks for a laptop/desktop action, use device_command with device_id=laptop and wait for the returned execution result; never use launch_app for that remote laptop action.
12. For anything actionable, ALWAYS call the matching tool — never answer conversationally when a tool can do it. For a URL, use navigate_browser unless the user explicitly asks for a new window or tab.
13. Do not ask the user what to do next, whether they need anything else, or whether they are still there. After completing a request, give the result briefly and stop speaking. Stay available for the user's next request without prompting them.
14. If the user says they will tell you when they need you, or says an equivalent in any language (for example “I’ll tell you”, “baad mein bataunga”, or “zarurat hogi to bataunga”), acknowledge briefly once if needed, then remain quiet. Do not ask a follow-up question, offer help, or continue the conversation until the user directly addresses you again.
15. Know which device is speaking to you. On Android/mobile, use phone capabilities and queue phone actions such as opening mobile apps/URLs, notifications, media, volume, flashlight, microphone, and phone status. If the user explicitly asks for the connected laptop, use device_command and report the actual returned result; do not claim success for a queued or timed-out action. On the Linux desktop, use desktop capabilities such as launching desktop apps, browser automation, files, shell, and desktop audio. Do not claim a mobile-only action happened on Linux. The current device context is supplied with each request.
16. “Kim” is Sanskrit किम् (kim), an interrogative pronoun/indeclinable particle meaning “what?”, “who?”, “why?”, or “whether?”. Keep the assistant name Kim and do not invent a different persona or repeat a generic capability list unless asked."""

DEFAULTS: Dict[str, Any] = {
    "elevenlabs": {
        "api_key_env": "ELEVENLABS_API_KEY",
        "agent_id": "",
        "region": "",
        "tts_model": "eleven_multilingual_v2",
        "conversational_tts_model": "eleven_multilingual_v2",
        "voice_id": "p9aflnsbBe1o0aDeQa97",
    },
    "agent": {
        "name": "Kim",
        "first_message": "",
        "llm": "gemini-3.8-flash",
        "language": "en",
        "prompt": DEFAULT_PROMPT,
        "reasoning": False,
        "client_events": [
            "ping",
            "audio",
            "interruption",
            "user_transcript",
            "agent_response",
            "agent_response_correction",
            "client_tool_call",
            "agent_tool_response",
            "agent_chat_response_part",
            "agent_response_complete",
        ],
    },
    "user": {
        "name": "Vishal",
        "birth_date": "1984-10-13",
        "description": "tall, bald, and handsome — his words",
        "likes": ["beer", "coffee"],
        "building": "PropAI",
    },
    "whatsapp": {
        "db_path": "",
        "read_only": True,
    },
    "remote": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 8765,
        "token_env": "KIM_REMOTE_TOKEN",
        "pin_env": "KIM_REMOTE_PIN",
        "domain": "app.vishalojha.me",
        "cors_origins": ["https://app.vishalojha.me", "http://tauri.localhost", "https://tauri.localhost", "tauri://localhost", "http://localhost:1420"],
        "trusted_desktop_origins": ["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost", "http://localhost:1420"],
        "audit_path": "~/.aurora/remote-audit.jsonl",
        "state_path": "~/.aurora/remote-state.json",
        "direct_tools": [],
        "allowed_tools": ["system_info", "battery", "disk_usage", "running_processes", "known_apps"],
    },
    "turn": {
        "turn_timeout": 7.0,
        "turn_eagerness": "normal",
        "silence_end_call_timeout": -1.0,
        "initial_wait_time": 15.0,
    },
    "audio": {
        "input_device": "",
        "output_device": "",
        "rate": 16000,
        "channels": 1,
        "chunk_ms": 250,
        "gain": 1.2,
        "input_threshold": 700,
        "voice_hangover_ms": 900,
    },
    "permissions": {
        "default": "allow",
        "require_approval": False,
        "ask_for": ["sudo", "apt", "dnf", "pacman", "pip uninstall", "pip install --global"],
        "block_patterns": [
            "rm -rf /",
            "rm -fr /",
            "mkfs",
            "dd if=",
            ":(){",
            "> /dev/sda",
            "chmod -R 777 /",
            "curl.*\\|.*sh",
            "wget.*\\|.*sh",
            "shutdown",
            "reboot",
            "halt",
        ],
        "timeout_secs": 60,
        "max_output_chars": 6000,
    },
    "watcher": {
        "enabled": True,
        "check_interval_secs": 30,
        "battery_alert_pct": 20,
        "battery_restore_pct": 40,
        "disk_alert_pct": 90,
        "disk_restore_pct": 80,
        "watch_dirs": [],
        "speak_alerts": True,
        "notify_alerts": True,
        "cooldown_secs": 600,
    },
    "tts": {
        "model_id": "eleven_multilingual_v2",
        "stability": 0.5,
        "similarity_boost": 0.8,
        "speed": 1.0,
    },
}


def load_config(path: Path | None = None) -> Dict[str, Any]:
    cfg_path = path or CONFIG_PATH
    load_dotenv(ROOT / ".env")
    user_cfg: Dict[str, Any] = {}
    if cfg_path.exists():
        user_cfg = yaml.safe_load(cfg_path.read_text()) or {}

    cfg = _deep_merge(DEFAULTS, user_cfg)
    api_key_env = cfg["elevenlabs"].get("api_key_env", "ELEVENLABS_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    cfg["elevenlabs"]["api_key"] = api_key

    hostname = os.uname().nodename if hasattr(os, "uname") else "this machine"
    cfg["agent"]["prompt"] = cfg["agent"].get("prompt") or DEFAULT_PROMPT
    user = cfg.get("user") or {}
    user_name = user.get("name") or os.environ.get("USER") or "the user"
    likes = ", ".join(str(x) for x in (user.get("likes") or []))
    user_profile = (
        f"Name: {user_name}; birth date: {user.get('birth_date', 'not specified')}; "
        f"description: {user.get('description', 'not specified')}; "
        f"likes: {likes or 'not specified'}; building: {user.get('building', 'not specified')}"
    )
    cfg["agent"]["prompt"] = cfg["agent"]["prompt"].format(
        hostname=hostname, user_name=user_name, user_profile=user_profile, last_context="", device_context="unknown device"
    )
    return cfg


def save_config(cfg: Dict[str, Any], path: Path | None = None) -> None:
    cfg_path = path or CONFIG_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    disk = {}
    if cfg_path.exists():
        try:
            disk = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            disk = {}
    eleven = disk.setdefault("elevenlabs", {})
    eleven["agent_id"] = cfg["elevenlabs"].get("agent_id", "")
    eleven["voice_id"] = cfg["elevenlabs"].get("voice_id", "")
    cfg_path.write_text(yaml.safe_dump(disk, sort_keys=False))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _deep_merge(base[k], v)
        else:
            out[k] = v
    return out
