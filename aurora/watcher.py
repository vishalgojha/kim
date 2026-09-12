import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from .tools.context import Notifier
from .host import battery as host_battery
from .host import notify as host_notify

log = logging.getLogger("aurora.watcher")


class Watcher:
    """Background monitor: battery, disk, and watched directories.

    On a state change it notifies (desktop) and speaks (TTS) proactively.
    """

    def __init__(self, cfg: Dict[str, Any], notifier: Notifier):
        w = cfg.get("watcher", {})
        self.enabled = w.get("enabled", True)
        self.interval = max(5, int(w.get("check_interval_secs", 30)))
        self.battery_alert = float(w.get("battery_alert_pct", 20))
        self.battery_restore = float(w.get("battery_restore_pct", 40))
        self.disk_alert = float(w.get("disk_alert_pct", 90))
        self.disk_restore = float(w.get("disk_restore_pct", 80))
        self.speak_alerts = w.get("speak_alerts", True)
        self.notify_alerts = w.get("notify_alerts", True)
        self.cooldown = max(0, int(w.get("cooldown_secs", 600)))
        watch = [str(x) for x in w.get("watch_dirs", [])]
        self.watch_dirs: List[Path] = [Path(x).expanduser() for x in watch if x]
        self.notifier = notifier

        self._battery_low = False
        self._disk_high = False
        self._dir_snapshots: Dict[str, Dict[str, int]] = {}
        self._last_alert: Dict[str, float] = {}
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def _cooldown_ok(self, key: str) -> bool:
        now = time.monotonic()
        if now - self._last_alert.get(key, 0) < self.cooldown:
            return False
        self._last_alert[key] = now
        return True

    async def _alert(self, key: str, text: str, title: str) -> None:
        if not self._cooldown_ok(key):
            return
        log.info("alert [%s]: %s", key, text)
        if self.notify_alerts:
            try:
                await self._notify_os(title, text)
            except Exception as e:  # noqa: BLE001
                log.error("notify failed: %s", e)
        if self.speak_alerts:
            await self.notifier.speak(text)

    @staticmethod
    async def _notify_os(title: str, body: str) -> None:
        await asyncio.to_thread(host_notify, title, body, "critical" if len(body) < 200 else "normal")

    # --------------------------------------------------------------- checks
    def _battery_pct(self) -> float | None:
        portable = host_battery()
        if portable.get("percent") not in (None, "n/a"):
            return float(str(portable["percent"]).rstrip("%"))
        base = "/sys/class/power_supply"
        if not base:
            return None
        import os

        try:
            bats = [d for d in os.listdir(base) if d.startswith("BAT")]
            if not bats:
                return None
            cap = Path(os.path.join(base, bats[0], "capacity")).read_text().strip()
            return float(cap)
        except Exception:
            return None

    def _disk_pct(self) -> float | None:
        try:
            du = shutil.disk_usage("/")
            return du.used / du.total * 100
        except Exception:
            return None

    async def check_battery(self) -> None:
        pct = self._battery_pct()
        if pct is None:
            return
        if pct <= self.battery_alert and not self._battery_low:
            self._battery_low = True
            await self._alert("battery", f"Battery is at {pct:.0f} percent. Consider plugging in.", "Kim: battery low")
        elif self._battery_low and pct >= self.battery_restore:
            self._battery_low = False

    async def check_disk(self) -> None:
        pct = self._disk_pct()
        if pct is None:
            return
        if pct >= self.disk_alert and not self._disk_high:
            self._disk_high = True
            await self._alert("disk", f"Disk is {pct:.0f} percent full. You might want to clean up.", "Kim: disk full")
        elif self._disk_high and pct <= self.disk_restore:
            self._disk_high = False

    async def check_dirs(self) -> None:
        for d in self.watch_dirs:
            if not d.is_dir():
                continue
            try:
                snapshot = {
                    str(p.relative_to(d)): p.stat().st_size for p in d.rglob("*") if p.is_file()
                }
            except Exception:
                continue
            prev = self._dir_snapshots.get(str(d))
            self._dir_snapshots[str(d)] = snapshot
            if prev is None:
                continue
            new_files = [(k, v) for k, v in snapshot.items() if k not in prev]
            changed = [(k, v) for k, v in snapshot.items() if k in prev and prev[k] != v]
            if new_files or changed:
                names = ", ".join(
                    [f for f, _ in (new_files[:3] + changed[:3])]
                )
                await self._alert(
                    "dir_" + str(d),
                    f"New or changed files in {d}: {names if names else 'something'}.",
                    f"Kim: files changed in {d.name}",
                )

    async def check_once(self) -> None:
        await self.check_battery()
        await self.check_disk()
        await self.check_dirs()

    async def run(self) -> None:
        if not self.enabled:
            log.info("watcher disabled in config")
            return
        log.info("watcher started (interval=%ss, battery<%s%%, disk>%s%%)", self.interval, self.battery_alert, self.disk_alert)
        while self._running:
            try:
                await self.check_once()
            except Exception as e:  # noqa: BLE001
                log.error("watcher check error: %s", e)
            await asyncio.sleep(self.interval)
