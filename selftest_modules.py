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
    # REGRESSION: the repair renames the BASE to _KivyApp, and the analyser used to only
    # recognise a literal `App` base -- so it then cried "no class X(App) found" on code
    # it had itself just fixed. Repaired code must come back completely clean.
    clean = A.analyze_code(A.with_kit(fixed), reqs, perms)
    check("repaired code raises no false 'no class X(App)' warning",
          not any("no `class X(App)`" in i["msg"] for i in clean))
    check("repaired code is issue-free", not clean)
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
def test_render_quality():
    """Regressions for defects that shipped green once: a hard black outline round every
    two-letter monogram, a grey halo on the transparent layer, and multi-second renders."""
    print("[modules] icon render quality + speed")
    import time
    import iconsmith as I

    t0 = time.time()
    png = I.icon_png("Alarm Clock", 256)
    dt = time.time() - t0
    check("a 256px icon renders in under 1s (was ~3s at 512)", dt < 1.0)

    w, h, rows = _decode_png(png)

    # 1. no hard black ring around the glyphs: opaque near-black pixels should be rare
    black = sum(1 for y in range(h) for x in range(w)
                if rows[y][x * 4 + 3] > 200 and max(rows[y][x * 4:x * 4 + 3]) < 60)
    check("icon has no hard black outline", black < (w * h) * 0.01)

    # 2. antialiased edges must not be muddy (correct source-over, not lerp-toward-black)
    edge = muddy = 0
    for y in range(h):
        for x in range(w):
            a = rows[y][x * 4 + 3]
            if 20 < a < 235:
                edge += 1
                if max(rows[y][x * 4:x * 4 + 3]) < 120:
                    muddy += 1
    check("icon edges are clean", muddy <= edge * 0.15)

    # 3. the transparent adaptive layer must not carry a dark halo
    w2, h2, rows2 = _decode_png(I.adaptive_fg_png("Alarm Clock", 128))
    e2 = m2 = 0
    for y in range(h2):
        for x in range(w2):
            a = rows2[y][x * 4 + 3]
            if 20 < a < 235:
                e2 += 1
                if max(rows2[y][x * 4:x * 4 + 3]) < 120:
                    m2 += 1
    check("adaptive foreground has no dark halo", m2 <= e2 * 0.15)

    # 4. nothing may be painted outside the squircle silhouette (corners stay empty)
    corner = rows[2][2 * 4 + 3]
    check("icon corner stays transparent", corner == 0)


def _decode_png(b):
    """Minimal RGBA PNG reader (filters 0/1/2 only) so tests can inspect pixels."""
    import zlib
    import struct
    pos, w, h, idat = 8, None, None, b""
    while pos < len(b):
        ln = struct.unpack(">I", b[pos:pos + 4])[0]
        tag = b[pos + 4:pos + 8]
        data = b[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", data[:8])
        elif tag == b"IDAT":
            idat += data
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4
    rows, prev, i = [], bytearray(stride), 0
    for _y in range(h):
        f = raw[i]; i += 1
        line = bytearray(raw[i:i + stride]); i += stride
        if f == 1:
            for x in range(4, stride):
                line[x] = (line[x] + line[x - 4]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        rows.append(bytes(line)); prev = line
    return w, h, rows


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
        P.RAN["started"] = False
        rc = P.main([mp, "--selftest", "--device", "pixel_8"])
        check("preview --selftest renders and exits 0", rc == 0)

        # REGRESSION: an app that never calls .run() rendered nothing, yet the old
        # preview still reported "selftest OK" and exited 0. A green light for a blank
        # screen is worse than no check at all.
        nr = os.path.join(d, "norun.py")
        open(nr, "w").write("from kivy.app import App\n"
                            "class ForgotApp(App):\n"
                            "    def build(self):\n"
                            "        from kivy.uix.widget import Widget\n"
                            "        return Widget()\n")
        P.RAN["started"] = False
        rc2 = P.main([nr, "--selftest", "--device", "pixel_8"])
        check("preview FAILS when the app never calls .run()", rc2 != 0)


if __name__ == "__main__":
    test_devices()
    test_iconsmith()
    test_render_quality()
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
