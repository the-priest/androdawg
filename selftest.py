#!/usr/bin/env python3
"""
Stress test for apkforge. Exercises the parser/validator against adversarial
model output and the full HTTP pipeline with a mocked AI and a mocked buildozer,
so the tool's logic can be proven without API keys or a real Android toolchain.

Run: python3 selftest.py
"""
import os
import re
import sys
import json
import time
import threading
import urllib.request
import urllib.error

import apkforge as A

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


GOOD_APP = (
    "from kivy.app import App\n"
    "from kivy.uix.label import Label\n"
    "class MyApp(App):\n"
    "    def build(self):\n"
    "        return Label(text='hi')\n"
    "if __name__ == '__main__':\n"
    "    MyApp().run()\n"
)

# ---- adversarial model outputs: (label, text, expect_ok, expect_hard_errors)
CASES = []

# 1. perfect marker format
CASES.append(("perfect_markers",
    "<<<NAME>>>\nflappy\n<<<TITLE>>>\nFlappy\n<<<ORIENTATION>>>\nportrait\n"
    "<<<REQUIREMENTS>>>\npython3,kivy\n<<<PERMISSIONS>>>\n\n<<<MAIN_PY>>>\n"
    + GOOD_APP + "\n<<<NOTES>>>\ntap to flap\n<<<END>>>", True, False))

# 2. markers but MAIN_PY wrapped in a python fence
CASES.append(("markers_fenced_code",
    "<<<NAME>>>\nx\n<<<MAIN_PY>>>\n```python\n" + GOOD_APP + "```\n<<<END>>>", True, False))

# 3. no markers at all, just a python fence
CASES.append(("no_markers_fence",
    "Here is your app:\n```python\n" + GOOD_APP + "```\nEnjoy.", True, False))

# 4. no markers, raw python only
CASES.append(("raw_python_only", GOOD_APP, True, False))

# 5. prose before and after the markers
CASES.append(("prose_around_markers",
    "Sure! Here you go.\n<<<NAME>>>\ny\n<<<MAIN_PY>>>\n" + GOOD_APP
    + "<<<NOTES>>>\nok\n<<<END>>>\nLet me know if you want changes.", True, False))

# 6. missing optional sections (no title/orientation/req/perms)
CASES.append(("missing_optionals",
    "<<<MAIN_PY>>>\n" + GOOD_APP + "<<<END>>>", True, False))

# 7. tkinter app -> hard error
CASES.append(("tkinter_hard_error",
    "<<<NAME>>>\nbad\n<<<MAIN_PY>>>\nimport tkinter\nfrom kivy.app import App\n"
    "class A(App):\n    def build(self):\n        return None\nA().run()\n<<<END>>>", True, True))

# 8. PyQt5 app -> hard error
CASES.append(("pyqt_hard_error",
    "<<<MAIN_PY>>>\nfrom PyQt5 import QtWidgets\nApp\n.run()\n<<<END>>>", True, True))

# 9. gi/GTK -> hard error
CASES.append(("gtk_hard_error",
    "<<<MAIN_PY>>>\nimport gi\nfrom kivy.app import App\nclass A(App):\n"
    "    def build(self):\n        return None\nA().run()\n<<<END>>>", True, True))

# 10. empty / garbage -> not ok
CASES.append(("empty", "", False, False))
CASES.append(("garbage", "lorem ipsum no code here at all", False, False))

# 11. syntax-broken code (still a payload, syntax flagged, but builds blocked later)
CASES.append(("syntax_broken",
    "<<<MAIN_PY>>>\nfrom kivy.app import App\nclass A(App)\n    def build(self): return None\nA().run()\n<<<END>>>", True, False))

# 12. weird requirement -> warning, not error
CASES.append(("weird_requirement",
    "<<<REQUIREMENTS>>>\npython3,kivy,leftpad\n<<<MAIN_PY>>>\n" + GOOD_APP + "<<<END>>>", True, False))

# 13. landscape orientation honored
CASES.append(("landscape",
    "<<<ORIENTATION>>>\nlandscape\n<<<MAIN_PY>>>\n" + GOOD_APP + "<<<END>>>", True, False))

# 14. junk orientation -> defaults portrait
CASES.append(("junk_orientation",
    "<<<ORIENTATION>>>\nsideways\n<<<MAIN_PY>>>\n" + GOOD_APP + "<<<END>>>", True, False))

# 15. huge code blob
CASES.append(("huge_code",
    "<<<MAIN_PY>>>\n" + GOOD_APP + ("# pad\n" * 5000) + "<<<END>>>", True, False))


def run_spec_tests():
    print("[1c] package/title sanitizers + spec")
    pkg = [
        ("3d game", "a3d_game"),
        ("garry_is_gay", "garry_is_gay"),
        ("My Cool App!!", "my_cool_app"),
        ("", "app"),
        ("123", "a123"),
        ("___", "app"),
    ]
    for inp, exp in pkg:
        got = A.safe_package(inp)
        check("safe_package(%r)==%r" % (inp, exp), got == exp)
    check("safe_title strips newlines", "\n" not in A.safe_title("a\nb"))
    check("safe_title strips brackets", "[" not in A.safe_title("x[y]"))
    check("safe_title length cap", len(A.safe_title("z" * 200)) <= 60)
    spec = A.make_spec("3D Blaster!", "3D Blaster!", "python3,kivy", "INTERNET", "portrait")
    check("spec package.name valid (no leading digit)",
          re.search(r"(?m)^package\.name = [a-z_][a-z0-9_]*$", spec) is not None)
    check("spec has exclude_dirs", "source.exclude_dirs" in spec)
    check("spec single-line title", spec.count("\ntitle = ") == 1)


def run_url_tests():
    print("[1b] chat_url normalization")
    cases = [
        ("https://api.siliconflow.cn/v1/chat/completions", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.siliconflow.cn/v1", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.siliconflow.cn/v1/", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.siliconflow.cn", "https://api.siliconflow.cn/v1/chat/completions"),
        ("https://api.groq.com/openai/v1/chat/completions", "https://api.groq.com/openai/v1/chat/completions"),
        ("https://api.groq.com/openai/v1", "https://api.groq.com/openai/v1/chat/completions"),
        ("", A.SF_URL),
        ("   ", A.SF_URL),
    ]
    for inp, exp in cases:
        got = A.chat_url(inp)
        check("chat_url(%r)==%r" % (inp, exp), got == exp)


def run_parser_cases():
    print("[1] parser/validator adversarial cases")
    for label, text, exp_ok, exp_err in CASES:
        try:
            p = A.build_forge_payload(text, "make a thing")
        except Exception as e:
            check(label + " (no exception)", False)
            print("    raised:", e)
            continue
        check(label + " ok==%s" % exp_ok, p.get("ok") == exp_ok)
        if exp_ok:
            check(label + " has main_py", bool(p.get("main_py")))
            check(label + " name is slug", p.get("name") == A.slugify(p.get("name", "")))
            check(label + " requirements has python3,kivy",
                  "python3" in p["requirements"] and "kivy" in p["requirements"])
            check(label + " orientation valid",
                  p["orientation"] in ("portrait", "landscape", "all"))
            has_err = bool(p.get("errors"))
            check(label + " hard_errors==%s" % exp_err, has_err == exp_err)
    # spec generation never throws and contains the essentials
    for label, text, exp_ok, _ in CASES:
        if not exp_ok:
            continue
        p = A.build_forge_payload(text, "thing")
        spec = A.make_spec(p["title"], p["name"], p["requirements"], p["permissions"], p["orientation"])
        check(label + " spec has requirements line", "requirements = " in spec)
        check(label + " spec has package.name", "package.name = " in spec)
        check(label + " spec arch", A.ANDROID_ARCHS in spec)


def hammer_parser(n):
    print("[2] hammering parser %d times (determinism / no-crash)" % n)
    crashes = 0
    mismatches = 0
    import itertools
    texts = [c[1] for c in CASES]
    expects = [(c[2], c[3]) for c in CASES]
    for i in range(n):
        idx = i % len(texts)
        try:
            p = A.build_forge_payload(texts[idx], "thing %d" % i)
        except Exception:
            crashes += 1
            continue
        exp_ok, exp_err = expects[idx]
        if p.get("ok") != exp_ok:
            mismatches += 1
        if exp_ok and (bool(p.get("errors")) != exp_err):
            mismatches += 1
    check("hammer: zero crashes in %d runs" % n, crashes == 0)
    check("hammer: zero mismatches in %d runs" % n, mismatches == 0)
    print("    crashes=%d mismatches=%d" % (crashes, mismatches))


# ---- HTTP pipeline test with mocked AI + mocked buildozer ----
class FakePopen:
    """Pretends to be buildozer: emits a few log lines, writes a fake apk, exits 0."""
    def __init__(self, args, **kw):
        self.returncode = 0
        cwd = kw.get("cwd") or "."
        bindir = os.path.join(cwd, "bin")
        os.makedirs(bindir, exist_ok=True)
        with open(os.path.join(bindir, "app-debug.apk"), "wb") as f:
            f.write(b"FAKE_APK_BYTES")
        self._lines = iter([
            "# Check configuration tokens",
            "# Preparing build",
            "# Building python-for-android distribution",
            "BUILD SUCCESSFUL in 12s",
            "",
        ])
        self.stdout = self

    def __iter__(self):
        return self._lines

    def wait(self):
        return 0


def http_json(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def http_bytes(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read()


def http_post_bytes(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def http_raw(method, url, body=None):
    """Like http_json but returns raw bytes -- for the HTML index."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def start_server():
    """Spin up the real handler on an ephemeral port against isolated temp state.
    Returns (server, base_url); caller must srv.shutdown()."""
    A.PROJECTS = os.path.join(os.getcwd(), "_test_projects")
    os.makedirs(A.PROJECTS, exist_ok=True)
    A.CONFIG_DIR = os.path.join(os.getcwd(), "_test_cfg")
    A.CONFIG_PATH = os.path.join(A.CONFIG_DIR, "config.json")
    A.CACHEDIR = os.path.join(os.getcwd(), "_test_cache")
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), A.H)
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, base


def run_http_pipeline():
    print("[3] full HTTP pipeline (mocked AI + mocked buildozer)")

    # mock AI to return a perfect marker payload
    canned = (
        "<<<NAME>>>\npipeline_app\n<<<TITLE>>>\nPipeline App\n<<<ORIENTATION>>>\nportrait\n"
        "<<<REQUIREMENTS>>>\npython3,kivy\n<<<PERMISSIONS>>>\nINTERNET\n<<<MAIN_PY>>>\n"
        + GOOD_APP + "\n<<<NOTES>>>\nmocked\n<<<END>>>"
    )
    A.call_ai = lambda messages, **kw: (canned, "MockProvider")

    # pretend buildozer exists + mock the subprocess
    real_which = A.shutil.which
    A.shutil.which = lambda name: ("/usr/bin/buildozer" if name == "buildozer" else real_which(name))
    A.subprocess.Popen = FakePopen

    # use a temp project dir so we do not touch the real home
    A.PROJECTS = os.path.join(os.getcwd(), "_test_projects")
    os.makedirs(A.PROJECTS, exist_ok=True)
    # isolate the settings store so the test never touches/persists real keys
    A.CONFIG_DIR = os.path.join(os.getcwd(), "_test_cfg")
    A.CONFIG_PATH = os.path.join(A.CONFIG_DIR, "config.json")
    A.CONFIG = dict(A.DEFAULT_CONFIG)

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), A.H)
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # index
        s, b = http_bytes(base + "/")
        check("GET / 200", s == 200)
        check("GET / has html", b'APK FORGE' in b)

        # doctor
        s, d = http_json("GET", base + "/api/doctor")
        check("doctor 200", s == 200 and "checks" in d)

        # smoke test payload
        s, d = http_json("GET", base + "/api/smoketest")
        check("smoketest ok", s == 200 and d.get("ok") and "SmokeApp" in d.get("main_py", ""))
        check("smoketest no hard errors", not d.get("errors"))

        # forge (empty -> 400)
        s, d = http_json("POST", base + "/api/forge", {"description": ""})
        check("forge empty -> 400", s == 400)

        # forge (real)
        s, d = http_json("POST", base + "/api/forge", {"description": "a label app"})
        check("forge 200 ok", s == 200 and d.get("ok"))
        check("forge provider passed through", d.get("provider") == "MockProvider")
        check("forge syntax ok", d.get("syntax_ok") is True)
        payload = d

        # build (real, mocked buildozer) -> should produce apk
        s, d = http_json("POST", base + "/api/build", payload)
        check("build 200", s == 200 and "build_id" in d)
        bid = d.get("build_id")

        # poll log until done/failed
        status = "running"
        apk = None
        for _ in range(50):
            s, d = http_json("GET", base + "/api/log?id=" + bid)
            status = d.get("status")
            apk = d.get("apk")
            if status in ("done", "failed"):
                break
            time.sleep(0.1)
        check("build reached done", status == "done")
        check("apk path set", bool(apk))

        # download apk
        if apk:
            s, b = http_bytes(base + "/api/apk?id=" + bid)
            check("apk download 200", s == 200)
            check("apk bytes correct", b == b"FAKE_APK_BYTES")

        # build with tkinter -> refused at preflight (400)
        bad = dict(payload)
        bad["main_py"] = "import tkinter\nfrom kivy.app import App\nApp().run()\n"
        s, d = http_json("POST", base + "/api/build", bad)
        check("build tkinter refused 400", s == 400 and "won't build" in d.get("error", ""))

        # build with syntax error -> refused (400)
        bad2 = dict(payload)
        bad2["main_py"] = "def broken(:\n  pass\n"
        s, d = http_json("POST", base + "/api/build", bad2)
        check("build syntax-error refused 400", s == 400 and "syntax" in d.get("error", "").lower())

        # build empty -> 400
        s, d = http_json("POST", base + "/api/build", {"main_py": ""})
        check("build empty refused 400", s == 400)

        # unknown route -> 404
        s, d = http_json("GET", base + "/api/nope")
        check("unknown route 404", s == 404)

        # malformed POST body -> handled (forge with junk -> 400 empty desc)
        req = urllib.request.Request(base + "/api/forge", data=b"{not json",
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                code = r.status
                jd = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            code = e.code
            jd = json.loads(e.read().decode())
        check("malformed body handled (400, no crash)", code == 400)

        # ---- ping / single-instance / quit ----
        s, d = http_json("GET", base + "/api/ping")
        check("ping 200 app marker", s == 200 and d.get("app") == "androdawg")
        check("ping returns version", d.get("version") == A.VERSION)
        check("instance_version detects self", A.instance_version(base + "/") == A.VERSION)
        check("instance_version None on dead port", A.instance_version("http://127.0.0.1:1/") is None)
        real_exit = A.os._exit
        A.os._exit = lambda *a: None  # don't actually kill the test process
        try:
            s, d = http_json("POST", base + "/api/quit")
            check("quit 200 bye", s == 200 and d.get("bye") is True)
            time.sleep(0.6)  # let the (now no-op) shutdown timer fire
        finally:
            A.os._exit = real_exit

        # ---- settings store ----
        s, d = http_json("GET", base + "/api/config")
        check("config GET 200", s == 200 and "sf_model" in d)
        s, d = http_json("POST", base + "/api/config", {"sf_key": "sk-test123", "sf_model": "my/model"})
        check("config POST saved", s == 200 and d.get("saved") is True)
        check("config POST sf_key_set", d.get("sf_key_set") is True)
        check("sf_key() resolves stored", A.sf_key() == "sk-test123")
        check("CONFIG model updated", A.CONFIG.get("sf_model") == "my/model")
        s, d = http_json("GET", base + "/api/config")
        check("config GET reflects set", d.get("sf_key_set") is True)
        check("config GET does not leak raw key", "sk-test123" not in json.dumps(d))
        s, d = http_json("POST", base + "/api/config", {"clear_sf": True})
        check("config clear sf", d.get("sf_key_set") is False)
        check("sf_key() cleared", A.sf_key() == "")
        # blank key keeps current (set, then send blank)
        http_json("POST", base + "/api/config", {"sf_key": "sk-keepme"})
        http_json("POST", base + "/api/config", {"sf_key": "", "sf_model": "other/model"})
        check("blank key keeps current", A.sf_key() == "sk-keepme")
        check("model still updates on blank-key save", A.CONFIG.get("sf_model") == "other/model")

        # ---- project zip ----
        s, zb = http_post_bytes(base + "/api/project_zip", payload)
        check("project_zip 200", s == 200)
        import io as _io
        import zipfile as _zip
        zf = _zip.ZipFile(_io.BytesIO(zb))
        names = zf.namelist()
        check("zip has main.py", any(n.endswith("/main.py") for n in names))
        check("zip has buildozer.spec", any(n.endswith("/buildozer.spec") for n in names))
        specname = [n for n in names if n.endswith("buildozer.spec")][0]
        spectext = zf.read(specname).decode()
        check("zip spec has requirements line", "requirements = " in spectext)
        check("zip spec has arch", A.ANDROID_ARCHS in spectext)
        # zip refuses empty
        s, d = http_json("POST", base + "/api/project_zip", {"main_py": ""})
        check("project_zip empty refused 400", s == 400)

    finally:
        srv.shutdown()
        A.shutil.which = real_which


def run_buildozer_missing_path():
    print("[4] buildozer-missing path (preflight refuses cleanly)")
    real_which = A.shutil.which
    A.shutil.which = lambda name: None  # nothing on PATH
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), A.H)
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        payload = A.build_forge_payload(
            "<<<MAIN_PY>>>\n" + GOOD_APP + "<<<END>>>", "x")
        s, d = http_json("POST", base + "/api/build", payload)
        check("no-buildozer refused 400", s == 400 and "buildozer not found" in d.get("error", ""))
    finally:
        srv.shutdown()
        A.shutil.which = real_which



def run_v2_endpoints():
    print("[5] v2 endpoints (templates, fix, polish, testrun, icon, build overrides)")
    # ---- pure-unit: BUILD override whitelist ----
    ov, warns = A.parse_build_overrides(
        "orientation=landscape\nfullscreen=1\napi=33\nminapi=24\n"
        "presplash_color=#101510\nwakelock=1\nbogus_key=evil\nndk=99\napi=999")
    check("override keep orientation", ov.get("orientation") == "landscape")
    check("override keep fullscreen", ov.get("fullscreen") == "1")
    check("override keep minapi", ov.get("minapi") == "24")
    check("override keep presplash_color", ov.get("presplash_color") == "#101510")
    check("override keep wakelock", ov.get("wakelock") == "1")
    check("override drop bogus key", "bogus_key" not in ov)
    check("override drop ndk (not whitelisted)", "ndk" not in ov)
    check("override drop out-of-range api (999)", ov.get("api") != "999")
    check("override warns on dropped keys", any(("bogus_key" in w or "ndk" in w) for w in warns))
    ov_bad, _ = A.parse_build_overrides("presplash_color=notacolor\nminapi=5")
    check("override drop bad hex color", "presplash_color" not in ov_bad)
    check("override drop out-of-range minapi", "minapi" not in ov_bad)
    # ---- make_spec honours new args ----
    spec = A.make_spec("T", "pkg", "python3,kivy", "INTERNET", "portrait",
                       archs="arm64-v8a,armeabi-v7a", overrides={"fullscreen": "1", "api": "33"})
    check("spec multi-arch honoured", "android.archs = arm64-v8a,armeabi-v7a" in spec)
    check("spec fullscreen override applied", "fullscreen = 1" in spec)
    check("spec api override applied", "android.api = 33" in spec)
    check("spec still single title", spec.count("\ntitle = ") == 1)
    check("spec package.name still valid",
          re.search(r"(?m)^package\.name = [a-z_][a-z0-9_]*$", spec) is not None)
    check("spec still has arch substring (back-compat)", A.ANDROID_ARCHS in spec)
    # ---- analyze_code returns structured issues ----
    issues = A.analyze_code(GOOD_APP, "python3,kivy", "")
    check("analyze_code returns list", isinstance(issues, list))
    check("analyze_code items shaped", all(("sev" in i and "msg" in i) for i in issues))
    # try-guarded android import is NOT flagged; unguarded IS
    e_g, w_g = A.validate_code(
        "try:\n    import android\nexcept Exception:\n    pass\n" + GOOD_APP, "python3,kivy")
    check("try-guarded android no warn", not any("android" in x for x in w_g))
    e_u, w_u = A.validate_code("import android\n" + GOOD_APP, "python3,kivy")
    check("unguarded android warns", any("android" in x for x in w_u))
    # ---- kit injection helpers ----
    k = A.with_kit(GOOD_APP)
    check("with_kit adds markers", A.KIT_BEGIN in k and A.KIT_END in k)
    check("with_kit idempotent", A.with_kit(k) == A.ensure_kit(k))
    sok, _ = A.syntax_check(k)
    check("with_kit output parses", sok)

    # ---- server-backed checks ----
    canned = "<<<MAIN_PY>>>\n" + A.with_kit(GOOD_APP) + "\n<<<END>>>"
    A.call_ai = lambda messages, **kw: (canned, "MockProvider")
    A.PROJECTS = os.path.join(os.getcwd(), "_test_projects")
    os.makedirs(A.PROJECTS, exist_ok=True)
    A.CONFIG_DIR = os.path.join(os.getcwd(), "_test_cfg")
    A.CONFIG_PATH = os.path.join(A.CONFIG_DIR, "config.json")
    A.CONFIG = dict(A.DEFAULT_CONFIG)
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), A.H)
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # icon routes (own-icon-in-panel: window favicon)
        s, b = http_bytes(base + "/icon.png")
        check("icon.png 200", s == 200)
        check("icon.png is real PNG", b[:8] == b"\x89PNG\r\n\x1a\n")
        s, b = http_bytes(base + "/favicon.ico")
        check("favicon.ico serves PNG", s == 200 and b[:8] == b"\x89PNG\r\n\x1a\n")
        # index references the favicon so the app-window titlebar uses it
        s, hb = http_bytes(base + "/")
        check("index links favicon", b'rel="icon"' in hb and b'/icon.png' in hb)

        # templates
        s, d = http_json("GET", base + "/api/templates")
        check("templates 200 list", s == 200 and isinstance(d.get("templates"), list) and len(d["templates"]) >= 1)
        # v3: templates declare whether they ship the kit. "blank" deliberately ships
        # nothing at all -- an empty editor is the point of manual mode.
        by_id = {t["id"]: t for t in d["templates"]}
        check("blank template declares no kit", by_id["blank"]["kit"] is False)
        s, d = http_json("GET", base + "/api/template?id=kit")
        check("kit template has main_py", s == 200 and bool(d.get("main_py")))
        check("kit template carries the kit", A.KIT_BEGIN in d.get("main_py", ""))
        s, d = http_json("GET", base + "/api/template?id=min")
        check("min template has code but no kit",
              bool(d.get("main_py")) and A.KIT_BEGIN not in d.get("main_py", ""))
        s, d = http_json("GET", base + "/api/template?id=__nope__")
        check("unknown template 404", s == 404)

        # fix (mocked AI) -> forge-shaped payload, kit preserved
        s, d = http_json("POST", base + "/api/fix", {"main_py": GOOD_APP, "error": "boom"})
        check("fix 200 ok", s == 200 and d.get("ok") and bool(d.get("main_py")))
        check("fix output carries kit", A.KIT_BEGIN in d.get("main_py", ""))
        s, d = http_json("POST", base + "/api/fix", {"main_py": ""})
        check("fix empty 400", s == 400)

        # polish (mocked AI)
        s, d = http_json("POST", base + "/api/polish", {"main_py": GOOD_APP})
        check("polish 200 ok", s == 200 and d.get("ok") and bool(d.get("main_py")))
        s, d = http_json("POST", base + "/api/polish", {"main_py": ""})
        check("polish empty 400", s == 400)

        # testrun: guards
        s, d = http_json("POST", base + "/api/testrun", {"main_py": ""})
        check("testrun empty 400", s == 400)
        s, d = http_json("POST", base + "/api/testrun", {"main_py": "def x(:\n pass\n"})
        check("testrun syntax-broken 400", s == 400)
        # testrun: real kit app reaches a terminal status (tolerant of skip if no kivy/display)
        s, d = http_json("POST", base + "/api/testrun",
                         {"main_py": A.with_kit(GOOD_APP), "requirements": "python3,kivy"})
        check("testrun 200 test_id", s == 200 and bool(d.get("test_id")))
        tid = d.get("test_id")
        terminal = {"pass", "fail", "warn", "timeout", "skipped"}
        status = "running"
        for _ in range(450):  # up to ~45s
            s, d = http_json("GET", base + "/api/testlog?id=" + tid)
            status = d.get("status")
            if status in terminal:
                break
            time.sleep(0.1)
        check("testrun reaches terminal status", status in terminal)
        s, d = http_json("GET", base + "/api/testlog?id=__nope__")
        check("testlog unknown 404", s == 404)
    finally:
        srv.shutdown()


# ============================================================== v3 additions
def run_kit_api_tests():
    """The kit API the prompt advertises must match the kit source, byte for byte.
    v2 shipped a prompt describing Theme.BG / IconButton(glyph=) -- neither existed --
    and every app that touched the theme crashed on launch."""
    print("\n[v3] kit API contract")
    # every Theme attribute named in the generated prompt really exists
    named = re.findall(r"Theme\.(\w+)", A.KIT_API)
    for attr in set(named):
        check("prompt's Theme.%s exists in the kit" % attr, attr in A.KIT_INFO["theme_attrs"])
    # the prompt must not resurrect the v2 ghosts
    for ghost in ("Theme.BG", "Theme.TXT", "Theme.ACCENT2", "Theme.GOOD", "Theme.BAD"):
        check("prompt no longer claims %s" % ghost, ghost not in A.SYSTEM_PROMPT)
    check("prompt no longer claims IconButton(glyph=)", "glyph=" not in A.SYSTEM_PROMPT)
    # kit introspection found the real widgets
    for w in ("Theme", "Card", "PillButton", "IconButton", "TextField", "AppBar",
              "GradientBackground", "Divider"):
        check("kit exposes %s" % w, w in A.KIT_PUBLIC)
    check("IconButton takes text=", "text" in A.KIT_INFO["ctor_kwargs"]["IconButton"])
    check("AppBar takes subtitle=", "subtitle" in A.KIT_INFO["ctor_kwargs"]["AppBar"])
    # every shipped template must be clean under our own analyser
    for tid, t in A.TEMPLATES.items():
        code = A.with_kit(t["code"]) if t.get("kit", True) else t["code"]
        if not code.strip():
            continue
        errs = [i for i in A.analyze_code(code, "python3,kivy", "") if i["sev"] == "error"]
        check("template '%s' has no blocking issues" % tid, not errs)
        ok, msg = A.syntax_check(code)
        check("template '%s' parses" % tid, ok)


def run_analysis_tests():
    print("\n[v3] static analysis")
    cases = [
        ("Theme.NOPE",       "c = Card(); x = Theme.NOPE", "error", "Theme.NOPE"),
        ("bad kit kwarg",    "b = IconButton(glyph='+')",  "error", "glyph"),
        ("undefined name",   "w = Chip(text='x')",         "error", "Chip"),
        ("kv file",          "Builder.load_file('a.kv')",  "error", ".kv"),
        ("subprocess",       "import subprocess\nsubprocess.run(['ls'])", "error", "shell"),
        ("time.sleep",       "import time\ntime.sleep(5)", "warn",  "ANR"),
        ("bare except",      "try:\n    pass\nexcept:\n    pass", "info", "bare"),
    ]
    for label, snippet, sev, needle in cases:
        issues = A.analyze_code(snippet, "python3,kivy", "")
        hit = any(i["sev"] == sev and needle.lower() in i["msg"].lower() for i in issues)
        check("analysis flags %s as %s" % (label, sev), hit)
    # and must NOT cry wolf on healthy code
    good = A.with_kit(A.TEMPLATES["kit"]["code"])
    errs = [i for i in A.analyze_code(good, "python3,kivy", "") if i["sev"] == "error"]
    check("no false positives on the kit starter", not errs)
    check("empty file produces no issues", A.analyze_code("", "python3,kivy", "") == [])


def run_repair_tests():
    print("\n[v3] local auto-repair (must cost 0 tokens)")
    before = dict(A.USAGE)
    bad = ("import requests\n"
           "from kivy.app import App\n"
           "class MyApp(App):\n"
           "    def build(self):\n"
           "        Window.size = (400, 800)\n"
           "        return Card(bg=Theme.TXT)\n")
    code, reqs, perms, fixes = A.auto_repair(bad, "python3,kivy", "")
    check("repair fixes Theme.TXT", "Theme.text" in code and "Theme.TXT" not in code)
    check("repair fixes Card(bg=)", "fill=" in code and "bg=" not in code)
    check("repair drops Window.size", "Window.size" not in code)
    check("repair declares requests", "requests" in reqs)
    check("repair declares INTERNET", "INTERNET" in perms)
    check("repair appends missing run()", ".run()" in code)
    check("repair reported every change", len(fixes) >= 5)
    check("repair spent no tokens", dict(A.USAGE) == before)
    # idempotent: repairing clean code changes nothing
    code2, _, _, fixes2 = A.auto_repair(code, reqs, perms)
    check("repair is idempotent", not fixes2)


def run_token_tests():
    print("\n[v3] token discipline")
    full = A.with_kit(A.TEMPLATES["kit"]["code"])
    app = A.strip_kit(full)
    check("strip_kit removes the kit", A.KIT_BEGIN not in app)
    check("strip_kit keeps the app", "class MyApp" in app)
    check("strip_kit round-trips", A.strip_kit(A.with_kit(app)).strip() == app.strip())
    saved = A.est_tokens(full) - A.est_tokens(app)
    check("stripping the kit saves >1000 tokens/call", saved > 1000)
    print("      (~%d tokens saved per fix/polish round, each way)" % saved)
    # metering
    A.USAGE.update({"calls": 0, "prompt": 0, "completion": 0, "total": 0, "cached": 0, "saved": 0})
    A.meter(100, 50)
    check("meter accumulates", A.USAGE["total"] == 150 and A.USAGE["calls"] == 1)
    # budget guard
    A.CONFIG["token_budget"] = 100
    try:
        A.check_budget()
        check("budget stops an over-spend", False)
    except RuntimeError:
        check("budget stops an over-spend", True)
    A.CONFIG["token_budget"] = 0
    check("no budget = unlimited", A.budget_left() is None)
    A.check_budget()


def run_v3_endpoints():
    print("\n[v3] endpoints")
    srv, base = start_server()
    try:
        s, d = http_json("GET", base + "/api/usage")
        check("usage 200", s == 200 and "usage" in d)
        check("usage reports kit size", d.get("kit_lines", 0) > 100)

        # blank template must be genuinely empty -- that is the whole point of manual mode
        s, d = http_json("GET", base + "/api/template?id=blank")
        check("blank template 200", s == 200)
        check("blank template is empty", d.get("main_py") == "")
        check("blank template has no kit", d.get("kit") is False)
        check("blank template raises no issues", not d.get("issues"))

        # manual endpoint honours the kit toggle
        s, d = http_json("POST", base + "/api/manual",
                         {"main_py": "x = 1\n", "kit": False})
        check("manual without kit is verbatim", d.get("main_py") == "x = 1\n")
        s, d = http_json("POST", base + "/api/manual",
                         {"main_py": "x = 1\n", "kit": True})
        check("manual with kit prepends it", A.KIT_BEGIN in d.get("main_py", ""))

        # lint
        s, d = http_json("POST", base + "/api/lint",
                         {"main_py": "x = Theme.NOPE", "requirements": "python3,kivy"})
        check("lint 200", s == 200)
        check("lint catches the bad attribute", any("Theme.NOPE" in i["msg"] for i in d["issues"]))
        s, d = http_json("POST", base + "/api/lint", {"main_py": "def (:", "requirements": ""})
        check("lint reports syntax errors", d.get("syntax_ok") is False)

        # repair endpoint
        s, d = http_json("POST", base + "/api/repair",
                         {"main_py": "c = Card(bg=Theme.TXT)", "requirements": "python3,kivy"})
        check("repair 200", s == 200)
        check("repair returns fixes", len(d.get("repairs") or []) >= 2)
        check("repair burned no tokens", d["usage"]["calls"] == A.USAGE["calls"])

        # autoforge needs something to work with
        s, d = http_json("POST", base + "/api/autoforge", {})
        check("autoforge with nothing -> 400", s == 400)

        s, d = http_json("GET", base + "/api/job?id=__nope__")
        check("unknown job 404", s == 404)

        # efficiency settings persist
        s, d = http_json("POST", base + "/api/config",
                         {"max_tokens": 8000, "token_budget": 50000,
                          "agent_rounds": 2, "cache": False, "auto_repair": False})
        check("efficiency settings saved", s == 200 and d.get("saved"))
        s, d = http_json("GET", base + "/api/config")
        check("max_tokens persisted", d.get("max_tokens") == 8000)
        check("token_budget persisted", d.get("token_budget") == 50000)
        check("agent_rounds persisted", d.get("agent_rounds") == 2)
        check("cache flag persisted", d.get("cache") is False)
        # clamp out-of-range values rather than trusting the client
        http_json("POST", base + "/api/config", {"max_tokens": 999999, "agent_rounds": 99})
        s, d = http_json("GET", base + "/api/config")
        check("max_tokens clamped", d.get("max_tokens") <= 32000)
        check("agent_rounds clamped", d.get("agent_rounds") <= 6)
        A.CONFIG["cache"] = True
        A.CONFIG["auto_repair"] = True
        A.CONFIG["token_budget"] = 0

        s, d = http_json("POST", base + "/api/cache_clear")
        check("cache clear 200", s == 200 and "removed" in d)

        # the UI must actually reference every route it calls
        s, html = http_raw("GET", base + "/")
        page = html.decode()
        for route in ("/api/lint", "/api/repair", "/api/autoforge", "/api/usage",
                      "/api/manual", "/api/job", "/api/cache_clear"):
            check("UI wires up %s" % route, route in page)
    finally:
        srv.shutdown()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
    run_parser_cases()
    run_url_tests()
    run_spec_tests()
    hammer_parser(n)
    run_http_pipeline()
    run_buildozer_missing_path()
    run_v2_endpoints()
    run_kit_api_tests()
    run_analysis_tests()
    run_repair_tests()
    run_token_tests()
    run_v3_endpoints()
    print()
    print("=" * 50)
    print("PASS: %d   FAIL: %d" % (PASS, FAIL))
    if FAILS:
        print("FAILURES:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)
