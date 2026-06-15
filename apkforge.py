#!/usr/bin/env python3
"""
THE DAWG // APK FORGE
Describe an Android app -> forge a complete single-file Kivy app -> compile to a
real .apk with Buildozer, all locally on this machine.

Stdlib-only server + browser UI. SiliconFlow/DeepSeek-V4-Flash primary, Groq fallback.
Keys are set in the in-app Settings (gear) or via env (SILICONFLOW_API_KEY / GROQ_API_KEY).
"""

import os
import re
import io
import glob
import json
import uuid
import time
import zipfile
import threading
import warnings
import shutil
import subprocess
import webbrowser
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------- config
HOST = "127.0.0.1"
PORT = 8731

SF_URL = "https://api.siliconflow.cn/v1/chat/completions"
SF_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

VERSION = "1.0"

WORKDIR = os.path.expanduser("~/AndroDawg")
PROJECTS = os.path.join(WORKDIR, "projects")

# arm64 only -> covers every modern phone and halves build time.
# add ,armeabi-v7a here if you ever need 32-bit.
ANDROID_ARCHS = "arm64-v8a"

BUILDS = {}  # build_id -> {"log":[...], "status":"running|done|failed", "apk":path|None}

# ----------------------------------------------------------------- settings store
CONFIG_DIR = os.path.expanduser("~/.androdawg")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "sf_key": "", "groq_key": "",
    "sf_model": SF_MODEL, "sf_url": SF_URL,
    "groq_model": GROQ_MODEL, "groq_url": GROQ_URL,
}
CONFIG = dict(DEFAULT_CONFIG)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for k in DEFAULT_CONFIG:
                    if k in stored and stored[k] is not None:
                        cfg[k] = stored[k]
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        try:
            os.chmod(CONFIG_PATH, 0o600)  # keys are secrets
        except Exception:
            pass
        return True
    except Exception:
        return False


def sf_key():
    return (CONFIG.get("sf_key") or os.environ.get("SILICONFLOW_API_KEY", "")).strip()


def groq_key():
    return (CONFIG.get("groq_key") or os.environ.get("GROQ_API_KEY", "")).strip()


# ----------------------------------------------------------------- prompt
SYSTEM_PROMPT = """You are The Dawg (APK edition), an elite Android app smith. The user describes an Android app or tool; you forge a COMPLETE, runnable, single-file Kivy app that will be cross-compiled into an .apk with Buildozer / python-for-android on a Linux box.

HARD RULES
- Output a single self-contained Kivy app as main.py. No placeholders, no TODO, no "...". Real working code only, top to bottom.
- The ONLY GUI toolkit that survives the python-for-android pipeline is Kivy. Use Kivy. NEVER tkinter/PyQt/PySide/GTK/curses.
- Default orientation is portrait unless the user asks otherwise.
- Do NOT rely on a hardware keyboard. Drive everything with touch (on_touch_down/move/up) and on-screen Kivy widgets/buttons.
- Guard Android-only imports with platform, and request runtime permissions only when actually needed:
    from kivy.utils import platform
    if platform == "android":
        from android.permissions import request_permissions, Permission
        request_permissions([Permission.INTERNET])
- Only use libraries with a python-for-android recipe or that are pure Python. SAFE: kivy, pillow (PIL), requests, certifi, urllib3, plyer, numpy, and the Python stdlib. If you need HTTP, use requests (declare it) or urllib from stdlib.
- Do NOT reference image/sound files that do not exist. Draw graphics with kivy.graphics (Rectangle, Ellipse, Line, Color) and generate any needed assets in code. Ship no external assets unless trivially embedded.
- Audio: kivy.core.audio.SoundLoader with wav/ogg only if you actually create the file at runtime; otherwise skip sound.
- The app launcher name is set by Buildozer, not in code.
- Persist any save data (high scores, settings, etc.) under App.get_running_app().user_data_dir -- NEVER a relative path or the cwd (Android sandboxes the working directory).
- Do NOT set Window.size or Window.fullscreen in code; orientation and sizing are handled by Buildozer. The app MUST also run on desktop (python3 main.py) for testing, so keep every android-only import behind `if platform == "android":`.
- Make it actually fun/usable: sensible touch hitboxes, clear feedback, no dead UI.

YOU ALSO DECLARE
- requirements: comma list for Buildozer's `requirements` line. ALWAYS start with python3,kivy. You may ONLY add packages from this exact set (they have python-for-android recipes): pillow, requests, certifi, urllib3, idna, plyer, numpy. NEVER put any other package name in requirements -- if the app would need something else, implement it with the Python stdlib or Kivy instead. Stdlib-only needs nothing beyond python3,kivy.
- permissions: comma list of Android permissions (e.g. INTERNET, VIBRATE, RECORD_AUDIO, WRITE_EXTERNAL_STORAGE, CAMERA). Empty line if none.

OUTPUT FORMAT - emit EXACTLY these sections in this order and NOTHING else (no prose before or after, no code fences):
<<<NAME>>>
short_snake_case_slug
<<<TITLE>>>
Human Readable App Name
<<<ORIENTATION>>>
portrait
<<<REQUIREMENTS>>>
python3,kivy
<<<PERMISSIONS>>>
INTERNET
<<<MAIN_PY>>>
(full main.py here, raw, no fences)
<<<NOTES>>>
one or two terse lines: what it does / how to play or use it
<<<END>>>
"""

# ----------------------------------------------------------------- spec
SPEC_TEMPLATE = """[app]
title = {title}
package.name = {package}
package.domain = org.thepriest
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,wav,ogg,mp3,json
source.exclude_dirs = .buildozer,bin,.git,__pycache__
version = 1.0
requirements = {requirements}
orientation = {orientation}
fullscreen = 0
android.permissions = {permissions}
android.api = 34
android.minapi = 24
android.archs = {archs}
android.allow_backup = 1
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
"""

# ----------------------------------------------------------------- helpers
def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (s or "").strip()).strip("_").lower()
    return s or "app"


JAVA_RESERVED = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
    "const", "continue", "default", "do", "double", "else", "enum", "extends", "final",
    "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int",
    "interface", "long", "native", "new", "package", "private", "protected", "public",
    "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
    "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false", "null",
}


def safe_package(name):
    """A valid Android/Java package segment: [a-z][a-z0-9_]*, never a digit-start or keyword."""
    s = re.sub(r"[^a-z0-9_]", "", slugify(name)).strip("_")
    if not s:
        s = "app"
    if s[0].isdigit():
        s = "a" + s
    if s in JAVA_RESERVED:
        s = s + "_app"
    return s


def safe_title(t):
    """Single-line title that can't break the .spec ini file."""
    t = re.sub(r"[\r\n\t]+", " ", (t or "")).strip()
    t = t.replace("[", "(").replace("]", ")")
    return t[:60] or "App"


def clean_perms(p):
    parts = [x.strip().upper() for x in (p or "").replace(";", ",").split(",") if x.strip()]
    return ",".join(dict.fromkeys(parts))


def fix_requirements(req):
    parts = [x.strip() for x in (req or "").replace(";", ",").split(",") if x.strip()]
    low = [x.lower() for x in parts]
    if "python3" not in low:
        parts.insert(0, "python3")
        low = [x.lower() for x in parts]
    if "kivy" not in low:
        parts.append("kivy")
    return ",".join(parts)


def strip_fence(s):
    s = (s or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def syntax_check(code):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(code, "main.py", "exec")
        return True, "syntax OK"
    except SyntaxError as e:
        return False, "SyntaxError: %s (line %s)" % (e.msg, e.lineno)


MARKERS = ["<<<NAME>>>", "<<<TITLE>>>", "<<<ORIENTATION>>>", "<<<REQUIREMENTS>>>",
           "<<<PERMISSIONS>>>", "<<<MAIN_PY>>>", "<<<NOTES>>>", "<<<END>>>"]


def parse_sections(text):
    found = []
    for mk in MARKERS:
        i = text.find(mk)
        if i != -1:
            found.append((i, mk))
    found.sort()
    out = {}
    for idx, (i, mk) in enumerate(found):
        start = i + len(mk)
        end = found[idx + 1][0] if idx + 1 < len(found) else len(text)
        key = mk.strip("<>").lower()
        out[key] = text[start:end].strip()
    return out


def make_spec(title, package, requirements, permissions, orientation):
    return SPEC_TEMPLATE.format(
        title=safe_title(title),
        package=safe_package(package),
        requirements=requirements,
        permissions=permissions,
        orientation=orientation,
        archs=ANDROID_ARCHS,
    )


# ----------------------------------------------------------------- validation
SAFE_REQS = {
    "python3", "kivy", "kivymd", "pillow", "requests", "certifi", "urllib3",
    "chardet", "charset-normalizer", "idna", "numpy", "plyer", "openssl",
    "android", "pyjnius", "sdl2", "cython", "setuptools", "six", "pyyaml",
}

BAD_IMPORTS = {
    "tkinter": "Tkinter", "PyQt5": "PyQt5", "PyQt6": "PyQt6",
    "PySide2": "PySide2", "PySide6": "PySide6", "wx": "wxPython", "curses": "curses",
}


def validate_code(code, requirements):
    """Return (errors, warnings). Errors are guaranteed build-killers."""
    errors, warnings = [], []
    for mod, name in BAD_IMPORTS.items():
        if re.search(r"(?m)^\s*(?:import|from)\s+" + re.escape(mod) + r"\b", code):
            errors.append("uses %s -> won't build with python-for-android (Kivy only)" % name)
    if re.search(r"(?m)^\s*(?:import\s+gi\b|from\s+gi\b)", code):
        errors.append("uses GTK (gi) -> won't build (Kivy only)")
    if not re.search(r"class\s+\w+\s*\(\s*App\s*\)", code):
        warnings.append("no `class X(App)` found -> the app may not launch")
    if ".run()" not in code:
        warnings.append("no `.run()` call found -> the app may not start")
    if re.search(r"(?m)^\s*(?:import|from)\s+pygame\b", code):
        warnings.append("imports pygame -> recipe is flaky on p4a; prefer pure Kivy")
    for r in [x.strip().lower() for x in (requirements or "").split(",") if x.strip()]:
        if r not in SAFE_REQS:
            warnings.append("requirement '%s' has no known p4a recipe -> build may fail" % r)
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))


def build_forge_payload(text, desc):
    """Parse a model response into a forge payload. Never raises; always returns a dict."""
    text = text or ""
    sec = parse_sections(text)
    main_py = strip_fence(sec.get("main_py", ""))
    if not main_py:
        m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            main_py = m.group(1).strip()
    if not main_py and ("class" in text and "App" in text and "def " in text):
        main_py = text.strip()
    if not main_py:
        return {"ok": False, "raw": text, "error": "model returned no usable main.py"}
    fallback_name = slugify(" ".join((desc or "app").split()[:4]))
    name = slugify(sec.get("name", "") or fallback_name)
    title = (sec.get("title", "") or name.replace("_", " ").title()).strip()
    orientation = (sec.get("orientation", "") or "portrait").strip().lower()
    if orientation not in ("portrait", "landscape", "all"):
        orientation = "portrait"
    requirements = fix_requirements(sec.get("requirements", ""))
    permissions = clean_perms(sec.get("permissions", ""))
    notes = sec.get("notes", "")
    syntax_ok, syntax_msg = syntax_check(main_py)
    errors, warnings = validate_code(main_py, requirements)
    return {
        "ok": True, "name": name, "title": title, "orientation": orientation,
        "requirements": requirements, "permissions": permissions, "notes": notes,
        "main_py": main_py, "syntax_ok": syntax_ok, "syntax_msg": syntax_msg,
        "errors": errors, "warnings": warnings, "raw": text,
    }


def java_version():
    """(version_string, major_int) for the java that will run Gradle, or (None, None)."""
    jh = os.environ.get("JAVA_HOME", "")
    java = os.path.join(jh, "bin", "java") if jh else ""
    if not (java and os.path.exists(java)):
        java = shutil.which("java") or ""
    if not java:
        return None, None
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=10)
        txt = (out.stderr or "") + (out.stdout or "")
    except Exception:
        return None, None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', txt)
    if not m:
        return None, None
    major = int(m.group(1))
    if major == 1 and m.group(2):  # legacy 1.8.0 scheme
        major = int(m.group(2))
    full = re.search(r'version "([^"]+)"', txt)
    return (full.group(1) if full else str(major)), major


# Buildozer's bundled Gradle (8.x) runs on JDK 17-24; JDK 25+ (class file major 69)
# crashes it. 17 is the safe target.
GRADLE_JDK_MIN = 17
GRADLE_JDK_MAX = 24


def doctor():
    """Toolchain self-diagnosis so failures are seen before a build is started."""
    checks = []
    checks.append(["buildozer", shutil.which("buildozer") is not None])
    jver, jmaj = java_version()
    if jmaj is None:
        checks.append(["java (none) - install JDK 17", False])
    elif GRADLE_JDK_MIN <= jmaj <= GRADLE_JDK_MAX:
        checks.append(["java %s" % jver, True])
    else:
        checks.append(["java %s - Gradle needs JDK 17" % jver, False])
    checks.append(["git", shutil.which("git") is not None])
    checks.append(["zip", shutil.which("zip") is not None])
    checks.append(["unzip", shutil.which("unzip") is not None])
    checks.append(["SILICONFLOW key", bool(sf_key())])
    checks.append(["GROQ key", bool(groq_key())])
    checks.append(["~/.buildozer cache", os.path.isdir(os.path.expanduser("~/.buildozer"))])
    return checks


SMOKE_APP = '''from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button


class Root(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=40, spacing=24, **kw)
        self.n = 0
        self.lbl = Label(text="Dawg smoke test\\nyour build works",
                         halign="center", font_size="24sp")
        self.add_widget(self.lbl)
        btn = Button(text="TAP ME", size_hint=(1, 0.3), font_size="22sp")
        btn.bind(on_release=self.tap)
        self.add_widget(btn)

    def tap(self, *a):
        self.n += 1
        self.lbl.text = "Dawg smoke test\\ntaps: %d" % self.n


class SmokeApp(App):
    def build(self):
        return Root()


if __name__ == "__main__":
    SmokeApp().run()
'''

SMOKE_TEXT = (
    "<<<NAME>>>\nsmoke_test\n<<<TITLE>>>\nDawg Smoke Test\n"
    "<<<ORIENTATION>>>\nportrait\n<<<REQUIREMENTS>>>\npython3,kivy\n"
    "<<<PERMISSIONS>>>\n\n<<<MAIN_PY>>>\n" + SMOKE_APP +
    "\n<<<NOTES>>>\nTap counter. If this builds and runs on the phone, your toolchain is good.\n<<<END>>>"
)


# ----------------------------------------------------------------- AI
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"


def chat_url(base):
    """Normalize a base/endpoint into a full /chat/completions URL."""
    u = (base or "").strip().rstrip("/")
    if not u:
        return SF_URL
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
    if u.endswith("/chat/completions"):
        return u
    if "/v1" not in u:
        u += "/v1"
    return u + "/chat/completions"


def _post_json(url, key, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,  # bare urllib UA gets Cloudflare-1010 blocked (e.g. Groq)
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read().decode("utf-8"))


def call_ai(messages):
    sf = sf_key()
    gq = groq_key()
    errs = []
    if sf:
        sf_u = chat_url(CONFIG.get("sf_url") or SF_URL)
        try:
            d = _post_json(sf_u, sf, {
                "model": CONFIG.get("sf_model") or SF_MODEL, "messages": messages,
                "temperature": 0.4, "max_tokens": 8192,
            })
            return d["choices"][0]["message"]["content"], "SiliconFlow / " + (CONFIG.get("sf_model") or SF_MODEL)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                body = ""
            errs.append("SiliconFlow %s at %s: %s" % (e.code, sf_u, body))
        except Exception as e:
            errs.append("SiliconFlow (%s): %s" % (sf_u, e))
    if gq:
        gq_u = chat_url(CONFIG.get("groq_url") or GROQ_URL)
        try:
            d = _post_json(gq_u, gq, {
                "model": CONFIG.get("groq_model") or GROQ_MODEL, "messages": messages,
                "temperature": 0.4, "max_tokens": 8192,
            })
            return d["choices"][0]["message"]["content"], "Groq (fallback)"
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")[:300]
            except Exception:
                body = ""
            errs.append("Groq %s: %s" % (e.code, body))
        except Exception as e:
            errs.append("Groq: %s" % e)
    if not sf and not gq:
        raise RuntimeError("No API key. Open Settings (gear) and add your SiliconFlow key, or set SILICONFLOW_API_KEY.")
    raise RuntimeError(" | ".join(errs) or "AI call failed")


# ----------------------------------------------------------------- build
def run_build(build_id, project_dir):
    rec = BUILDS[build_id]
    logpath = os.path.join(project_dir, "build.log")
    try:
        logf = open(logpath, "w")
    except Exception:
        logf = None
    rec["logfile"] = logpath

    def log(line):
        rec["log"].append(line)
        if len(rec["log"]) > 6000:
            del rec["log"][:1500]
        if logf:
            try:
                logf.write(line + "\n")
                logf.flush()
            except Exception:
                pass

    log("$ cd " + project_dir)
    log("$ buildozer -v android debug")
    log("(first build downloads the Android SDK/NDK and can take 20-40 min; later builds are minutes)")
    log("full log saved to: " + logpath)
    log("")
    try:
        env = dict(os.environ, BUILDOZER_WARN_ON_ROOT="0", PYTHONUNBUFFERED="1",
                   PIP_BREAK_SYSTEM_PACKAGES="1")
        proc = subprocess.Popen(
            ["buildozer", "-v", "android", "debug"],
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        for line in proc.stdout:
            log(line.rstrip("\n"))
        proc.wait()
        if proc.returncode == 0:
            bindir = os.path.join(project_dir, "bin")
            apks = []
            if os.path.isdir(bindir):
                apks = [os.path.join(bindir, f) for f in os.listdir(bindir) if f.endswith(".apk")]
            apks.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            if apks:
                rec["apk"] = apks[0]
                rec["status"] = "done"
                log("")
                log("APK READY -> " + apks[0])
            else:
                rec["status"] = "failed"
                log("")
                log("buildozer exited 0 but no .apk in bin/ -- check the log above.")
        else:
            rec["status"] = "failed"
            log("")
            log("buildozer exited with code %s (full log: %s)" % (proc.returncode, logpath))
    except FileNotFoundError:
        rec["status"] = "failed"
        log("ERROR: buildozer not found on PATH. Run install.sh, then retry.")
    except Exception as e:
        rec["status"] = "failed"
        log("ERROR: %s" % e)
    finally:
        if logf:
            try:
                logf.close()
            except Exception:
                pass


# ----------------------------------------------------------------- server
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path.startswith("/index"):
            return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        if path == "/api/log":
            qs = parse_qs(urlparse(self.path).query)
            bid = (qs.get("id") or [""])[0]
            rec = BUILDS.get(bid)
            if not rec:
                return self._send(404, {"error": "no such build"})
            return self._send(200, {
                "status": rec["status"],
                "log": "\n".join(rec["log"][-1500:]),
                "apk": rec.get("apk"),
            })
        if path == "/api/apk":
            qs = parse_qs(urlparse(self.path).query)
            bid = (qs.get("id") or [""])[0]
            rec = BUILDS.get(bid)
            if not rec or not rec.get("apk") or not os.path.exists(rec["apk"]):
                return self._send(404, {"error": "apk not ready"})
            with open(rec["apk"], "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(rec["apk"]))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == "/api/ping":
            return self._send(200, {"app": "androdawg", "version": VERSION, "ok": True})
        if path == "/api/doctor":
            return self._send(200, {"checks": doctor()})
        if path == "/api/smoketest":
            payload = build_forge_payload(SMOKE_TEXT, "smoke test")
            payload["provider"] = "built-in (no AI)"
            return self._send(200, payload)
        if path == "/api/config":
            return self._send(200, {
                "sf_key_set": bool((CONFIG.get("sf_key") or "").strip()),
                "groq_key_set": bool((CONFIG.get("groq_key") or "").strip()),
                "sf_env": bool(os.environ.get("SILICONFLOW_API_KEY")),
                "groq_env": bool(os.environ.get("GROQ_API_KEY")),
                "sf_model": CONFIG.get("sf_model") or SF_MODEL,
                "sf_url": CONFIG.get("sf_url") or SF_URL,
                "groq_model": CONFIG.get("groq_model") or GROQ_MODEL,
                "groq_url": CONFIG.get("groq_url") or GROQ_URL,
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        ln = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(ln) if ln else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if path == "/api/forge":
            return self.handle_forge(body)
        if path == "/api/build":
            return self.handle_build(body)
        if path == "/api/config":
            return self.handle_config(body)
        if path == "/api/project_zip":
            return self.handle_project_zip(body)
        if path == "/api/quit":
            self._send(200, {"bye": True})
            threading.Timer(0.4, lambda: os._exit(0)).start()
            return
        return self._send(404, {"error": "not found"})

    def handle_config(self, body):
        # blank key field => keep current; clear flag => wipe it
        if body.get("clear_sf"):
            CONFIG["sf_key"] = ""
        elif (body.get("sf_key") or "").strip():
            CONFIG["sf_key"] = body["sf_key"].strip()
        if body.get("clear_groq"):
            CONFIG["groq_key"] = ""
        elif (body.get("groq_key") or "").strip():
            CONFIG["groq_key"] = body["groq_key"].strip()
        if (body.get("sf_model") or "").strip():
            CONFIG["sf_model"] = body["sf_model"].strip()
        if "sf_url" in body:
            CONFIG["sf_url"] = (body.get("sf_url") or "").strip() or SF_URL
        if (body.get("groq_model") or "").strip():
            CONFIG["groq_model"] = body["groq_model"].strip()
        if "groq_url" in body:
            CONFIG["groq_url"] = (body.get("groq_url") or "").strip() or GROQ_URL
        ok = save_config(CONFIG)
        return self._send(200, {
            "saved": ok,
            "sf_key_set": bool(sf_key()),
            "groq_key_set": bool(groq_key()),
        })

    def handle_project_zip(self, body):
        name = slugify(body.get("name", "app"))
        title = (body.get("title") or name).strip()
        main_py = body.get("main_py") or ""
        orientation = (body.get("orientation") or "portrait").strip().lower()
        if orientation not in ("portrait", "landscape", "all"):
            orientation = "portrait"
        requirements = fix_requirements(body.get("requirements", ""))
        permissions = clean_perms(body.get("permissions", ""))
        if not main_py.strip():
            return self._send(400, {"error": "no main_py to package"})
        spec = make_spec(title, name, requirements, permissions, orientation)
        readme = (
            "%s -- Buildozer project forged by The Dawg.\n\n"
            "Build the APK on a Linux box with the SDK/NDK:\n"
            "  cd %s\n"
            "  buildozer android debug\n\n"
            "APK lands in bin/.\n" % (title, name)
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name + "/main.py", main_py)
            z.writestr(name + "/buildozer.spec", spec)
            z.writestr(name + "/README.txt", readme)
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="%s_buildozer.zip"' % name)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_forge(self, body):
        desc = (body.get("description") or "").strip()
        history = body.get("history") or []
        if not desc:
            return self._send(400, {"error": "empty description"})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += history[-10:]
        messages += [{"role": "user", "content": desc}]
        try:
            text, provider = call_ai(messages)
        except Exception as e:
            return self._send(502, {"error": str(e)})
        payload = build_forge_payload(text, desc)
        payload["provider"] = provider
        return self._send(200, payload)

    def handle_build(self, body):
        name = slugify(body.get("name", "app"))
        title = (body.get("title") or name).strip()
        main_py = body.get("main_py") or ""
        orientation = (body.get("orientation") or "portrait").strip().lower()
        if orientation not in ("portrait", "landscape", "all"):
            orientation = "portrait"
        requirements = fix_requirements(body.get("requirements", ""))
        permissions = clean_perms(body.get("permissions", ""))
        if not main_py.strip():
            return self._send(400, {"error": "no main_py to build"})
        # preflight: never burn a 40-minute build on something guaranteed to fail
        syntax_ok, syntax_msg = syntax_check(main_py)
        if not syntax_ok:
            return self._send(400, {"error": "main.py has a syntax error: " + syntax_msg})
        errors, _ = validate_code(main_py, requirements)
        if errors:
            return self._send(400, {"error": "won't build: " + "; ".join(errors)})
        if shutil.which("buildozer") is None:
            return self._send(400, {"error": "buildozer not found on PATH. Run install.sh (or `pip install buildozer cython`), then retry."})
        jver, jmaj = java_version()
        if jmaj is not None and not (GRADLE_JDK_MIN <= jmaj <= GRADLE_JDK_MAX):
            return self._send(400, {"error":
                "Java %s is active, but Buildozer's Gradle needs JDK 17. The build would "
                "run for ages then die at the Gradle step. Fix: sudo apt install -y "
                "openjdk-17-jdk  then relaunch The Dawg (it uses JDK 17 automatically once "
                "installed)." % jver})
        project_dir = os.path.join(PROJECTS, name)
        os.makedirs(project_dir, exist_ok=True)
        if body.get("clean"):
            # wipe this project's local build cache (keeps the global SDK/NDK in ~/.buildozer)
            shutil.rmtree(os.path.join(project_dir, ".buildozer"), ignore_errors=True)
        with open(os.path.join(project_dir, "main.py"), "w") as f:
            f.write(main_py)
        spec = make_spec(title, name, requirements, permissions, orientation)
        with open(os.path.join(project_dir, "buildozer.spec"), "w") as f:
            f.write(spec)
        bid = uuid.uuid4().hex[:12]
        first = "clean rebuild (project cache wiped)" if body.get("clean") else "project: " + project_dir
        BUILDS[bid] = {"log": [first, ""], "status": "running", "apk": None}
        threading.Thread(target=run_build, args=(bid, project_dir), daemon=True).start()
        return self._send(200, {"build_id": bid, "project_dir": project_dir})


# ----------------------------------------------------------------- UI
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>THE DAWG // APK FORGE</title>
<style>
  :root{
    --bg:#080b08; --panel:#0e130e; --panel2:#121912; --line:#1d271d;
    --txt:#d6e6d6; --muted:#6e8070; --green:#49d367; --cyan:#36c7e2;
    --danger:#ff5d5d; --amber:#e8b341;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace;
    font-size:14px;line-height:1.45}
  header{display:flex;align-items:center;gap:12px;padding:14px 18px;
    border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0c110c,#080b08)}
  header .dot{width:10px;height:10px;border-radius:50%;background:var(--green);
    box-shadow:0 0 10px var(--green)}
  header h1{font-size:15px;letter-spacing:2px;margin:0;font-weight:700}
  header h1 span{color:var(--cyan)}
  header h1 .ver{color:var(--muted);font-size:10px;font-weight:400;letter-spacing:1px}
  header .sub{margin-left:auto;color:var(--muted);font-size:12px}
  main{max-width:1000px;margin:0 auto;padding:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:14px;margin-bottom:16px}
  label{display:block;color:var(--muted);font-size:12px;margin-bottom:6px;letter-spacing:1px}
  textarea,.codebox{width:100%;background:var(--panel2);color:var(--txt);
    border:1px solid var(--line);border-radius:8px;padding:11px;
    font-family:inherit;font-size:13px;resize:vertical}
  #desc{height:92px}
  .codebox{height:340px;white-space:pre;overflow:auto;tab-size:4}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  button{cursor:pointer;border:1px solid var(--line);background:var(--panel2);
    color:var(--txt);padding:10px 16px;border-radius:8px;font-family:inherit;
    font-size:13px;letter-spacing:1px;transition:.12s}
  button:hover{border-color:var(--green);box-shadow:0 0 0 1px var(--green) inset}
  button:disabled{opacity:.5;cursor:not-allowed;box-shadow:none;border-color:var(--line)}
  button.primary{background:#10220f;border-color:#1f4a1c;color:var(--green)}
  button.primary:hover{box-shadow:0 0 14px rgba(73,211,103,.35)}
  button.build{background:#0c1f24;border-color:#1d4a55;color:var(--cyan)}
  button.build:hover{box-shadow:0 0 14px rgba(54,199,226,.35)}
  .chip{padding:7px 12px;font-size:12px;border-radius:20px}
  .chip:hover{border-color:var(--cyan)}
  .meta{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 12px}
  .tag{font-size:11px;padding:4px 9px;border:1px solid var(--line);border-radius:6px;
    color:var(--muted);background:var(--panel2)}
  .tag b{color:var(--txt);font-weight:600}
  .ok{color:var(--green);border-color:#1f4a1c}
  .bad{color:var(--danger);border-color:#5a1f1f}
  .warn{color:var(--amber);border-color:#5a4a1f}
  .hidden{display:none !important}
  .gear{margin-left:12px;font-size:12px;padding:8px 12px}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.62);display:flex;
    align-items:center;justify-content:center;z-index:50}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    width:min(560px,92vw);max-height:88vh;overflow:auto;padding:18px}
  .modal h2{margin:0 0 14px;font-size:14px;letter-spacing:2px;color:var(--cyan)}
  .modal input[type=text],.modal input[type=password]{width:100%;background:var(--panel2);
    color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:10px;
    font-family:inherit;font-size:13px;margin-bottom:4px}
  .modal .field{margin-bottom:14px}
  .modal select{width:100%;background:var(--panel2);color:var(--txt);
    border:1px solid var(--line);border-radius:8px;padding:10px;font-family:inherit;font-size:13px}
  .modal .adv{margin:2px 0 8px;border-top:1px solid var(--line);padding-top:10px}
  .modal .adv summary{cursor:pointer;color:var(--muted);font-size:12px;letter-spacing:1px}
  .modal .sub2{color:var(--muted);font-size:11px;letter-spacing:0}
  .modal .clr{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:11px;margin-top:4px}
  .hint{color:var(--muted);font-size:12px;margin-top:8px}
  #log{height:360px;white-space:pre-wrap;overflow:auto;background:#060806;
    border:1px solid var(--line);border-radius:8px;padding:12px;
    font-size:12.5px;color:#bcd0bc}
  .status{font-size:13px;letter-spacing:1px;margin-bottom:8px}
  .spin{display:inline-block;animation:sp 1s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  a.dl{display:inline-block;text-decoration:none}
  .err{color:var(--danger);font-size:12px;margin-top:8px;white-space:pre-wrap}
  h3{margin:0 0 8px;font-size:13px;letter-spacing:1px;color:var(--cyan)}
</style>
</head>
<body>
<header>
  <div class="dot"></div>
  <h1>THE DAWG <span>// APK FORGE</span> <span class="ver">v1.0</span></h1>
  <div class="sub" id="prov">describe an app &rarr; forge &rarr; build .apk</div>
  <button class="gear" id="gear" onclick="openSettings()">&#9881; settings</button>
  <button class="gear" id="quit" onclick="quitApp()">&#9211; quit</button>
</header>

<main>
  <div class="card" id="doctorcard" style="padding:10px 14px;margin-bottom:14px">
    <label style="margin-bottom:8px">TOOLCHAIN</label>
    <div class="meta" id="doctor" style="margin:0"><span class="tag">checking...</span></div>
  </div>

  <div class="card">
    <label>WHAT ANDROID APP / TOOL DO YOU WANT?</label>
    <textarea id="desc" placeholder="e.g. a flappy-bird clone with tap-to-flap, score, and a restart button. portrait."></textarea>
    <div class="row" style="margin-top:10px">
      <button class="primary" id="forge" onclick="forge()">FORGE</button>
      <button id="smoke" onclick="smoke()">SMOKE-TEST APP</button>
      <span class="hint" id="forgehint"></span>
    </div>
  </div>

  <div class="card hidden" id="out">
    <div class="meta" id="meta"></div>
    <label>main.py (editable &mdash; tweak before building if you want)</label>
    <textarea class="codebox" id="code" spellcheck="false"></textarea>
    <div class="row" style="margin-top:10px">
      <span class="hint">refine:</span>
      <button class="chip" onclick="chip('Add smooth touch controls and bigger hitboxes.')">+ touch feel</button>
      <button class="chip" onclick="chip('Add sound effects generated at runtime, no external files.')">+ sound</button>
      <button class="chip" onclick="chip('Add a start screen and a game-over / restart screen.')">+ screens</button>
      <button class="chip" onclick="chip('Save and show a high score that persists between runs.')">+ high score</button>
      <button class="chip" onclick="chip('Add a settings/pause menu accessible by touch.')">+ menu</button>
      <button class="chip" onclick="chip('Make it landscape orientation.')">make landscape</button>
      <button class="chip" onclick="chip('Harden it: validate inputs and handle edge cases cleanly.')">harden</button>
      <button class="chip" onclick="chip('Rewrite using ONLY Kivy. Remove any tkinter/PyQt/PySide/GTK/curses entirely.')">kivy-only fix</button>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="build" id="build" onclick="build()">BUILD APK</button>
      <button id="zip" onclick="downloadProject()">DOWNLOAD PROJECT (.zip)</button>
      <label style="color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px"><input type="checkbox" id="clean"> clean rebuild</label>
      <span class="hint">first build pulls the SDK/NDK (~20-40 min); after that it's minutes.</span>
    </div>
  </div>

  <div class="card hidden" id="buildwrap">
    <h3>BUILD</h3>
    <div class="status" id="bstatus"></div>
    <pre id="log"></pre>
    <div class="row" style="margin-top:12px" id="dlrow"></div>
  </div>
  <div class="overlay hidden" id="settings" onclick="overlayClick(event)">
    <div class="modal">
      <h2>SETTINGS</h2>
      <div class="field">
        <label>SILICONFLOW API KEY <span class="sub2" id="sfset"></span></label>
        <input id="sf_key" type="password" placeholder="paste your key here" autocomplete="off">
        <div class="clr"><input type="checkbox" id="clear_sf"> clear stored key</div>
      </div>
      <div class="field">
        <label>MODEL</label>
        <select id="sf_model_sel" onchange="onModelChange()">
          <option value="deepseek-ai/DeepSeek-V4-Flash">DeepSeek-V4-Flash (default)</option>
          <option value="deepseek-ai/DeepSeek-V3.2">DeepSeek-V3.2</option>
          <option value="deepseek-ai/DeepSeek-V3.1-Terminus">DeepSeek-V3.1-Terminus</option>
          <option value="zai-org/GLM-4.6">GLM-4.6</option>
          <option value="Qwen/Qwen3-32B">Qwen3-32B</option>
          <option value="Qwen/Qwen3-30B-A3B">Qwen3-30B-A3B</option>
          <option value="Qwen/Qwen3.5-35B-A3B">Qwen3.5-35B-A3B</option>
          <option value="tencent/Hunyuan-A13B-Instruct">Hunyuan-A13B-Instruct</option>
          <option value="__custom__">Custom...</option>
        </select>
        <input id="sf_model_custom" type="text" class="hidden" placeholder="exact model id, e.g. deepseek-ai/DeepSeek-R1" style="margin-top:6px" autocomplete="off">
      </div>
      <div class="field">
        <label>GROQ API KEY <span class="sub2">fallback, optional</span> <span class="sub2" id="gqset"></span></label>
        <input id="groq_key" type="password" placeholder="optional" autocomplete="off">
        <div class="clr"><input type="checkbox" id="clear_groq"> clear stored key</div>
      </div>
      <details class="adv">
        <summary>Advanced (you don't need to touch these)</summary>
        <div class="field" style="margin-top:10px">
          <label>SILICONFLOW BASE URL</label>
          <input id="sf_url" type="text" value="https://api.siliconflow.cn/v1/chat/completions" autocomplete="off">
        </div>
        <div class="field">
          <label>GROQ MODEL</label>
          <input id="groq_model" type="text" value="llama-3.3-70b-versatile" autocomplete="off">
        </div>
        <div class="field">
          <label>GROQ BASE URL</label>
          <input id="groq_url" type="text" value="https://api.groq.com/openai/v1/chat/completions" autocomplete="off">
        </div>
      </details>
      <div class="row" style="margin-top:12px">
        <button class="primary" onclick="saveSettings()">SAVE</button>
        <button onclick="closeSettings()">CANCEL</button>
        <span class="hint" id="setmsg"></span>
      </div>
    </div>
  </div>
</main>

<script>
var convo = [];
var cur = null;
var poll = null;
var working = false;

function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
function setHint(t){document.getElementById('forgehint').textContent=t||'';}

function refreshButtons(){
  document.getElementById('forge').disabled = working;
  var s=document.getElementById('smoke'); if(s) s.disabled = working;
  var b=document.getElementById('build');
  if(b){ b.disabled = working || !cur || !!cur.hardFail; }
}

async function forge(text){
  var desc = (typeof text==='string'&&text) ? text : document.getElementById('desc').value.trim();
  if(!desc || working){ return; }
  working=true; refreshButtons(); setHint('forging ...');
  try{
    var r = await fetch('/api/forge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({description:desc, history:convo})});
    var d = await r.json();
    if(!r.ok || d.error){ alert('forge error: ' + (d.error||r.status)); }
    else if(!d.ok){ alert(d.error||'no code produced'); }
    else {
      convo.push({role:'user',content:desc});
      convo.push({role:'assistant',content:d.raw});
      cur = d; render(d);
    }
  }catch(e){ alert('network: ' + e); }
  setHint(''); working=false; refreshButtons();
}

function chip(t){ forge(t); }

async function smoke(){
  if(working){ return; }
  working=true; refreshButtons(); setHint('loading built-in smoke-test app ...');
  try{
    var r = await fetch('/api/smoketest');
    var d = await r.json();
    convo = []; cur = d; render(d);
  }catch(e){ alert('smoke: ' + e); }
  setHint(''); working=false; refreshButtons();
}

function render(d){
  d.hardFail = !!(d.errors && d.errors.length);
  document.getElementById('prov').textContent = d.provider || '';
  var html = ''
    + '<span class="tag"><b>'+esc(d.title)+'</b></span>'
    + '<span class="tag">slug: '+esc(d.name)+'</span>'
    + '<span class="tag">'+esc(d.orientation)+'</span>'
    + '<span class="tag">req: '+esc(d.requirements)+'</span>'
    + '<span class="tag">perms: '+esc(d.permissions||'none')+'</span>'
    + (d.syntax_ok ? '<span class="tag ok">syntax OK</span>'
                   : '<span class="tag bad">'+esc(d.syntax_msg)+'</span>');
  if(d.errors){ d.errors.forEach(function(x){ html += '<span class="tag bad">x '+esc(x)+'</span>'; }); }
  if(d.warnings){ d.warnings.forEach(function(x){ html += '<span class="tag warn">! '+esc(x)+'</span>'; }); }
  document.getElementById('meta').innerHTML = html;
  document.getElementById('code').value = d.main_py;
  document.getElementById('out').classList.remove('hidden');
  refreshButtons();
  document.getElementById('out').scrollIntoView({behavior:'smooth',block:'start'});
}

async function build(){
  if(!cur || working || cur.hardFail){ return; }
  cur.main_py = document.getElementById('code').value;  // build the possibly-edited code
  cur.clean = document.getElementById('clean').checked;
  working=true; refreshButtons();
  document.getElementById('buildwrap').classList.remove('hidden');
  document.getElementById('dlrow').innerHTML='';
  document.getElementById('bstatus').innerHTML='<span class="spin">&#9696;</span> starting build...';
  document.getElementById('log').textContent='';
  try{
    var r = await fetch('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(cur)});
    var d = await r.json();
    if(!r.ok || d.error){
      document.getElementById('bstatus').innerHTML='<span style="color:var(--danger)">build refused: '+esc(d.error||r.status)+'</span>';
      working=false; refreshButtons(); return;
    }
    document.getElementById('buildwrap').scrollIntoView({behavior:'smooth',block:'start'});
    watch(d.build_id);
  }catch(e){
    document.getElementById('bstatus').textContent='network: '+e;
    working=false; refreshButtons();
  }
}

function watch(bid){
  if(poll){ clearInterval(poll); }
  poll = setInterval(async function(){
    try{
      var r = await fetch('/api/log?id='+bid);
      var d = await r.json();
      var log = document.getElementById('log');
      var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
      log.textContent = d.log || '';
      if(atBottom){ log.scrollTop = log.scrollHeight; }
      if(d.status==='running'){
        document.getElementById('bstatus').innerHTML='<span class="spin">&#9696;</span> building...';
      }else if(d.status==='done'){
        clearInterval(poll); working=false; refreshButtons();
        document.getElementById('bstatus').innerHTML='<span style="color:var(--green)">&#10003; APK READY</span>';
        document.getElementById('dlrow').innerHTML =
          '<a class="dl" href="/api/apk?id='+bid+'"><button class="build">DOWNLOAD .APK</button></a>'+
          '<span class="hint">'+esc(d.apk||'')+'</span>';
      }else if(d.status==='failed'){
        clearInterval(poll); working=false; refreshButtons();
        document.getElementById('bstatus').innerHTML='<span style="color:var(--danger)">&#10007; BUILD FAILED &mdash; read the log</span>';
      }
    }catch(e){ /* transient, keep polling */ }
  }, 2000);
}

async function loadDoctor(){
  try{
    var r = await fetch('/api/doctor');
    var d = await r.json();
    var html = (d.checks||[]).map(function(c){
      return '<span class="tag '+(c[1]?'ok':'bad')+'">'+(c[1]?'&#10003; ':'&#10007; ')+esc(c[0])+'</span>';
    }).join('');
    document.getElementById('doctor').innerHTML = html || '<span class="tag">no checks</span>';
  }catch(e){
    document.getElementById('doctor').innerHTML='<span class="tag bad">doctor failed</span>';
  }
}

function onModelChange(){
  var sel = document.getElementById('sf_model_sel');
  var cust = document.getElementById('sf_model_custom');
  if(sel.value === '__custom__'){ cust.classList.remove('hidden'); cust.focus(); }
  else { cust.classList.add('hidden'); }
}

async function openSettings(){
  try{
    var r = await fetch('/api/config'); var d = await r.json();
    var sel = document.getElementById('sf_model_sel');
    var cust = document.getElementById('sf_model_custom');
    var m = d.sf_model || 'deepseek-ai/DeepSeek-V4-Flash';
    var found = false;
    for(var i=0;i<sel.options.length;i++){ if(sel.options[i].value===m){ found=true; break; } }
    if(found){ sel.value=m; cust.classList.add('hidden'); cust.value=''; }
    else { sel.value='__custom__'; cust.classList.remove('hidden'); cust.value=m; }
    if(d.sf_url){ document.getElementById('sf_url').value=d.sf_url; }
    if(d.groq_model){ document.getElementById('groq_model').value=d.groq_model; }
    if(d.groq_url){ document.getElementById('groq_url').value=d.groq_url; }
    document.getElementById('sf_key').value=''; document.getElementById('groq_key').value='';
    document.getElementById('clear_sf').checked=false; document.getElementById('clear_groq').checked=false;
    document.getElementById('sfset').textContent = d.sf_key_set ? '(stored)' : (d.sf_env ? '(from env)' : '(not set)');
    document.getElementById('gqset').textContent = d.groq_key_set ? '(stored)' : (d.groq_env ? '(from env)' : '');
    document.getElementById('setmsg').textContent='';
  }catch(e){}
  document.getElementById('settings').classList.remove('hidden');
}
function closeSettings(){ document.getElementById('settings').classList.add('hidden'); }
function overlayClick(e){ if(e.target && e.target.id==='settings'){ closeSettings(); } }
async function saveSettings(){
  var sel = document.getElementById('sf_model_sel');
  var model = (sel.value==='__custom__') ? document.getElementById('sf_model_custom').value.trim() : sel.value;
  var body = {
    sf_key: document.getElementById('sf_key').value,
    groq_key: document.getElementById('groq_key').value,
    sf_model: model,
    sf_url: document.getElementById('sf_url').value,
    groq_model: document.getElementById('groq_model').value,
    groq_url: document.getElementById('groq_url').value,
    clear_sf: document.getElementById('clear_sf').checked,
    clear_groq: document.getElementById('clear_groq').checked
  };
  try{
    var r = await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var d = await r.json();
    document.getElementById('setmsg').textContent = d.saved ? 'saved' : 'save failed (check ~/.androdawg perms)';
    loadDoctor();
    if(d.saved){ setTimeout(closeSettings, 500); }
  }catch(e){ document.getElementById('setmsg').textContent='error: '+e; }
}

async function downloadProject(){
  if(!cur){ return; }
  cur.main_py = document.getElementById('code').value;
  try{
    var r = await fetch('/api/project_zip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cur)});
    if(!r.ok){ var e = await r.json(); alert('zip error: '+(e.error||r.status)); return; }
    var blob = await r.blob();
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (cur.name||'app')+'_buildozer.zip';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1000);
  }catch(e){ alert('network: '+e); }
}

async function quitApp(){
  try{ await fetch('/api/quit',{method:'POST'}); }catch(e){}
  document.body.innerHTML = '<div style="color:#6e8070;font-family:ui-monospace,monospace;padding:48px;font-size:14px">The Dawg stopped. You can close this window.</div>';
  setTimeout(function(){ try{ window.close(); }catch(e){} }, 400);
}

document.getElementById('desc').addEventListener('keydown', function(e){
  if((e.ctrlKey||e.metaKey) && e.key==='Enter'){ forge(); }
});
document.addEventListener('keydown', function(e){
  if(e.key==='Escape'){ closeSettings(); }
});
loadDoctor();
refreshButtons();
</script>
</body>
</html>
"""


# ----------------------------------------------------------------- main
def launch_app_window(url):
    """Open the UI in its own frameless window (Chromium/Brave --app), not a browser tab."""
    forced = os.environ.get("DAWG_BROWSER", "").strip()
    candidates = ["brave-browser", "brave", "chromium", "chromium-browser",
                  "google-chrome", "google-chrome-stable", "microsoft-edge", "vivaldi"]
    if forced:
        candidates = [forced] + candidates
    profile = os.path.join(WORKDIR, "appwindow")
    for name in candidates:
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            subprocess.Popen(
                [exe, "--app=" + url, "--user-data-dir=" + profile,
                 "--window-size=1100,840", "--no-first-run", "--no-default-browser-check"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return name
        except Exception:
            continue
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return None


def instance_version(url):
    """Return the version of a running instance at url, or None if none/ours-not-there."""
    try:
        with urllib.request.urlopen(url + "api/ping", timeout=0.7) as r:
            d = json.loads(r.read().decode())
            if d.get("app") == "androdawg":
                return str(d.get("version", "?"))
    except Exception:
        pass
    return None


def _post_quit(url):
    try:
        req = urllib.request.Request(url + "api/quit", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def main():
    global CONFIG
    CONFIG = load_config()
    # make sure user-site bin (where buildozer installs) is found, and allow pip to
    # install into an externally-managed env (Kali / PEP 668) during the build
    local_bin = os.path.expanduser("~/.local/bin")
    if local_bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("PIP_BREAK_SYSTEM_PACKAGES", "1")
    # Force JDK 17 for the build if one is installed. Buildozer's bundled Gradle can't
    # run on JDK 25+ (Kali's default), so we point JAVA_HOME at 17 regardless of the
    # system default. If no 17 is present, the doctor + preflight will flag it.
    for pat in ("/usr/lib/jvm/temurin-17-jdk*", "/usr/lib/jvm/java-17-openjdk*",
                "/usr/lib/jvm/*-17-*", "/usr/lib/jvm/*17*"):
        hits = sorted(p for p in glob.glob(pat)
                      if os.path.isdir(p) and os.path.exists(os.path.join(p, "bin", "java")))
        if hits:
            os.environ["JAVA_HOME"] = hits[0]
            os.environ["PATH"] = os.path.join(hits[0], "bin") + os.pathsep + os.environ.get("PATH", "")
            break
    os.makedirs(PROJECTS, exist_ok=True)
    # single instance + auto-replace: if an instance is running, focus it when it's
    # the same version, or tell it to quit and take over when it's older.
    probe = "http://%s:%s/" % (HOST, PORT)
    running = instance_version(probe)
    if running is not None:
        if running == VERSION:
            print("[dawg] already running (v%s) -> focusing its window" % running)
            launch_app_window(probe)
            return
        print("[dawg] replacing older instance (v%s -> v%s)" % (running, VERSION))
        _post_quit(probe)
        time.sleep(1.2)  # let the old process release the port
    last_err = None
    srv = None
    for p in range(PORT, PORT + 12):
        try:
            srv = ThreadingHTTPServer((HOST, p), H)
            bound = p
            break
        except OSError as e:
            last_err = e
    if srv is None:
        raise SystemExit("could not bind a port: %s" % last_err)
    url = "http://%s:%s/" % (HOST, bound)
    print("[dawg] APK forge running -> " + url)
    if not sf_key() and not groq_key():
        print("[dawg] no key set yet -- add one in Settings (gear) once the window opens.")
    print("[dawg] projects + .apk land in: " + PROJECTS)
    win = launch_app_window(url)
    if win:
        print("[dawg] opened in app window via: " + win)
    else:
        print("[dawg] no chromium/brave found -- opened in your default browser.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[dawg] bye")


if __name__ == "__main__":
    main()
