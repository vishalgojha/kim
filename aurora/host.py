"""Small cross-platform adapters for desktop integration."""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict


SYSTEM = platform.system().lower()


def open_default(target: str) -> bool:
    """Open a URL/file with the operating system's default handler."""
    try:
        if SYSTEM == "windows":
            os.startfile(target)  # type: ignore[attr-defined]
        elif SYSTEM == "darwin":
            subprocess.Popen(["open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(target)
        return True
    except Exception:
        return False


def notify(title: str, body: str, urgency: str = "normal") -> str:
    """Best-effort desktop notification on the current operating system."""
    try:
        if SYSTEM == "darwin":
            script = f'display notification {body!r} with title {title!r}'
            subprocess.run(["osascript", "-e", script], timeout=10, check=True)
        elif SYSTEM == "windows":
            # Works without an extra package on Windows PowerShell.
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime];"
                f"$xml = [Windows.Data.Xml.Dom.XmlDocument]::new(); $xml.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>{title}</text><text>{body[:1000]}</text></binding></visual></toast>\");"
                "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Kim'); $n.Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=10, check=True)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "-u", urgency, title, body[:1000]], timeout=10, check=True)
        else:
            return "no desktop notification backend available"
        return f"notification sent: {title}"
    except Exception as e:
        return f"notification failed: {e}"


def battery() -> Dict[str, Any]:
    """Return battery information using psutil where available."""
    try:
        import psutil

        b = psutil.sensors_battery()
        if b is not None:
            status = "charging" if b.power_plugged else "discharging"
            return {"present": "yes", "percent": f"{b.percent:.0f}%", "status": status}
    except Exception:
        pass
    return {"present": "no", "percent": "n/a", "status": "n/a"}


async def open_default_async(target: str) -> bool:
    return await asyncio.to_thread(open_default, target)


def install_autostart(project_root: Path) -> str:
    """Install the voice service in the current user's login startup."""
    command = [sys.executable, "-m", "aurora", "voice"]
    if SYSTEM == "darwin":
        launch_dir = Path.home() / "Library" / "LaunchAgents"
        launch_dir.mkdir(parents=True, exist_ok=True)
        plist = launch_dir / "com.kim.assistant.plist"
        args = "".join(f"<string>{x}</string>" for x in command)
        plist.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<plist version=\"1.0\"><dict>"
            "<key>Label</key><string>com.kim.assistant</string>"
            f"<key>ProgramArguments</key><array>{args}</array>"
            f"<key>WorkingDirectory</key><string>{project_root}</string>"
            "<key>RunAtLoad</key><true/>"
            "<key>KeepAlive</key><true/>"
            "</dict></plist>"
        )
        return f"installed macOS LaunchAgent: {plist}"
    if SYSTEM == "windows":
        startup = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        startup.mkdir(parents=True, exist_ok=True)
        bat = startup / "Kim.bat"
        bat.write_text(f'@echo off\ncd /d "{project_root}"\n"{sys.executable}" -m aurora voice\n')
        return f"installed Windows startup launcher: {bat}"
    return "Linux autostart uses ./service.sh install"
