#!/usr/bin/env python3
"""
THE DAWG // preview  --  run a generated app in a phone-shaped window, before buildozer

Building an APK takes 20-40 minutes. This takes about two seconds: it launches your
`main.py` on the desktop in a window sized to a real Android screen, wrapped in believable
device chrome -- status bar, notch, rounded corners, home indicator -- so you can see and
click exactly what the phone will show, and catch the ugly stuff (cramped layout, clipped
text, a dead button) instantly instead of after a 40-minute build.

    python3 preview.py main.py                     # default device (Pixel 8)
    python3 preview.py main.py --device galaxy_s24
    python3 preview.py main.py --device pixel_tablet --scale 0.8
    python3 preview.py main.py --no-frame          # bare phone-size window, no chrome
    python3 preview.py --list                       # list device profiles

It needs Kivy on the desktop (the same thing the self-test uses). It does NOT need
buildozer, the Android SDK, or a key. Android-only imports in your app stay dormant on
desktop as long as they're guarded by `if platform == "android":`, exactly as the forge
already writes them.
"""
import os
import sys
import time
import runpy
import argparse

try:
    import devices
except Exception:                       # allow running from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import devices


def _parse_args(argv):
    ap = argparse.ArgumentParser(prog="preview.py", add_help=True,
                                 description="Preview a Kivy app at Android-phone size.")
    ap.add_argument("main", nargs="?", help="path to the app's main.py")
    ap.add_argument("--device", "-d", default=devices.DEFAULT,
                    help="device profile (see --list). default: %s" % devices.DEFAULT)
    ap.add_argument("--scale", "-s", type=float, default=1.0, help="zoom factor (default 1.0)")
    ap.add_argument("--max-h", type=int, default=880, help="cap window height in px")
    ap.add_argument("--no-frame", action="store_true", help="skip the device chrome overlay")
    ap.add_argument("--list", action="store_true", help="list device profiles and exit")
    ap.add_argument("--selftest", action="store_true",
                    help="render a few frames headlessly and exit 0/1 (for CI)")
    return ap.parse_args(argv)


def _configure_window(dev_key, scale, max_h):
    """MUST run before the app imports kivy.core.window. Sets the window to phone size."""
    key, prof = devices.resolve(dev_key)
    w, h = devices.window_size(key, max_h=max_h, scale=scale)
    from kivy.config import Config
    Config.set("graphics", "width", str(w))
    Config.set("graphics", "height", str(h))
    Config.set("graphics", "resizable", "0")
    Config.set("graphics", "borderless", "0")
    # keep Kivy from eating our argv or drawing its red multitouch dots
    os.environ.setdefault("KIVY_NO_ARGS", "1")
    try:
        Config.set("input", "mouse", "mouse,multitouch_on_demand")
    except Exception:
        pass
    return key, prof, (w, h)


# --------------------------------------------------------------------- device chrome
def _install_chrome(prof, win_px):
    """Add a status bar / notch / rounded-corner / home-indicator overlay onto the Window.
    Pure Kivy graphics; sits above the app and never intercepts its touches."""
    from kivy.core.window import Window
    from kivy.uix.widget import Widget
    from kivy.uix.label import Label
    from kivy.graphics import Color, RoundedRectangle, Mesh, Ellipse
    from kivy.metrics import dp
    import math

    notch = prof.get("notch", "punch")
    radius = float(prof.get("radius", 26))

    class _Chrome(Widget):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.size_hint = (None, None)
            self._clock = Label(text="", font_size="12sp", bold=True,
                                color=(1, 1, 1, 0.92), size_hint=(None, None),
                                halign="left", valign="middle")
            self.add_widget(self._clock)
            self._batt = Label(text="", font_size="12sp", bold=True,
                               color=(1, 1, 1, 0.92), size_hint=(None, None),
                               halign="right", valign="middle")
            self.add_widget(self._batt)
            Window.bind(size=self._sync, on_draw=self._tick)
            self._sync()

        def _tick(self, *a):
            try:
                self._clock.text = time.strftime("%H:%M")
            except Exception:
                self._clock.text = "9:41"

        def _corner_mesh(self, cx, cy, sx, sy, r, seg=14):
            """Black corner cap: the square corner minus a quarter disc = rounded screen."""
            acx, acy = cx + sx * r, cy + sy * r          # arc centre (inner point)
            A = (cx + sx * r, cy)                          # axis point on x
            B = (cx, cy + sy * r)                          # axis point on y
            a0 = math.atan2(A[1] - acy, A[0] - acx)
            a1 = math.atan2(B[1] - acy, B[0] - acx)
            # go the short way
            if a1 - a0 > math.pi:
                a1 -= 2 * math.pi
            elif a0 - a1 > math.pi:
                a1 += 2 * math.pi
            verts = [cx, cy, 0, 0]                          # fan hub = the corner
            for i in range(seg + 1):
                t = a0 + (a1 - a0) * (i / seg)
                verts += [acx + r * math.cos(t), acy + r * math.sin(t), 0, 0]
            idx = list(range(len(verts) // 4))
            Mesh(vertices=verts, indices=idx, mode="triangle_fan")

        def _sync(self, *a):
            w, h = Window.size
            self.size = (w, h)
            self.pos = (0, 0)
            sb = dp(26)                                    # status-bar height
            self._clock.size = (dp(80), sb)
            self._clock.text_size = self._clock.size
            self._clock.pos = (dp(14), h - sb)
            self._batt.size = (dp(80), sb)
            self._batt.text_size = self._batt.size
            self._batt.pos = (w - dp(94), h - sb)
            self._batt.text = "5G  100%"
            self.canvas.after.clear()
            with self.canvas.after:
                # rounded screen corners (4 black caps)
                Color(0, 0, 0, 1)
                r = dp(radius)
                self._corner_mesh(0, 0, 1, 1, r)           # bottom-left
                self._corner_mesh(w, 0, -1, 1, r)          # bottom-right
                self._corner_mesh(0, h, 1, -1, r)          # top-left
                self._corner_mesh(w, h, -1, -1, r)         # top-right
                # notch / punch-hole
                if notch == "notch":
                    Color(0, 0, 0, 1)
                    nw, nh = dp(150), dp(22)
                    RoundedRectangle(pos=((w - nw) / 2, h - nh),
                                     size=(nw, nh), radius=[0, 0, dp(12), dp(12)])
                elif notch == "punch":
                    Color(0, 0, 0, 1)
                    d = dp(14)
                    Ellipse(pos=((w - d) / 2, h - dp(20)), size=(d, d))
                # battery pill outline (top-right)
                Color(1, 1, 1, 0.85)
                bw, bh = dp(20), dp(10)
                RoundedRectangle(pos=(w - dp(30), h - dp(19)), size=(bw, bh), radius=[dp(2)])
                Color(1, 1, 1, 1)
                RoundedRectangle(pos=(w - dp(30), h - dp(17)), size=(bw - dp(3), bh - dp(4)),
                                 radius=[dp(1)])
                # home indicator (bottom pill)
                Color(1, 1, 1, 0.55)
                hw = dp(130)
                RoundedRectangle(pos=((w - hw) / 2, dp(7)), size=(hw, dp(5)), radius=[dp(3)])

    chrome = _Chrome()
    Window.add_widget(chrome)
    # nudge the app's root up out from under the status bar / down off the home bar
    return chrome


RAN = {"started": False}     # did the app actually reach App.run()?


def _set_title(prof):
    try:
        from kivy.core.window import Window
        Window.set_title("Preview - %s" % prof.get("label", "Android"))
    except Exception:
        pass


def _patch_run(prof, win_px, frame):
    """Wrap App.run so the chrome goes on once the loop starts, without blocking import."""
    import kivy.app
    _orig = kivy.app.App.run

    def run(self, *a, **k):
        from kivy.clock import Clock
        RAN["started"] = True
        if frame:
            Clock.schedule_once(lambda dt: _install_chrome(prof, win_px), 0)
            Clock.schedule_once(lambda dt: _inset_root(self), 0)
        _set_title(prof)
        return _orig(self, *a, **k)

    kivy.app.App.run = run


NO_RUN_MSG = (
    "the file defines an App but never launched it, so there is nothing to show.\n"
    "        Add this to the bottom of main.py:\n"
    "            if __name__ == '__main__':\n"
    "                YourApp().run()")


def _inset_root(app):
    """Sit the app's content inside the phone's safe area.

    buildozer.spec ships `fullscreen = 0`, so on a real device the system status bar is
    visible and the app gets the space BELOW it (and above the gesture bar). Without this
    the preview draws the app full-bleed and its top row hides under our status bar --
    which is not what the phone does."""
    try:
        from kivy.core.window import Window
        from kivy.metrics import dp
        root = getattr(app, "root", None)
        if root is None:
            return
        top = dp(26)          # status bar
        bottom = dp(18)       # gesture / home indicator
        # only take over sizing when the root is a normal full-bleed root
        if getattr(root, "size_hint", None) not in ((1, 1), [1, 1]):
            return
        root.size_hint = (None, None)

        def _fit(*a):
            root.size = (Window.width, max(1, Window.height - top - bottom))
            root.pos = (0, bottom)
        Window.bind(size=_fit)
        _fit()
    except Exception:
        pass


# ------------------------------------------------------------------------- selftest
def _run_selftest(mainpath, prof, win_px):
    """Headless: set up an offscreen display, load the app, pump a few frames, exit."""
    from kivy.clock import Clock
    from kivy.base import EventLoop

    frames = {"n": 0}

    def _stop_soon(dt):
        frames["n"] += 1
        if frames["n"] >= 4:
            from kivy.app import App
            app = App.get_running_app()
            if app:
                app.stop()

    import kivy.app
    _orig = kivy.app.App.run

    def run(self, *a, **k):
        RAN["started"] = True
        Clock.schedule_once(lambda dt: _install_chrome(prof, win_px), 0)
        Clock.schedule_once(lambda dt: _inset_root(self), 0)
        Clock.schedule_interval(_stop_soon, 0.05)
        Clock.schedule_once(lambda dt: self.stop(), 2.0)   # hard stop backstop
        return _orig(self, *a, **k)

    kivy.app.App.run = run
    runpy.run_path(mainpath, run_name="__main__")
    # An app that never called .run() rendered NOTHING. Reporting that as a pass is a lie
    # -- it is exactly the failure the preview exists to catch.
    return bool(RAN["started"])


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.list:
        print(devices.table())
        return 0
    if not args.main:
        print("usage: python3 preview.py <main.py> [--device NAME] [--scale S] [--no-frame]")
        print("       python3 preview.py --list")
        return 2
    mainpath = os.path.abspath(args.main)
    if not os.path.isfile(mainpath):
        print("preview: no such file: %s" % mainpath)
        return 2

    key, prof, win_px = _configure_window(args.device, args.scale, args.max_h)
    print("[preview] %s  ->  %dx%d px window" % (prof["label"], win_px[0], win_px[1]))

    # run from the app's own directory so relative imports/assets resolve like on-device
    os.chdir(os.path.dirname(mainpath) or ".")

    if args.selftest:
        try:
            ok = _run_selftest(mainpath, prof, win_px)
        except SystemExit:
            ok = bool(RAN["started"])
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[preview] selftest FAILED: %s" % e)
            return 1
        if not ok:
            print("[preview] selftest FAILED: " + NO_RUN_MSG)
            return 1
        print("[preview] selftest OK -- app rendered in the phone frame without crashing")
        return 0

    _patch_run(prof, win_px, frame=not args.no_frame)
    try:
        runpy.run_path(mainpath, run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("[preview] app crashed: %s" % e)
        return 1
    if not RAN["started"]:
        # no window ever opened -- say so instead of exiting 0 in silence
        print("[preview] nothing opened: " + NO_RUN_MSG)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
