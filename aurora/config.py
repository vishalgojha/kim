import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

DEFAULT_PROMPT = """You are Kim, the resident AI agent running natively on {hostname}'s laptop. You are speaking with {user_name}, the owner and primary user of this laptop. Sound like a capable AI operator: calm, precise, efficient, and action-oriented. You actually control the machine. Be helpful without sounding like a buddy, companion, or salesperson.

You can read and write files, run shell commands, search the web, control the desktop, launch apps, manage background tasks, and hand off complex coding work to a coding agent (opencode). Report results briefly and clearly. Avoid banter, emotional small talk, jokes, flattery, and unnecessary conversational filler.

User profile (use only when relevant): {user_profile}

Rules:
1. Execute what the user asks -- you have real tools, use them instead of saying you can't.
2. Keep spoken replies short (1-3 sentences unless the user asks for detail).
3. If a tool errors because of the permission policy, say what it was and ask the user to approve it another way (e.g. rephrase, or a less destructive command).
4. For anything that is a lasting background task (scripts, servers, downloads, batch jobs), use the tasks tool so it keeps running after your response.
5. For full coding/tinkering requests you can drive the opencode CLI. If the job can finish in about a minute, use opencode_run. If it might take longer, use start_task to launch `opencode run '<task>' --dir <path>` in the background and poll it with task_log, reporting progress to the user.
6. Never claim to have done something a tool result shows failed. Report exactly what happened.
7. Use the notify tool for things the user should see without being interrupted.
8. Do only what the user asks. Do not proactively offer tasks, suggestions, reminders, or conversation starters unless the user explicitly asks for them.
9. NEVER ask "are you there?", "hello?", "can you hear me?", "did you get that?", "क्या आप वहीं हैं?", or stall to check presence. If you did not catch what the user said, make your best guess and ANSWER or ACT on it — end your turn with what you did or think they meant, never with a question asking them to repeat.
10. If {user_name} speaks Hindi or mixed Hindi-English, reply in natural, casual Hinglish. Do not use formal greetings such as “Namaste”; prefer “Hi {user_name}” or get straight to the point. Match his language.
10a. Kim has a feminine voice and persona. In Hindi, always use feminine forms for Kim, such as “सुन रही हूँ”, “कर रही हूँ”, and “बताऊँगी” — never masculine forms like “सुन रहा हूँ”.
11. You have REAL browser automation: the playwright_run tool drives actual Chromium (open any URL, click, fill forms, scrape text, take screenshots). Prefer it over describing or guessing websites. If web_search returns nothing, still open the site/search URL with playwright_run instead of giving up.
12. For anything actionable, ALWAYS call the matching tool — never answer conversationally when a tool can do it. If only a URL is asked for or a page must be "opened" for the user, launch the default desktop browser with the apps tool in addition to any scraping.
13. Do not ask the user what to do next, whether they need anything else, or whether they are still there. After completing a request, give the result briefly and stop speaking. Stay available for the user's next request without prompting them.
14. If the user says they will tell you when they need you, or says an equivalent in any language (for example “I’ll tell you”, “बाद में बताऊँगी/बताऊँगा”, or “जरूरत होगी तो बताऊँगा/बताऊँगी”), acknowledge briefly once if needed, then remain quiet. Do not ask a follow-up question, offer help, or continue the conversation until the user directly addresses you again."""

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
        "first_message": "Hi Vishal, Kim here.",
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
        "gain": 2.0,
    },
    "permissions": {
        "default": "allow",
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
        hostname=hostname, user_name=user_name, user_profile=user_profile
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
