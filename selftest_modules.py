#!/usr/bin/env python3
"""
Tests for the v3.1 additions: device profiles, the icon smith, the phone preview, and the
new apkforge repairs (the `class App(App)` fix + kit tolerance). No API key needed. Kivy is
only needed for the optional live-preview check; without it that check reports 'skipped'.

    python3 selftest_modules.py
"""
import io
import os
import sys
import tempfile

PASS = 0
FAIL = 0
FAILS = []


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(name)
        print("  FAIL:", name)


# ------------------------------------------------------------------ devices.py
def test_devices():
    print("[modules] device profiles")
    import devices as D
    check("has a dozen-ish profiles", len(D.names()) >= 8)
    k, p = D.resolve("pixel_8")
    check("resolves pixel_8", k == "pixel_8" and p["w"] > 0 and p["h"] > 0)
    check("alias s24 -> galaxy_s24", D.resolve("s24")[0] == "galaxy_s24")
    check("alias pixel8 -> pixel_8", D.resolve("pixel8")[0] == "pixel_8")
    check("unknown falls back to default", D.resolve("nope___")[0] == D.DEFAULT)
    w, h = D.window_size("pixel_8", max_h=880)
    check("window_size caps height", h <= 880 and w > 0)
    check("window keeps portrait aspect", h > w)
    check("table renders", "DEVICE" in D.table())


# ------------------------------------------------------------------ iconsmith.py
def _is_png(b):
    return b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) > 500


def test_iconsmith():
    print("[modules] iconsmith")
    import iconsmith as I
    icon = I.icon_png("Alarm Clock", 128)
    check("icon.png is a real PNG", _is_png(icon))
    check("round icon is a real PNG", _is_png(I.icon_round_png("Alarm Clock", 128)))
    check("presplash is a real PNG", _is_png(I.presplash_png("Alarm Clock", 256)))
    check("adaptive fg is a real PNG", _is_png(I.adaptive_fg_png("Alarm Clock", 128)))
    check("adaptive bg is a real PNG", _is_png(I.adaptive_bg_png("Alarm Clock", 128)))
    # deterministic: same name -> identical bytes; different name -> different
    check("deterministic per name", I.icon_png("Timer", 96) == I.icon_png("Timer", 96))
    check("distinct across names", I.icon_png("Timer", 96) != I.icon_png("Weather", 96))
    check("initials of two words", I._initials("Alarm Clock") == "AC")
    check("initials of one word", I._initials("Weather") == "WE")
    check("presplash_hex is #rrggbb", I.presplash_hex("X").startswith("#") and len(I.presplash_hex("X")) == 7)
    with tempfile.TemporaryDirectory() as d:
        ok_i, ok_s = I.write_assets(d, "Money Manager", size=96, full_set=True)
        check("write_assets writes icon+splash", ok_i and ok_s)
        for fn in ("icon.png", "presplash.png", "icon_round.png", "icon_fg.png", "icon_bg.png"):
            check("wrote " + fn, os.path.exists(os.path.join(d, fn)))


# ------------------------------------------------------------------ apkforge fixes
def test_apkforge_fixes():
    print("[modules] apkforge repairs + kit tolerance")
    import apkforge as A
    # the App name-collision is repaired for free
    bad = ("from kivy.app import App\n"
           "class App(App):\n"
           "    def build(self):\n"
           "        return GradientBackground()\n"
           "if __name__ == '__main__':\n"
           "    App().run()\n")
    fixed, reqs, perms, fixes = A.auto_repair(bad, "python3,kivy", "")
    check("collision repaired to _KivyApp", "class App(_KivyApp)" in fixed)
    check("repair is reported", any("collid" in f for f in fixes))
    # idempotent: repairing clean code changes nothing about the class line
    fixed2, _, _, fixes2 = A.auto_repair(fixed, reqs, perms)
    check("repair idempotent on the collision", "class App(_KivyApp)" in fixed2)
    # static analysis flags it live (as a warn, not a false error)
    issues = A.analyze_code(A.with_kit(bad), "python3,kivy", "")
    check("analysis warns about class App(App)",
          any(i["sev"] == "warn" and "shadows" in i["msg"] for i in issues))
    # kit tolerates the bad kwargs that used to crash build()
    full = A.with_kit("from kivy.app import App\n"
                      "class MyApp(App):\n"
                      "    def build(self):\n"
                      "        r = GradientBackground(strips=[], top=Theme.primary_d)\n"
                      "        c = Card(fill=True)\n"
                      "        c.add_widget(body('hi', size_hint_x=0.6))\n"
                      "        r.add_widget(c); return r\n")
    ok, msg = A.syntax_check(full)
    check("tolerant kit composes cleanly", ok)


# ------------------------------------------------------------------ preview.py
def test_preview():
    print("[modules] preview (headless)")
    import preview as P
    args = P._parse_args(["main.py", "--device", "galaxy_s24", "--scale", "0.9"])
    check("preview parses args", args.main == "main.py" and args.device == "galaxy_s24")
    # the live render needs kivy + a display; only run it when both are present
    import importlib.util
    have_kivy = importlib.util.find_spec("kivy") is not None
    have_disp = bool(os.environ.get("DISPLAY")) or bool(os.environ.get("WAYLAND_DISPLAY"))
    if not (have_kivy and have_disp):
        print("  (live preview render skipped -- needs kivy + a display)")
        check("preview import ok", True)
        return
    import apkforge as A
    with tempfile.TemporaryDirectory() as d:
        mp = os.path.join(d, "main.py")
        open(mp, "w").write(A.with_kit(
            "from kivy.app import App\n"
            "class DemoApp(App):\n"
            "    def build(self):\n"
            "        return GradientBackground()\n"
            "if __name__ == '__main__':\n    DemoApp().run()\n"))
        rc = P.main([mp, "--selftest", "--device", "pixel_8"])
        check("preview --selftest renders and exits 0", rc == 0)


if __name__ == "__main__":
    test_devices()
    test_iconsmith()
    test_apkforge_fixes()
    test_preview()
    print()
    print("=" * 50)
    print("PASS: %d   FAIL: %d" % (PASS, FAIL))
    if FAILS:
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)
