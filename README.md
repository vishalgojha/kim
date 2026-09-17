# Kim

Kim is a consumer-friendly, agentic layer for your devices. Kim owns the planning loop, tool registry, approvals, memory, and device routing. A configurable LLM powers reasoning; ElevenLabs is optional voice (STT/TTS) only. Kim can:

- **Control the laptop by voice** — run shell commands, files, apps, volume, clipboard, screenshots, notifications. Sensitive commands need a verbal "yes".
- **Handle long-running tasks** — spawn background jobs (`start_task`), monitor logs, cancel them.
- **A coding assistant by voice** — hands off real coding work to the local `opencode` CLI.
- **Watch & react in the background** — battery/disk/directory monitors and scheduled reminders that speak up proactively (no mic needed in `watch` mode).
- **Work like a research agent** — private knowledge spaces index documents and URLs, while `deep_research` searches and fetches multiple sources for cited synthesis.

```
 you ──mic──▶ ┌───────────────────────────────────────────────────┐
              │                 Kim (this repo)                 │
              │                                                   │
 speech ◀──── │  AudioIn (parec) ─▶ ElevenLabs Conversational Agent◀── brain (LLM+ASR+TTS)
              │                      ▲                            │
              │            client_tool_call │                    │
              │                      └──▶ local tools            │
              │                            ├─ shell / files      │
              │         proactive alerts ── ┼─ tasks / scheduler  │
              │              (watcher+REST │─ opencode / web      │
              │               TTS + speaker)└─ apps / system      │
              └───────────────────────────────────────────────────┘
```

## Quickstart

```bash
cd ~/aurora
./run.sh setup        # 1. configures optional ElevenLabs voice
./run.sh test         # 2. connectivity + tool inventory check
./run.sh voice        # 3. live: just talk. Ctrl-C to stop
./run.sh watch        # background-only mode (alerts + schedules, no mic)
```

For login autostart, use `./.venv/bin/python -m aurora install`. Linux uses the existing systemd setup; macOS installs a LaunchAgent; Windows installs a Startup-folder launcher.

> **`run.sh` is for Linux/macOS.** `run.bat` is the equivalent Windows launcher (`run.bat setup`, `run.bat voice`, …) — Windows batch files don't run on Linux, so use `./run.sh` here.

On first run you need your key:

```bash
cp .env.example .env        # then edit .env → ELEVENLABS_API_KEY=sk_...
```

For Kim's hosted text brain, add `KIM_LLM_API_KEY` to Coolify. The default
endpoint uses Gemini's OpenAI-compatible API; `KIM_LLM_BASE_URL` and
`KIM_LLM_MODEL` can point to another compatible provider. Web and Android use
`/v1/chat`; side effects always enter Kim's approval queue.

For text-based tool calling, `SARVAM_API_KEY` (model `sarvam-105b` at
`https://api.sarvam.ai`) is used as an automatic fallback whenever the primary
brain above is unconfigured or errors. It goes through the exact same
approval/tool-calling pipeline. Voice always uses ElevenLabs; `/v1/chat` text
prefers the Sarvam/default text brain unless `KIM_TEXT_BRAIN=elevenlabs`.

For computer-agent desktop control, `computer_action` drives the laptop like a
desktop agent: `see`/`describe` read the screen (multimodal vision description
when configured, OCR otherwise), `ocr` returns raw text, `click_label` clicks
what it sees, plus mouse move/click/drag/scroll, typing, hotkeys, and window
list/activate/move. It verifies in a loop — after each action it re-reads the
screen before continuing or retrying, up to `KIM_MAX_TOOL_STEPS` (default 20).
Install once on the laptop: `sudo apt install -y tesseract-ocr xdotool`
(Wayland input injection: `sudo apt install -y ydotool` + `sudo systemctl
enable --now ydotool`). To enable vision descriptions, set `KIM_VISION_API_KEY`
(and `KIM_VISION_BASE_URL`/`KIM_VISION_MODEL` for a custom endpoint) or the
primary brain key; the Sarvam v2 beta `gemma4` model (`/v2/chat/completions`)
supports image input for this too.

Prereqs: Python 3.11+, `ffmpeg`, and a working audio device. Linux can use `parec`/PulseAudio; macOS and Windows use the `sounddevice` fallback. `opencode` is optional, for the coding tool.

## Floating opencode tool

Kim is installed as a **global custom tool** in opencode, so any agent session in any project can use it:

- `~/.config/opencode/tools/aurora.ts` — registers the floating `aurora` tool
  - `aurora say <text>` — the laptop **speaks** the text aloud (TTS + playback, no voice session needed)
  - `aurora watch` — launch the background watcher detached
  - `aurora voice` — launch the interactive voice session detached
  - `aurora setup` / `aurora test` — sync the agent / run the connectivity self-test

Restart opencode after adding it. In any conversation, just ask e.g. *"use the aurora tool to tell me on the speakers that lunch is ready"*.

## WhatsApp access

Kim can search the local WhatsApp message index and send messages through the linked local bridge. Sending is confirmation-gated: Kim drafts the recipient/message first and only sends after an explicit `confirm: yes`.

```bash
export WHATSAPP_DB_PATH="$HOME/whatsapp-mcp/whatsapp-bridge/store/messages.db"
```

Kim provides `whatsapp_search`, `whatsapp_recent`, `whatsapp_chats`, and `whatsapp_send`. The database remains read-only. For a laptop-independent deployment, configure Meta WhatsApp Cloud API secrets in Coolify: `WHATSAPP_CLOUD_API_TOKEN`, `WHATSAPP_CLOUD_PHONE_NUMBER_ID`, and the current Graph version as `WHATSAPP_CLOUD_API_VERSION`.

## Android and cloud mode

The native Android companion at `android/` can connect directly to the hosted control panel. It stores the PIN in encrypted Android preferences, supports background approval notifications, streams voice through the signed ElevenLabs session, and handles read-only tool calls directly. Sensitive actions create an approval; once approved, they execute in the cloud when the relevant cloud connector is configured, otherwise they queue for the laptop relay.

For cloud Gmail and Calendar, Kim uses Nango for connector authentication and its authenticated API proxy. Configure the masked Coolify runtime variables `NANGO_SECRET_KEY`, `NANGO_GMAIL_INTEGRATION_ID`, `NANGO_GMAIL_CONNECTION_ID`, `NANGO_CALENDAR_INTEGRATION_ID`, and `NANGO_CALENDAR_CONNECTION_ID`. Set `NANGO_BASE_URL` only when using a self-hosted Nango instance; it defaults to `https://api.nango.dev`. Nango stores and refreshes provider credentials, while Kim only receives the API response. Enable cloud execution with `KIM_REMOTE_DIRECT_TOOLS=gmail_send,calendar_create,whatsapp_send`; read-only cloud tools are enabled with `KIM_REMOTE_ALLOWED_TOOLS`.

## Example things you can say

- "What's eating my CPU?"
- "Make a folder called work and put a notes file in it"
- "Open the browser and remind me in 20 minutes to take a break"
- "Copy the output of `df -h` to the clipboard"
- "Turn the volume down to 40%"
- "Search the web for the latest elevenlabs pricing"
- "Start `python -m http.server 8000` in the background and keep an eye on its log"
- "Check this repo: fix the failing test in `tests/`" → hands off to `opencode`
- "Stop talking and just watch for battery and disk issues" → `watch` mode

## Tools

`shell`, `files` (read/write/edit/list/search/info), `system_info`, `battery`, `disk_usage`, `running_processes`, `launch_app`/`navigate_browser` (+ known app aliases), `volume_get/set`, `clipboard_get/set`, `screenshot`, `desktop_notify`, `web_search`, `web_fetch`, `deep_research`, `knowledge_ingest`/`knowledge_search`/`knowledge_sources`, `memory_save`/`memory_search`, `start_task`/`task_log`/`list_tasks`/`cancel_task`, `opencode_run`, `schedule_remind`, `schedule_every`, `list_schedules`, `schedule_cancel`.

> Note on the 110s tool window: ElevenLabs caps a client tool response at 120s, so `opencode_run` and `run_shell` are best for short work; longer work should go through `start_task` + `task_log` (which the agent does automatically for long-running commands).

## Safety model

Everything runs under a policy in `config.yaml`:

- `block_patterns` — hard-blocked regardless of anything (`rm -rf /`, `mkfs`, `dd`, etc.). Cannot be bypassed.
- `ask_for` — commands like `sudo` / `apt` need a **verbal "yes"** from you; the agent asks, you confirm, then it retries with `confirm: yes`. It will never retry without your confirmation.
- `default: allow` — everything else runs. Tighten it by listing prefixes, or flip to a deny-by-default mode by moving tools out (ask before changing defaults).

## Phone and web control

Kim includes an opt-in, PIN-authenticated remote control plane for the Android/web client. The hosted deployment runs it on the public HTTPS domain; local deployments bind to localhost by default.

1. Put a private PIN in `.env` as `KIM_REMOTE_PIN=...`.
2. Set `remote.enabled: true` for a local deployment, or run `python -m aurora serve` in the hosted container.
3. Use HTTPS and keep the PIN private. The hosted panel is available at `app.vishalojha.me`.

Endpoints include authenticated status/control, voice-session, read-only tool, approval list/create/approve/reject, and laptop command relay routes. Approval and command state is persisted separately from the audit log; all remote actions are audited in `~/.aurora/remote-audit.jsonl`.

## Configuration

Everything lives in `config.yaml` (created from built-in defaults on first run):

| key | meaning |
| --- | --- |
| `elevenlabs.agent_id` | auto-created & saved on first `setup` |
| `elevenlabs.voice_id` | pick any from your account |
| `elevenlabs.region` | `""` (prod) / `us` / `eu` / `in` / `sg` |
| `elevenlabs.tts_model` | proactive alert voice (`eleven_multilingual_v2`) |
| `elevenlabs.conversational_tts_model` | in-conversation voice (`eleven_flash_v2`) |
| `agent.llm` | legacy voice-agent setting; text reasoning uses `KIM_LLM_*` |
| `agent.first_message` | opening line |
| `agent.prompt` / `prompt_file` | system prompt |
| `audio.*` | mic/sink devices, sample rate |
| `permissions.*` | block/confirm/allow rules |
| `watcher.*` | thresholds + watch_dirs for proactive alerts |

Run `./run.sh setup --reset-agent` to force-recreate the agent when you change tools or prompt.

## Autostart (run at boot, background only)

Kim can auto-start at login with its voice service, microphone setup, and top-center control panel. Install once:

```bash
cp ~/aurora/systemd/aurora-mic.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable aurora-mic
```

```ini
# ~/.config/systemd/user/aurora.service
[Unit]
Description=Kim agentic assistant (watch mode)
After=sound.target

[Service]
Type=simple
WorkingDirectory=/home/USER/aurora
EnvironmentFile=/home/USER/aurora/.env
ExecStart=/home/USER/aurora/.venv/bin/python -m aurora watch
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now aurora
```

Or use the project helper, which also installs the panel autostart entry:

```bash
./service.sh install
./service.sh start
```

## Architecture notes / troubleshooting

- **Realtime voice**: websocket → `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=…` using a signed URL fetched with your API key. Mic is captured as 16 kHz mono PCM via `parec` and sent as base64 `user_audio_chunk`s; agent audio comes back as `pcm_16000` and is piped to the sink through `ffmpeg -f pulse`.
- **Proactive speech** (watcher/scheduler) uses the plain TTS REST endpoint so it works even when no voice session is open.
- **Agent recreated/updated automatically on startup** so tool definitions and your prompt always match local code. If you see API errors about tools, run `./run.sh setup --reset-agent`.
- **Mic silent?** `pactl info | grep -i "default source"`; set `audio.input_device` to a specific source from `pactl list sources short`.
- **No sound?** Verify `ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -f pulse default` plays. Set `audio.output_device` to a sink name from `pactl list sinks short`.
- **Reconnects**: the voice session auto-reconnects with backoff if the socket drops.
- This is a local tool with real CPU/disk/network access — treat it like root access. Run `./run.sh test` to see exactly which tools the agent gets.

## Project layout

```
aurora/
  main.py        CLI (voice / watch / setup / test)
  realtime.py    websocket voice session + client-tool execution
  eleven.py      REST client (create/update agent, TTS, signed URL, voices)
  audio.py       parec capture + ffmpeg/pulse playback
  speak.py       proactive TTS-to-speaker queue
  watcher.py     battery/disk/dir monitors → proactive alerts
  permissions.py safety policy engine
  tools/         the 30 client tools the agent can call
```
