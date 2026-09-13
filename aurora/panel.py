#!/usr/bin/python3
"""Kim panel: a small always-on-top button centered below the top bar.
Click it for Wake / Say / Status."""
import os
from pathlib import Path
import subprocess
import sys
import threading

os.environ.setdefault("GDK_BACKEND", "x11")
os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY") or ":0")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

KIM_ROOT = "/home/vishal/aurora"
PY = f"{KIM_ROOT}/.venv/bin/python"
ORB_ASSET = Path(KIM_ROOT) / "assets" / "kim-orb.svg"
VOICE_STATE_PATH = Path.home() / ".aurora" / "voice_state"


def sh(cmd, timeout=130):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "timed out")


class KimButton(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="Kim")
        # Keep GNOME’s dock/taskbar grouping under Kim instead of panel.py.
        self.set_wmclass("Kim", "Kim")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_default_size(100, 32)
        self.set_position(Gtk.WindowPosition.NONE)
        screen = Gdk.Screen.get_default()
        if screen:
            visual = screen.get_rgba_visual()
            if visual:
                self.set_visual(visual)
        self.set_app_paintable(True)

        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_name("kim-window")
        self.box.set_name("kim-container")
        self.add(self.box)

        css = Gtk.CssProvider()
        css.load_from_data(b"""
        #kim-window, #kim-container {
            background-color: #000000;
            border-radius: 18px;
        }
        #kim-orb-shell {
            background-color: #000000;
            border: 1px solid #2f3138;
            border-radius: 16px;
            padding: 0;
        }
        #kim-orb {
            background-color: transparent;
            border: 0;
            border-radius: 0;
            min-width: 28px;
            min-height: 28px;
        }
        #kim-orb.active {
            background-color: #693cff;
            border-color: #d4c7ff;
        }
        #kim-orb.responding {
            background-color: #b33cff;
            border-color: #ffd4fa;
        }
        #kim-orb.paused {
            background-color: #333746;
            border-color: #7d8297;
        }
        #kim-orb label { color: #ffffff; font-weight: bold; }
        #kim-orb-k { color: #e5e7eb; font-size: 17px; font-weight: bold; }
        #kim-status { color: #c7ccff; font-size: 10px; font-weight: bold; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.orb_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.box.pack_start(self.orb_row, True, True, 0)

        self.drag_handle = Gtk.EventBox()
        self.drag_handle.add(Gtk.Label(label="⠿"))
        self.drag_handle.set_tooltip_text("Drag Kim anywhere")
        self.drag_handle.set_size_request(4, 24)
        self.drag_handle.connect("button-press-event", self._start_drag)
        self.orb_row.pack_start(self.drag_handle, False, False, 0)

        self.shell = Gtk.EventBox()
        self.shell.set_name("kim-orb-shell")
        self.shell.set_size_request(26, 26)

        self.btn = Gtk.EventBox()
        self.btn.set_name("kim-orb")
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(ORB_ASSET), 24, 24, True)
        self.orb_image = Gtk.Image.new_from_pixbuf(pixbuf)
        self.orb_image.set_size_request(24, 24)
        self.btn.add(self.orb_image)
        self.btn.set_size_request(24, 24)
        self.shell.add(self.btn)
        self.orb_row.pack_start(self.shell, True, True, 0)
        self.btn.connect("button-press-event", self.on_clicked)

        self.status_label = Gtk.Label(label="OFFLINE")
        self.status_label.set_name("kim-status")
        self.box.pack_start(self.status_label, False, False, 0)

        self.controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self.box.pack_start(self.controls, False, False, 0)

        self.wake_btn = Gtk.EventBox()
        self.wake_btn.add(Gtk.Label(label="▶"))
        self.wake_btn.set_tooltip_text("Wake and listen")
        self.wake_btn.set_size_request(32, -1)
        self.wake_btn.connect("button-press-event", lambda *_: self.do_wake())
        self.controls.pack_start(self.wake_btn, True, True, 0)

        self.stop_btn = Gtk.EventBox()
        self.stop_btn.add(Gtk.Label(label="■"))
        self.stop_btn.set_tooltip_text("Stop listening")
        self.stop_btn.set_size_request(32, -1)
        self.stop_btn.connect("button-press-event", lambda *_: self.do_stop())
        self.controls.pack_start(self.stop_btn, True, True, 0)

        self._voice_active = False
        self._voice_state = "offline"
        self._wave_index = 0
        self._wave_frames = ["·  ·  ·", "·  •  ·", "•  ●  •", "·  •  ·"]
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
        item("↻", "Restart widget", lambda *_: self.do_restart())
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
        try:
            state = VOICE_STATE_PATH.read_text().strip().lower()
        except Exception:
            state = ""
        if state not in {"offline", "listening", "responding", "paused"}:
            state = "listening" if sh(
                ["systemctl", "--user", "is-active", "--quiet", "aurora"]
            ).returncode == 0 else "offline"
        self._voice_state = state
        self._voice_active = state != "offline"
        self.status_label.set_text(state.upper())
        for name in ("active", "responding", "paused"):
            self.btn.get_style_context().remove_class(name)
        if state == "listening":
            self.btn.get_style_context().add_class("active")
        elif state in {"responding", "paused"}:
            self.btn.get_style_context().add_class(state)
        return True

    def _animate_wave(self):
        if self._voice_state in {"listening", "responding"}:
            frame = self._wave_frames[self._wave_index % len(self._wave_frames)]
            self.orb_image.set_opacity(0.82 + 0.16 * ((self._wave_index % 4) / 3))
            self._wave_index += 1
        else:
            self.orb_image.set_opacity(1.0)
        return True

    def do_wake(self):
        self._close_popover()
        self._toast("waking Kim…")
        self._background(lambda: (lambda r: (r.stdout or r.stderr or "wake issued").strip())(
            sh(["bash", f"{KIM_ROOT}/service.sh", "wake"])
        ))

    def do_stop(self):
        self._close_popover()
        self._toast("pausing Kim…")
        self._background(lambda: (lambda r: (r.stdout or r.stderr or "Kim stopped").strip())(
            sh(["bash", f"{KIM_ROOT}/service.sh", "pause"])
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

    def do_restart(self):
        self._close_popover()
        subprocess.Popen(["systemctl", "--user", "restart", "kim-panel.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def place(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() if display else None
        if monitor is None:
            monitor = display.get_monitor(0) if display else None
        if monitor is None:
            self.show_all()
            return
        geo = monitor.get_geometry()
        w, h = 100, 32
        self.move(geo.x + (geo.width - w) // 2, geo.y + 38)
        self.show_all()


def main():
    app = KimButton()
    app.connect("destroy", Gtk.main_quit)
    app.place()
    Gtk.main()


if __name__ == "__main__":
    main()
