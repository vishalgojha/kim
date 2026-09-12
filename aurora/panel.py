#!/usr/bin/python3
"""Kim panel: a small always-on-top button centered below the top bar.
Click it for Wake / Say / Status."""
import os
import subprocess
import sys
import threading

os.environ.setdefault("GDK_BACKEND", "x11")
os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY") or ":0")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

KIM_ROOT = "/home/vishal/aurora"
PY = f"{KIM_ROOT}/.venv/bin/python"


def sh(cmd, timeout=130):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "timed out")


class KimButton(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="Kim")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_default_size(208, 44)
        self.set_position(Gtk.WindowPosition.NONE)

        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.add(self.box)

        self.drag_handle = Gtk.EventBox()
        self.drag_handle.add(Gtk.Label(label="⠿"))
        self.drag_handle.set_tooltip_text("Drag Kim anywhere")
        self.drag_handle.set_size_request(20, -1)
        self.drag_handle.connect("button-press-event", self._start_drag)
        self.box.pack_start(self.drag_handle, False, False, 0)

        self.btn = Gtk.EventBox()
        self.btn_label = Gtk.Label(label="Kim")
        self.btn_label.set_xalign(0.5)
        self.btn.add(self.btn_label)
        self.btn.set_size_request(128, -1)
        self.box.pack_start(self.btn, True, True, 0)
        self.btn.connect("button-press-event", self.on_clicked)

        self.wake_btn = Gtk.EventBox()
        self.wake_btn.add(Gtk.Label(label="▶"))
        self.wake_btn.set_tooltip_text("Wake and listen")
        self.wake_btn.set_size_request(32, -1)
        self.wake_btn.connect("button-press-event", lambda *_: self.do_wake())
        self.box.pack_start(self.wake_btn, False, False, 0)

        self.stop_btn = Gtk.EventBox()
        self.stop_btn.add(Gtk.Label(label="■"))
        self.stop_btn.set_tooltip_text("Stop listening")
        self.stop_btn.set_size_request(32, -1)
        self.stop_btn.connect("button-press-event", lambda *_: self.do_stop())
        self.box.pack_start(self.stop_btn, False, False, 0)

        self._voice_active = False
        self._wave_index = 0
        self._wave_frames = ["▁▂▃▅▃▂", "▂▃▅▇▅▃", "▃▅▇▅▃▂", "▅▇▅▃▂▁"]
        GLib.timeout_add(160, self._animate_wave)
        GLib.timeout_add(1000, self._refresh_voice_state)

        self.pop = None

    def on_clicked(self, *args):
        if self.pop is None:
            self._build_popover()
        self.show_status()
        self.pop.popup()

    def _build_popover(self):
        self.pop = Gtk.Popover(relative_to=self.btn)
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        menu.set_margin_top(8)
        menu.set_margin_bottom(8)
        menu.set_margin_start(10)
        menu.set_margin_end(10)

        def item(icon, text, handler):
            row = Gtk.EventBox()
            row.add(Gtk.Label(label=f"{icon}  {text}"))
            row.connect("button-press-event", handler)
            menu.add(row)

        item("⚡", "Wake & listen", lambda *_: self.do_wake())
        item("🎤", "Say...", lambda *_: self.do_say())
        item("⏱", "Status", lambda *_: self.show_status())
        item("⚙", "Self-test", lambda *_: self.do_test())
        item("✕", "Quit Kim panel", lambda *_: self.do_quit())
        self.pop.add(menu)
        self.pop.show_all()

    def _close_popover(self):
        if self.pop is not None:
            self.pop.popdown()

    def _start_drag(self, _widget, event):
        if event.button == 1:
            self.begin_move_drag(
                event.button,
                int(event.x_root),
                int(event.y_root),
                event.time,
            )
            return True
        return False

    def _toast(self, text):
        t = Gtk.Label(label="\n".join(text.strip().splitlines()[:5]) or "ok")
        t.set_line_wrap(True)
        t.show()
        p = Gtk.Popover(relative_to=self.btn)
        p.add(t)
        p.popup()
        GLib.timeout_add_seconds(4, p.popdown)

    def _background(self, work):
        def runner():
            try:
                message = work()
            except Exception as e:  # noqa: BLE001
                message = str(e)
            GLib.idle_add(self._toast, message)

        threading.Thread(target=runner, daemon=True).start()

    def _refresh_voice_state(self):
        self._voice_active = sh(
            ["systemctl", "--user", "is-active", "--quiet", "aurora"]
        ).returncode == 0
        return True

    def _animate_wave(self):
        if self._voice_active:
            frame = self._wave_frames[self._wave_index % len(self._wave_frames)]
            self.btn_label.set_text(f"Kim  {frame}")
            self._wave_index += 1
        else:
            self.btn_label.set_text("Kim")
        return True

    def do_wake(self):
        self._close_popover()
        self._toast("waking Kim…")
        self._background(lambda: (lambda r: (r.stdout or r.stderr or "wake issued").strip())(
            sh(["bash", f"{KIM_ROOT}/service.sh", "wake"])
        ))

    def do_stop(self):
        self._close_popover()
        self._toast("stopping Kim…")
        self._background(lambda: (lambda r: (r.stdout or r.stderr or "Kim stopped").strip())(
            sh(["bash", f"{KIM_ROOT}/service.sh", "stop"])
        ))

    def do_say(self):
        self._close_popover()
        def work():
            try:
                p = subprocess.Popen(
                    ["zenity", "--entry", "--title=Speak", "--text=Words for Kim to say:",
                     "--width=420"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                out, _ = p.communicate()
            except FileNotFoundError:
                return "zenity not installed"
            text = (out or "").strip()
            if p.returncode != 0:
                return "say cancelled"
            if not text:
                return "nothing to say"
            r = sh([PY, "-m", "aurora", "say", text])
            return "spoke that aloud" if not r.returncode else (r.stderr or "say failed")

        self._background(work)

    def _state(self):
        active = sh(["systemctl", "--user", "is-active", "aurora"]).stdout.strip() == "active"
        live = False
        if active:
            j = sh(["journalctl", "--user", "-u", "aurora.service", "-n", "400", "--no-pager"])
            live = "voice session live" in j.stdout
        return "Awake ✓" if live else ("Running" if active else "Stopped")

    def show_status(self):
        self._toast(self._state())

    def do_test(self):
        self._close_popover()
        self._toast("running self-test…")
        self._background(lambda: (lambda r: (r.stdout or r.stderr or "test done").strip())(
            sh([PY, "-m", "aurora", "test"])
        ))

    def do_quit(self):
        Gtk.main_quit()

    def place(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() if display else None
        if monitor is None:
            monitor = display.get_monitor(0) if display else None
        if monitor is None:
            self.show_all()
            return
        geo = monitor.get_geometry()
        w, h = 208, 44
        self.move(geo.x + (geo.width - w) // 2, geo.y + 38)
        self.show_all()


def main():
    app = KimButton()
    app.connect("destroy", Gtk.main_quit)
    app.place()
    Gtk.main()


if __name__ == "__main__":
    main()
