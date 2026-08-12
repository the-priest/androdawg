#!/usr/bin/env python3
"""
THE DAWG // APK FORGE  v3
Describe an Android app (or write it yourself) -> forge a polished, single-file Kivy
app -> static-check it -> SELF-TEST it headless (build, tap every button, rotate,
soak) to catch crashes BEFORE the 40-min build -> repair/fix in a loop -> compile a
real .apk with Buildozer.

Stdlib-only server + browser UI. SiliconFlow primary, Groq fallback.
Keys are set in the in-app Settings (gear) or via env (SILICONFLOW_API_KEY / GROQ_API_KEY).

What's new in v3:
  - The system prompt's kit API is GENERATED from the kit's AST. v2 advertised
    Theme.BG / Theme.TXT / IconButton(glyph=) -- none of which existed -- so the model
    was being told to write code that AttributeErrors on launch. That is fixed at the
    root, and a selftest asserts prompt and kit can never drift apart again.
  - Static analysis now catches wrong Theme attrs, fake kit kwargs, undefined names,
    .kv loads, subprocess use, UI-thread sleeps, un-chained Widget.__init__ and more.
  - auto_repair(): deterministic fixes for the common faults, at ZERO token cost.
  - Token discipline: the ~305-line kit is stripped from every AI call (~2.9k tokens
    saved each way per round), history no longer replays whole past apps, responses
    are cached on disk, usage is metered from the API and a hard session budget applies.
  - The self-test is 8 real phases instead of "run it for 2 seconds".
  - /api/autoforge: forge -> repair -> lint -> self-test -> fix, looping until it
    passes or it hits a stop condition (never spending on a repeat answer).
  - MANUAL mode opens a genuinely empty file -- no kit, no scaffold.
  - Rebuilt UI: line-numbered editor, live linting, phase chips, agent rail, token meter.
"""

import os
import re
import io
import sys
import ast
import glob
import json
import math
import uuid
import time
import zlib
import struct
import shutil
import hashlib
import zipfile
import tempfile
import warnings
import threading
import subprocess
import webbrowser
import importlib.util
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

VERSION = "3.1"

WORKDIR = os.path.expanduser("~/AndroDawg")
PROJECTS = os.path.join(WORKDIR, "projects")
TESTDIR = os.path.join(WORKDIR, "testruns")
CACHEDIR = os.path.join(WORKDIR, "aicache")

# Optional sibling modules that ship next to apkforge.py: a nicer icon smith and a
# phone-frame desktop preview. Everything is guarded so the tool still runs if someone
# copied apkforge.py on its own -- it just falls back to the built-in asset generator and
# hides the PREVIEW button.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import iconsmith as _iconsmith
except Exception:
    _iconsmith = None
try:
    import devices as _devices
except Exception:
    _devices = None
_PREVIEW_PY = os.path.join(_HERE, "preview.py")

# arm64 only -> covers every modern phone (incl. ROG Phone 5S / SD888+) and halves
# build time. add ,armeabi-v7a here if you ever need to support 32-bit hardware.
ANDROID_ARCHS = "arm64-v8a"

BUILDS = {}   # build_id -> {"log":[...], "status": "...", "apk": path|None}
TESTS = {}    # test_id  -> {"log":[...], "status": "...", "summary": str}
JOBS = {}     # job_id   -> agent-loop record (steps, payload, status)

# ----------------------------------------------------------------- token accounting
# Every AI call is metered here so the UI can show exactly what a session costs and
# a hard budget can stop a runaway loop from eating the key.
USAGE = {"calls": 0, "prompt": 0, "completion": 0, "total": 0, "cached": 0, "saved": 0}
_USAGE_LOCK = threading.Lock()


def meter(prompt_tok, completion_tok, cached=False, saved=0):
    with _USAGE_LOCK:
        if cached:
            USAGE["cached"] += 1
            USAGE["saved"] += int(saved or 0)
            return
        USAGE["calls"] += 1
        USAGE["prompt"] += int(prompt_tok or 0)
        USAGE["completion"] += int(completion_tok or 0)
        USAGE["total"] = USAGE["prompt"] + USAGE["completion"]


def budget_left():
    """Tokens remaining this session, or None when no budget is set."""
    cap = int(CONFIG.get("token_budget") or 0)
    if cap <= 0:
        return None
    return max(0, cap - USAGE["total"])


def check_budget():
    left = budget_left()
    if left is not None and left <= 0:
        raise RuntimeError(
            "session token budget of %s is used up (%s spent). Raise or clear the budget "
            "in Settings, or restart to reset the counter."
            % (CONFIG.get("token_budget"), USAGE["total"]))

# ----------------------------------------------------------------- settings store
CONFIG_DIR = os.path.expanduser("~/.androdawg")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "sf_key": "", "groq_key": "",
    "sf_model": SF_MODEL, "sf_url": SF_URL,
    "groq_model": GROQ_MODEL, "groq_url": GROQ_URL,
    # --- efficiency knobs (all about not burning tokens) ---
    "max_tokens": 12000,      # per-call output ceiling
    "token_budget": 0,        # 0 = unlimited; otherwise a hard session cap
    "cache": True,            # reuse identical prior responses for free
    "auto_repair": True,      # fix what we can locally before ever calling the AI
    "agent_rounds": 3,        # max fix rounds in the autoforge loop
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
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def sf_key():
    return (CONFIG.get("sf_key") or os.environ.get("SILICONFLOW_API_KEY", "")).strip()


def groq_key():
    return (CONFIG.get("groq_key") or os.environ.get("GROQ_API_KEY", "")).strip()


# ----------------------------------------------------------------- UI KIT (prepended to forged apps)
KIT_BEGIN = "# ===== DAWG UI KIT"
KIT_END = "# ===== END DAWG UI KIT ====="
KIT = '# ===== DAWG UI KIT (pure Kivy, no external deps) =====\n# A small, battle-tested component kit that makes generated apps look modern\n# instead of default-Kivy grey. Pure kivy + stdlib only -> always builds on p4a.\nimport hashlib\nfrom kivy.metrics import dp, sp\nfrom kivy.animation import Animation\nfrom kivy.clock import Clock\nfrom kivy.properties import ListProperty\nfrom kivy.graphics import Color, RoundedRectangle, Rectangle, Line\nfrom kivy.uix.widget import Widget\nfrom kivy.uix.label import Label\nfrom kivy.uix.button import Button\nfrom kivy.uix.boxlayout import BoxLayout\nfrom kivy.uix.floatlayout import FloatLayout\nfrom kivy.uix.textinput import TextInput\nfrom kivy.core.window import Window\n\n\ndef _hx(h):\n    h = h.lstrip("#")\n    if len(h) == 6:\n        h += "ff"\n    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4, 6))\n\n\ndef _mix(a, b, t):\n    return tuple(a[i] * (1 - t) + b[i] * t for i in range(4))\n\n\ndef _kit_col(c, fallback):\n    """Coerce anything into a valid RGBA colour, so a stray bool/None/str can\'t crash a\n    widget at build time. Accepts a 3/4-tuple, a #hex string, or falls back."""\n    try:\n        if isinstance(c, str) and c.strip():\n            return _hx(c)\n        if isinstance(c, (tuple, list)) and len(c) in (3, 4):\n            return tuple(list(c) + [1])[:4]\n    except Exception:\n        pass\n    return fallback\n\n\ndef _kit_int(v, fallback):\n    """Coerce to a positive int (for counts like gradient strips)."""\n    try:\n        v = int(v)\n        return v if v > 0 else fallback\n    except Exception:\n        return fallback\n\n\nclass Theme:\n    """Central palette. accent is derived from a seed so each app feels distinct."""\n    bg        = _hx("#0c0f14")\n    bg2       = _hx("#11161f")\n    surface   = _hx("#161d29")\n    surface2  = _hx("#1d2736")\n    line      = _hx("#27303f")\n    text      = _hx("#eaf0f7")\n    muted     = _hx("#8b97a8")\n    primary   = _hx("#4f7cff")\n    primary_d = _hx("#3b63e0")\n    accent    = _hx("#27e0b0")\n    danger    = _hx("#ff5d6c")\n    ok        = _hx("#37d98a")\n    warn      = _hx("#ffba49")\n    on_primary = _hx("#ffffff")\n    radius    = dp(16)\n    pad       = dp(18)\n    gap       = dp(12)\n\n    @classmethod\n    def seed(cls, name):\n        """Tint the accent/primary from an app name so identity is consistent."""\n        if not name:\n            return\n        hue = int(hashlib.sha256(name.encode()).hexdigest(), 16) % 360\n        cls.primary = cls._hsl(hue, 0.78, 0.62)\n        cls.primary_d = cls._hsl(hue, 0.78, 0.50)\n        cls.accent = cls._hsl((hue + 150) % 360, 0.70, 0.58)\n\n    @staticmethod\n    def _hsl(h, s, l):\n        import colorsys\n        r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)\n        return (r, g, b, 1)\n\n\nclass GradientBackground(FloatLayout):\n    """Full-bleed vertical gradient drawn as strips (no texture flip surprises)."""\n    def __init__(self, top=None, bottom=None, strips=48, **kw):\n        super().__init__(**kw)\n        self._top = _kit_col(top, Theme.bg)\n        self._bottom = _kit_col(bottom, Theme.bg2)\n        self._strips = _kit_int(strips, 48)\n        self.bind(pos=self._redraw, size=self._redraw)\n        self._redraw()\n\n    def _redraw(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            n = self._strips\n            for i in range(n):\n                Color(*_mix(self._top, self._bottom, i / (n - 1)))\n                Rectangle(pos=(self.x, self.y + self.height * (1 - (i + 1) / n)),\n                          size=(self.width, self.height / n + 1))\n\n\nclass _Rounded:\n    """Mixin: paints a rounded background + optional border into canvas.before."""\n    def _paint(self, fill, radius=None, border=None, bw=1.2):\n        self._fill = fill\n        self._radius = radius if radius is not None else Theme.radius\n        self._border = border\n        self._bw = bw\n        self.bind(pos=self._rp, size=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*self._fill)\n            self._rr = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])\n            if self._border:\n                Color(*self._border)\n                Line(rounded_rectangle=(self.x, self.y, self.width, self.height,\n                                        self._radius), width=self._bw)\n\n\nclass Card(BoxLayout, _Rounded):\n    """A rounded surface panel with a faint border + soft drop shadow."""\n    def __init__(self, fill=None, radius=None, padding=None, **kw):\n        kw.setdefault("orientation", "vertical")\n        kw.setdefault("padding", padding if padding is not None else Theme.pad)\n        kw.setdefault("spacing", Theme.gap)\n        super().__init__(**kw)\n        self._paint(_kit_col(fill, Theme.surface), radius, border=Theme.line)\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            # soft shadow: two translucent offset rects\n            Color(0, 0, 0, 0.22)\n            RoundedRectangle(pos=(self.x, self.y - dp(3)),\n                             size=(self.width, self.height), radius=[self._radius])\n            Color(*self._fill)\n            RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])\n            if self._border:\n                Color(*self._border)\n                Line(rounded_rectangle=(self.x, self.y, self.width, self.height,\n                                        self._radius), width=self._bw)\n\n\nclass AppBar(BoxLayout, _Rounded):\n    """Top title bar. Use as the first child of your root."""\n    def __init__(self, title="App", subtitle="", **kw):\n        kw.setdefault("orientation", "vertical")\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(64) if not subtitle else dp(78))\n        kw.setdefault("padding", (Theme.pad, dp(8)))\n        super().__init__(**kw)\n        self._paint(Theme.surface, radius=0, border=None)\n        t = Label(text=title, font_size=sp(20), bold=True, color=Theme.text,\n                  halign="left", valign="middle", shorten=True)\n        t.bind(size=lambda w, *a: setattr(w, "text_size", w.size))\n        self.add_widget(t)\n        if subtitle:\n            s = Label(text=subtitle, font_size=sp(12), color=Theme.muted,\n                      halign="left", valign="middle")\n            s.bind(size=lambda w, *a: setattr(w, "text_size", w.size))\n            self.add_widget(s)\n\n\nclass PillButton(Button):\n    """Rounded, animated, theme-coloured button. variant: \'primary\'|\'ghost\'|\'danger\'."""\n    cur = ListProperty([0, 0, 0, 0])  # animated fill colour\n\n    def __init__(self, text="", variant="primary", radius=None, **kw):\n        kw.setdefault("font_size", sp(16))\n        kw.setdefault("bold", True)\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(52))\n        super().__init__(text=text, **kw)\n        self.background_normal = ""\n        self.background_down = ""\n        self.background_color = (0, 0, 0, 0)\n        self._radius = radius if radius is not None else dp(14)\n        self._variant = variant\n        self._set_colors()\n        self.bind(pos=self._rp, size=self._rp, cur=self._rp,\n                  on_press=self._down, on_release=self._up)\n        self._rp()\n\n    def _set_colors(self):\n        if self._variant == "ghost":\n            self._base = (0, 0, 0, 0); self._edge = Theme.line; self.color = Theme.text\n        elif self._variant == "danger":\n            self._base = Theme.danger; self._edge = None; self.color = (1, 1, 1, 1)\n        else:\n            self._base = Theme.primary; self._edge = None; self.color = Theme.on_primary\n        self.cur = list(self._base)\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*self.cur)\n            RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])\n            if self._edge:\n                Color(*self._edge)\n                Line(rounded_rectangle=(self.x, self.y, self.width, self.height,\n                                        self._radius), width=1.3)\n\n    def _down(self, *a):\n        target = _mix(self._base, (1, 1, 1, 1), 0.18) if self._variant != "ghost" \\\n            else (1, 1, 1, 0.08)\n        Animation.cancel_all(self, "cur")\n        Animation(cur=list(target), d=0.06).start(self)\n\n    def _up(self, *a):\n        Animation.cancel_all(self, "cur")\n        Animation(cur=list(self._base), d=0.12).start(self)\n\n\nclass IconButton(Button):\n    """Circular icon/text button."""\n    def __init__(self, text="+", diameter=dp(48), variant="primary", **kw):\n        kw.setdefault("font_size", sp(20))\n        kw.setdefault("bold", True)\n        kw.setdefault("size_hint", (None, None))\n        kw.setdefault("size", (diameter, diameter))\n        super().__init__(text=text, **kw)\n        self.background_normal = ""; self.background_down = ""\n        self.background_color = (0, 0, 0, 0)\n        self._variant = variant\n        self.color = Theme.on_primary if variant == "primary" else Theme.text\n        self.bind(pos=self._rp, size=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        d = min(self.width, self.height)\n        with self.canvas.before:\n            Color(*(Theme.primary if self._variant == "primary" else Theme.surface2))\n            RoundedRectangle(pos=self.pos, size=(d, d), radius=[d / 2.0])\n\n\nclass TextField(TextInput):\n    """Rounded, padded, theme-coloured single/multi-line input."""\n    def __init__(self, hint="", **kw):\n        kw.setdefault("multiline", False)\n        kw.setdefault("font_size", sp(16))\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(50))\n        kw.setdefault("padding", (dp(14), dp(13)))\n        super().__init__(**kw)\n        self.background_normal = ""; self.background_active = ""\n        self.background_color = (0, 0, 0, 0)\n        self.foreground_color = Theme.text\n        self.cursor_color = Theme.primary\n        self.hint_text = hint\n        self.hint_text_color = Theme.muted\n        self.bind(pos=self._rp, size=self._rp, focus=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*Theme.surface2)\n            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])\n            Color(*(Theme.primary if self.focus else Theme.line))\n            Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(12)),\n                 width=1.4 if self.focus else 1.1)\n\n\nclass Divider(Widget):\n    def __init__(self, **kw):\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(1))\n        super().__init__(**kw)\n        self.bind(pos=self._rp, size=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*Theme.line)\n            Rectangle(pos=self.pos, size=self.size)\n\n\ndef heading(text, size=24, **kw):\n    kw.setdefault("halign", "left"); kw.setdefault("valign", "middle")\n    l = Label(text=text, font_size=sp(size), bold=True, color=Theme.text,\n              size_hint_y=None, **kw)\n    l.bind(width=lambda w, *a: setattr(w, "text_size", (w.width, None)),\n           texture_size=lambda w, *a: setattr(w, "height", w.texture_size[1] + dp(6)))\n    return l\n\n\ndef body(text, muted=True, size=14, **kw):\n    kw.setdefault("halign", "left"); kw.setdefault("valign", "top")\n    l = Label(text=text, font_size=sp(size),\n              color=Theme.muted if muted else Theme.text,\n              size_hint_y=None, **kw)\n    l.bind(width=lambda w, *a: setattr(w, "text_size", (w.width, None)),\n           texture_size=lambda w, *a: setattr(w, "height", w.texture_size[1]))\n    return l\n\n\ndef toast(message, duration=1.6):\n    """Floating, auto-dismissing message at the bottom of the window."""\n    lbl = Label(text=message, color=Theme.text, font_size=sp(14),\n                size_hint=(None, None), padding=(dp(16), dp(10)))\n    lbl.texture_update()\n    lbl.size = (lbl.texture_size[0] + dp(32), lbl.texture_size[1] + dp(20))\n    with lbl.canvas.before:\n        Color(*Theme.surface2)\n        r = RoundedRectangle(radius=[dp(12)])\n    def _sync(*a):\n        r.pos = lbl.pos; r.size = lbl.size\n    lbl.bind(pos=_sync, size=_sync)\n    lbl.pos = ((Window.width - lbl.width) / 2, dp(60))\n    Window.add_widget(lbl)\n    def _gone(*a):\n        try:\n            Window.remove_widget(lbl)\n        except Exception:\n            pass\n    Clock.schedule_once(_gone, duration)\n# ===== END DAWG UI KIT =====\n'


def ensure_kit(code):
    """If the kit markers are present, splice the canonical kit back in (self-heal)."""
    if KIT_BEGIN in code and KIT_END in code:
        i = code.index(KIT_BEGIN)
        j = code.index(KIT_END) + len(KIT_END)
        return code[:i] + KIT.strip() + code[j:]
    return code


def with_kit(app_code):
    """Prepend the canonical kit unless it's already there."""
    if KIT_BEGIN in app_code:
        return ensure_kit(app_code)
    return KIT.strip() + "\n\n\n" + app_code.strip() + "\n"


def strip_kit(full_code):
    """Inverse of with_kit: return the APP portion only.

    This is the single biggest token saver in the app. The kit is ~9 KB; sending it to
    the model on every fix/polish round and having the model echo it back burned ~5k
    tokens each way for zero benefit -- the kit never changes.
    """
    code = full_code or ""
    if KIT_END in code:
        return code.split(KIT_END, 1)[1].lstrip("\n")
    return code


def kit_line_offset():
    """How many lines the kit occupies, so editor line numbers can be translated."""
    return len(KIT.strip().splitlines())


# ----------------------------------------------------------------- kit introspection
# The v2 system prompt described a kit API that did not exist (Theme.BG / Theme.TXT /
# IconButton(glyph=...)), so a large share of forged apps died with AttributeError or
# TypeError on launch. The prompt is now GENERATED from the kit source, so the two can
# never drift again, and the same introspection powers a static check.
def _parse_kit():
    info = {"theme_attrs": set(), "names": set(), "ctor_kwargs": {}, "funcs": {},
            "imported": set()}
    try:
        tree = ast.parse(KIT)
    except Exception:
        return info
    # names the kit itself imports are in scope for the app spliced beneath it
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                info["imported"].add(al.asname or al.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for al in node.names:
                info["imported"].add(al.asname or al.name)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            info["names"].add(node.name)
            if node.name == "Theme":
                for sub in node.body:
                    if isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                info["theme_attrs"].add(t.id)
                    elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        info["theme_attrs"].add(sub.name)
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                    kws = [a.arg for a in sub.args.args[1:]]
                    kws += [a.arg for a in getattr(sub.args, "kwonlyargs", [])]
                    info["ctor_kwargs"][node.name] = set(kws)
        elif isinstance(node, ast.FunctionDef):
            info["names"].add(node.name)
            info["funcs"][node.name] = [a.arg for a in node.args.args]
    return info


KIT_INFO = _parse_kit()
# names a generated app may reference without defining them (they come from the kit)
KIT_PUBLIC = sorted(n for n in KIT_INFO["names"] if not n.startswith("_"))
THEME_ATTRS = sorted(a for a in KIT_INFO["theme_attrs"] if not a.startswith("_"))

# every widget the kit ships takes **kw straight through to its Kivy base, so only
# flag a kwarg when it is neither a kit ctor arg nor a plausible Kivy property.
KIVY_COMMON_KW = {
    "size_hint", "size_hint_x", "size_hint_y", "size", "width", "height", "pos",
    "pos_hint", "x", "y", "orientation", "padding", "spacing", "text", "font_size",
    "color", "bold", "italic", "halign", "valign", "text_size", "opacity", "disabled",
    "multiline", "hint_text", "password", "readonly", "input_filter", "markup",
    "id", "canvas", "cols", "rows", "on_release", "on_press", "on_text_validate",
    "background_color", "shorten", "line_height", "font_name", "max_lines",
}


def kit_api_reference():
    """The kit's real API, rendered for the system prompt. Generated, never hand-typed."""
    L = []
    L.append("- Theme : palette class. Attributes (each an rgba tuple unless noted): "
             + ", ".join("Theme." + a for a in THEME_ATTRS if a not in ("seed",)))
    L.append("    Theme.radius / Theme.pad / Theme.gap are dp numbers, not colours.")
    L.append("    Call Theme.seed(\"Your App Name\") ONCE at startup to derive a unique "
             "primary/accent from the name.")
    sigs = [
        ("GradientBackground", "FloatLayout that paints a vertical gradient. Use as your root."),
        ("AppBar", "top bar."),
        ("Card", "rounded raised surface (a BoxLayout)."),
        ("PillButton", "rounded button; bind on_release."),
        ("IconButton", "round compact button."),
        ("TextField", "rounded text input; read .text."),
        ("Divider", "thin separator line."),
    ]
    for name, blurb in sigs:
        kw = KIT_INFO["ctor_kwargs"].get(name, set())
        args = ", ".join(sorted(kw)) if kw else ""
        L.append("- %s(%s) : %s" % (name, args + (", **kw" if args else "**kw"), blurb))
    for fn in ("heading", "body", "toast"):
        if fn in KIT_INFO["funcs"]:
            L.append("- %s(%s)" % (fn, ", ".join(KIT_INFO["funcs"][fn])))
    return "\n".join(L)


KIT_API = kit_api_reference()

# ----------------------------------------------------------------- icon + splash (pure stdlib)
def _png_bytes(w, h, buf):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw.extend(buf[y * stride:(y + 1) * stride])
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _hsl(h, s, l):
    import colorsys
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return (int(r * 255), int(g * 255), int(b * 255))


def _palette(name):
    hue = int(hashlib.sha256((name or "app").encode()).hexdigest(), 16) % 360
    top = _hsl(hue, 0.72, 0.60)
    bot = _hsl((hue + 28) % 360, 0.80, 0.42)
    emblem = _hsl((hue + 150) % 360, 0.18, 0.97)
    bg = _hsl(hue, 0.30, 0.10)
    shape = int(hashlib.sha256((name or "x").encode()).hexdigest(), 16) % 4
    return top, bot, emblem, bg, shape


def _render(name, size, rounded, bg_solid=None):
    ss = 2
    W = H = size * ss
    top, bot, emblem, bg, shape = _palette(name)
    buf = bytearray(W * H * 4)
    cx, cy = W / 2.0, H / 2.0
    rad = W * 0.235
    for y in range(H):
        t = y / (H - 1)
        if bg_solid is None:
            r = int(top[0] * (1 - t) + bot[0] * t)
            g = int(top[1] * (1 - t) + bot[1] * t)
            b = int(top[2] * (1 - t) + bot[2] * t)
        else:
            r, g, b = bg_solid
        for x in range(W):
            a = 255
            if rounded:
                dx = abs(x - cx) - (W / 2.0 - rad)
                dy = abs(y - cy) - (H / 2.0 - rad)
                if dx > 0 and dy > 0:
                    d = math.hypot(dx, dy)
                    if d > rad:
                        a = 0
                    elif d > rad - 1.5 * ss:
                        a = int(255 * (rad - d) / (1.5 * ss))
            i = (y * W + x) * 4
            buf[i] = r; buf[i + 1] = g; buf[i + 2] = b; buf[i + 3] = a
    er = W * 0.26

    def blend(x, y, col, cov):
        if x < 0 or y < 0 or x >= W or y >= H:
            return
        i = (y * W + x) * 4
        if buf[i + 3] == 0 and rounded:
            return
        for k in range(3):
            buf[i + k] = int(buf[i + k] * (1 - cov) + col[k] * cov)

    def disc(ox, oy, r0, r1, col):
        x0 = max(0, int(ox - r1 - 2)); x1 = min(W, int(ox + r1 + 2))
        y0 = max(0, int(oy - r1 - 2)); y1 = min(H, int(oy + r1 + 2))
        for y in range(y0, y1):
            for x in range(x0, x1):
                d = math.hypot(x - ox, y - oy)
                cov = 0.0
                if r0 <= d <= r1:
                    cov = 1.0
                    if d > r1 - 1.5 * ss:
                        cov = (r1 - d) / (1.5 * ss)
                    elif r0 and d < r0 + 1.5 * ss:
                        cov = (d - r0) / (1.5 * ss)
                if cov > 0:
                    blend(x, y, col, max(0.0, min(1.0, cov)))

    def bars(col):
        bw = er * 0.42
        for k, hh in enumerate((0.55, 0.95, 0.7)):
            bx = cx + (k - 1) * (bw + er * 0.18)
            top_y = cy + er - er * 2 * hh
            for y in range(int(top_y), int(cy + er)):
                for x in range(int(bx - bw / 2), int(bx + bw / 2)):
                    blend(x, y, col, 1.0)

    if shape == 0:
        disc(cx, cy, er * 0.62, er, emblem); disc(cx, cy, 0, er * 0.30, emblem)
    elif shape == 1:
        disc(cx, cy, 0, er, emblem); disc(cx, cy, 0, er * 0.55, bg)
    elif shape == 2:
        bars(emblem)
    else:
        disc(cx, cy, er * 0.66, er, emblem)

    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    si = ((y * ss + sy) * W + (x * ss + sx)) * 4
                    r += buf[si]; g += buf[si + 1]; b += buf[si + 2]; a += buf[si + 3]
            n = ss * ss
            o = (y * size + x) * 4
            out[o] = r // n; out[o + 1] = g // n; out[o + 2] = b // n; out[o + 3] = a // n
    return _png_bytes(size, size, out)


def icon_png(name, size=512):
    return _render(name, size, rounded=True)


def presplash_png(name, size=720):
    _, _, _, bg, _ = _palette(name)
    return _render(name, size, rounded=False, bg_solid=bg)


def presplash_hex(name):
    if _iconsmith is not None:
        try:
            return _iconsmith.presplash_hex(name)
        except Exception:
            pass
    _, _, _, bg, _ = _palette(name)
    return "#%02x%02x%02x" % bg


def write_assets(project_dir, name):
    """Generate icon.png + presplash.png into project_dir. Returns (icon_ok, splash_ok).
    Prefers the richer iconsmith (squircle + monogram + adaptive layers) when it's present,
    and always falls back to the built-in generator so a build never fails for lack of it."""
    if _iconsmith is not None:
        try:
            return _iconsmith.write_assets(project_dir, name, size=512, full_set=True)
        except Exception:
            pass  # fall through to the built-in
    icon_ok = splash_ok = False
    try:
        with open(os.path.join(project_dir, "icon.png"), "wb") as f:
            f.write(icon_png(name, 512))
        icon_ok = True
    except Exception:
        pass
    try:
        with open(os.path.join(project_dir, "presplash.png"), "wb") as f:
            f.write(presplash_png(name, 720))
        splash_ok = True
    except Exception:
        pass
    return icon_ok, splash_ok

# ----------------------------------------------------------------- prompts
_KIT_CONTRACT = """A POLISHED UI KIT IS ALREADY DEFINED ABOVE YOUR CODE. It is NOT shown to you and it never changes. Do NOT paste it, do NOT redefine it, do NOT import it, do NOT output it -- these names are already in the module namespace and you call them directly. This is the EXACT API, copied from the kit source; using any other attribute name is an instant crash:

%s

Kit widgets pass any extra keyword straight to their Kivy base, so size_hint / pos_hint / padding / spacing / height etc. all work as normal.
PillButton variant is one of: "primary", "ghost", "danger".
""" % KIT_API

SYSTEM_PROMPT = """You are The Dawg (APK edition), an elite Android app smith. The user describes an app; you forge a COMPLETE, runnable, single-file Kivy app that gets cross-compiled to an .apk with Buildozer / python-for-android and must look like a polished Google-Play app and launch first try on a modern arm64 phone.

""" + _KIT_CONTRACT + """
HARD RULES
- Output a single self-contained app. No placeholders, no TODO, no "...". Real working code top to bottom.
- Kivy ONLY. NEVER tkinter / PyQt / PySide / GTK(gi) / wx / curses / pygame -- none of them survive python-for-android.
- Subclass App, but NEVER name your class `App`. `class App(App):` shadows Kivy's own App and fails to launch. Name it for the app, e.g. `class AlarmClockApp(App):`, and end the file with `if __name__ == "__main__":` then `AlarmClockApp().run()`.
- Build your UI in build() returning a GradientBackground root with an AppBar + Card(s). Make it genuinely nice: clear hierarchy, generous spacing (dp), big touch targets (>= 48dp), obvious feedback on every tap. No dead grey default widgets.
- ONLY pass constructor keywords that exist. The kit constructor signatures are listed in the API reference above; passing an invented keyword (e.g. Card(fill=True), GradientBackground(strips=[])) is a crash. Kit fill/top/bottom take an RGBA colour tuple or a Theme colour, never a bool; strips is an int. The helpers heading()/body() take (text, size=...) and return a Label -- style them via that Label, don't pass layout kwargs they don't accept.
- Drive everything with touch + on-screen widgets. Do NOT assume a hardware keyboard (except TextField input).
- Guard ALL android-only imports behind platform, and request runtime permissions only when actually used:
    from kivy.utils import platform
    if platform == "android":
        from android.permissions import request_permissions, Permission
        request_permissions([Permission.INTERNET])
- The app MUST also run on desktop (python3 main.py) so it can be test-run before building: keep every android-only import behind `if platform == "android":`.
- Persist save data under self.user_data_dir (App) / App.get_running_app().user_data_dir -- NEVER a relative path or cwd; Android sandboxes the working dir.
- Do NOT reference image/sound files that don't exist. Draw with kivy.graphics; generate any asset in code. Audio only via SoundLoader on a file you create at runtime, else skip sound.
- Do NOT set Window.size or Window.fullscreen in code (Buildozer owns sizing/orientation). The launcher name + icon are set by Buildozer, not in code.

YOU ALSO DECLARE
- requirements: comma list for Buildozer. ALWAYS python3,kivy first. You may ONLY add from this exact recipe set: pillow, requests, certifi, urllib3, idna, plyer, numpy. Anything else -> do it with the stdlib or Kivy. Stdlib-only needs just python3,kivy.
- permissions: comma list (e.g. INTERNET, VIBRATE, RECORD_AUDIO, CAMERA, WRITE_EXTERNAL_STORAGE). Empty if none. Declare INTERNET whenever you do any network call.
- build (OPTIONAL): zero or more `key = value` lines, only from this safe set (anything else is ignored):
    orientation = portrait | landscape | all
    fullscreen = 0 | 1
    presplash_color = #RRGGBB
    wakelock = 0 | 1
    api = 24..35
    minapi = 21..30

SELF-CHECK BEFORE YOU EMIT (you get no second chance -- a bad build costs 40 minutes)
1. Every name you use is either defined in your own code, imported by you, or on the kit list above. No exceptions.
2. Every Theme.<attr> you wrote is on the attribute list above, spelled exactly (they are lowercase).
3. Every widget you construct exists and its keywords are real.
4. build() returns a widget. The App subclass is instantiated and .run() is called.
5. Nothing blocks: no input(), no time.sleep in the main thread, no while-True without a Clock.
6. Every file path goes through user_data_dir.

BE ECONOMICAL: write the app once, correctly. No commentary, no alternative versions, no explanations, no restating the requirements back. Code and the required sections only -- every extra word is wasted budget.

OUTPUT FORMAT -- emit EXACTLY these sections in this order, NOTHING else (no prose, no code fences):
<<<NAME>>>
short_snake_case_slug
<<<TITLE>>>
Human Readable App Name
<<<ORIENTATION>>>
portrait
<<<REQUIREMENTS>>>
python3,kivy
<<<PERMISSIONS>>>

<<<BUILD>>>

<<<MAIN_PY>>>
(your app code here -- uses the kit above, raw, no fences, NO kit redefinition)
<<<NOTES>>>
one or two terse lines: what it does / how to use it
<<<END>>>
"""

POLISH_PROMPT = """You are The Dawg (APK edition), a senior Android UI engineer. You are handed the APP portion of a working single-file Kivy app and must make it look like a premium Google-Play app WITHOUT changing what it does or breaking it.

""" + _KIT_CONTRACT + """
You are shown the app code ONLY -- the kit sits above it and is already in scope. Return the app code ONLY. Never output the kit; if you do, the response is discarded and the round is wasted.

Make these improvements:
- Replace bare/default widgets with kit components (GradientBackground root, AppBar, Cards, PillButtons).
- Apply Theme colors; call Theme.seed(<app name>) at startup if not already.
- Tighten layout: consistent dp spacing/padding, clear visual hierarchy, big touch targets, satisfying feedback on tap.
- Keep it Kivy-only, keep all android imports guarded by platform, keep user_data_dir for saves, add no new external assets.
- Do NOT change requirements/permissions unless the restyle truly needs it.

Output EXACTLY the same section format you were given (<<<NAME>>> ... <<<END>>>) with the updated APP CODE ONLY in <<<MAIN_PY>>>. No kit, no prose, no fences."""

FIX_PROMPT = """You are The Dawg (APK edition), a Kivy/Android debugging expert. You are given the APP portion of a single-file Kivy app and an error it produced (a syntax error, a Python traceback from a real headless test run, or a static-analysis finding). Fix the ROOT CAUSE so the app launches cleanly on Android and on desktop.

""" + _KIT_CONTRACT + """
You are shown the app code ONLY -- the kit sits above it, unchanged and already in scope. Return the app code ONLY. Never output the kit.

Line numbers in a traceback refer to the FULL file (kit + app). The kit is %d lines, so app line N appears as line N+%d in a traceback -- subtract before you go looking.

Common Android launch killers to check and fix:
- A Theme attribute or kit keyword that does not exist (check the API list above -- Theme attributes are lowercase).
- Unguarded android-only imports (must be behind `if platform == "android":`).""" % (kit_line_offset(), kit_line_offset()) + """
- A third-party import not listed in requirements (add it ONLY if it's in the allowed recipe set: pillow, requests, certifi, urllib3, idna, plyer, numpy; otherwise reimplement with stdlib/Kivy).
- File writes to a relative path / cwd instead of user_data_dir.
- References to image/sound files that don't exist (draw/generate instead).
- Network calls without the INTERNET permission.
- Setting Window.size / Window.fullscreen in code.
- Exceptions in __init__ / build().

Change as little as possible -- fix the fault, do not rewrite working code, do not add features, do not explain. Output EXACTLY the same section format you were given (<<<NAME>>> ... <<<END>>>) with the corrected APP CODE ONLY in <<<MAIN_PY>>>. No kit, no prose, no fences."""

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
    """A valid Android/Java package segment: [a-z][a-z0-9_]*, never digit-start or keyword."""
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
           "<<<PERMISSIONS>>>", "<<<BUILD>>>", "<<<MAIN_PY>>>", "<<<NOTES>>>", "<<<END>>>"]


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


# build overrides the model is allowed to set -- every value is validated/clamped so a
# bad model output can never brick a 40-minute build.
def parse_build_overrides(raw):
    """Return (overrides_dict, warnings_list). Only whitelisted, validated keys survive."""
    out, warns = {}, []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if k == "orientation":
            if v.lower() in ("portrait", "landscape", "all"):
                out["orientation"] = v.lower()
            else:
                warns.append("build: bad orientation %r ignored" % v)
        elif k == "fullscreen":
            if v in ("0", "1"):
                out["fullscreen"] = v
            else:
                warns.append("build: fullscreen must be 0/1, got %r" % v)
        elif k == "wakelock":
            if v in ("0", "1"):
                out["wakelock"] = v
            else:
                warns.append("build: wakelock must be 0/1, got %r" % v)
        elif k == "presplash_color":
            if re.fullmatch(r"#[0-9a-fA-F]{6}", v or ""):
                out["presplash_color"] = v
            else:
                warns.append("build: presplash_color must be #RRGGBB, got %r" % v)
        elif k == "api":
            if v.isdigit() and 24 <= int(v) <= 35:
                out["api"] = v
            else:
                warns.append("build: api must be 24-35, got %r" % v)
        elif k == "minapi":
            if v.isdigit() and 21 <= int(v) <= 30:
                out["minapi"] = v
            else:
                warns.append("build: minapi must be 21-30, got %r" % v)
        else:
            warns.append("build: unknown key %r ignored" % k)
    return out, warns


def make_spec(title, package, requirements, permissions, orientation,
              archs=ANDROID_ARCHS, version="1.0",
              icon=False, presplash=False, presplash_color=None, overrides=None):
    """Render a buildozer.spec. New optional args default to the original v1 behavior."""
    overrides = overrides or {}
    orient = orientation if orientation in ("portrait", "landscape", "all") else "portrait"
    if overrides.get("orientation") in ("portrait", "landscape", "all"):
        orient = overrides["orientation"]
    fullscreen = overrides.get("fullscreen", "0")
    api = overrides.get("api", "34")
    minapi = overrides.get("minapi", "24")
    pcolor = presplash_color or overrides.get("presplash_color")
    wakelock = overrides.get("wakelock")

    L = []
    L.append("[app]")
    L.append("title = " + safe_title(title))
    L.append("package.name = " + safe_package(package))
    L.append("package.domain = org.thepriest")
    L.append("source.dir = .")
    L.append("source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,wav,ogg,mp3,json")
    L.append("source.exclude_dirs = .buildozer,bin,.git,__pycache__")
    L.append("version = " + str(version))
    L.append("requirements = " + requirements)
    if icon:
        L.append("icon.filename = icon.png")
    if presplash:
        L.append("presplash.filename = presplash.png")
    if pcolor:
        L.append("android.presplash_color = " + pcolor)
    L.append("orientation = " + orient)
    L.append("fullscreen = " + str(fullscreen))
    L.append("android.permissions = " + permissions)
    L.append("android.api = " + str(api))
    L.append("android.minapi = " + str(minapi))
    L.append("android.archs = " + archs)
    L.append("android.allow_backup = 1")
    if wakelock in ("0", "1"):
        L.append("android.wakelock = " + str(wakelock))
    L.append("android.accept_sdk_license = True")
    L.append("")
    L.append("[buildozer]")
    L.append("log_level = 2")
    L.append("warn_on_root = 1")
    L.append("")
    return "\n".join(L)

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

# import root -> the name it must appear as in `requirements`. Missing one of these is a
# guaranteed ImportError on device, so it's a hard error (only this tight known set).
IMPORT_TO_REQ = {
    "PIL": "pillow", "requests": "requests", "numpy": "numpy", "plyer": "plyer",
    "certifi": "certifi", "urllib3": "urllib3", "idna": "idna", "kivymd": "kivymd",
}
# android-only modules that don't exist off-device; importing them unguarded crashes desktop
# and risks a crash on launch if the import is at module top with no platform guard.
ANDROID_ONLY = {"android", "jnius", "pyjnius"}


def _android_guarded_test(test):
    """True if an `if` test looks like a platform == 'android' guard."""
    try:
        return "android" in ast.dump(test).lower()
    except Exception:
        return False


def _collect_imports(tree):
    """Return {root_module: guarded_bool} using the lowest guard state seen for each."""
    seen = {}

    def visit(node, guarded):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for a in child.names:
                    root = a.name.split(".")[0]
                    seen[root] = seen.get(root, True) and guarded
            elif isinstance(child, ast.ImportFrom):
                if child.module and child.level == 0:
                    root = child.module.split(".")[0]
                    seen[root] = seen.get(root, True) and guarded
                visit(child, guarded)
            elif isinstance(child, ast.If):
                g2 = guarded or _android_guarded_test(child.test)
                for n in child.body:
                    visit(n, g2)
                for n in child.orelse:
                    visit(n, guarded)
            elif isinstance(child, ast.Try):
                # An import inside `try:` with an except handler is effectively
                # guarded: the handler swallows the ImportError on desktop. This is
                # the canonical python-for-android pattern, so don't warn on it.
                body_guarded = guarded or bool(child.handlers)
                for n in child.body:
                    visit(n, body_guarded)
                for h in child.handlers:
                    visit(h, guarded)
                for n in child.orelse:
                    visit(n, guarded)
                for n in child.finalbody:
                    visit(n, guarded)
            else:
                visit(child, guarded)

    visit(tree, False)
    return seen


def analyze_code(code, requirements, permissions=""):
    """Rich static analysis -> list of {sev, msg, fix}. sev in error|warn|info.
    Catches the real reasons a built APK installs but won't launch."""
    issues = []
    code = code or ""
    if not code.strip():
        return issues  # an empty editor isn't a problem, it's a starting point
    reqs = set(x.strip().lower() for x in (requirements or "").split(",") if x.strip())
    perms = set(x.strip().upper() for x in (permissions or "").replace(";", ",").split(",") if x.strip())

    def add(sev, msg, fix=""):
        issues.append({"sev": sev, "msg": msg, "fix": fix})

    # --- hard incompatible toolkits (regex so it fires even on broken syntax) ---
    for mod, nm in BAD_IMPORTS.items():
        if re.search(r"(?m)^\s*(?:import|from)\s+" + re.escape(mod) + r"\b", code):
            add("error", "uses %s -- python-for-android can't build it (Kivy only)" % nm,
                "Rebuild the UI in Kivy using the kit (Card / PillButton / TextField).")
    if re.search(r"(?m)^\s*(?:import\s+gi\b|from\s+gi\b)", code):
        add("error", "uses GTK (gi) -- won't build (Kivy only)",
            "Use Kivy widgets instead of GTK.")

    # --- AST-based checks (skip cleanly if syntax is broken) ---
    tree = None
    try:
        tree = ast.parse(code)
    except Exception:
        tree = None

    if tree is not None:
        imports = _collect_imports(tree)
        # undeclared third-party recipe import -> hard error
        for root, guarded in imports.items():
            if root in IMPORT_TO_REQ:
                req = IMPORT_TO_REQ[root]
                if req not in reqs:
                    add("error",
                        "imports %s but '%s' is not in requirements -> ImportError on device"
                        % (root, req),
                        "Add %s to the requirements line." % req)
        # unguarded android-only import
        for root, guarded in imports.items():
            if root in ANDROID_ONLY and not guarded:
                add("warn",
                    "imports '%s' without an `if platform == \"android\":` guard -> crashes on desktop and risks a launch crash"
                    % root,
                    "Wrap the import in `from kivy.utils import platform` + `if platform == \"android\":`.")
        # class App / run presence.
        # The base may be aliased -- `from kivy.app import App as _KivyApp` is exactly what
        # the free repair writes to break a `class App(App)` name collision -- so collect
        # every local alias of kivy's App and accept any of them as a valid base. Without
        # this, repaired (correct!) code got flagged "no class X(App) found".
        app_aliases = {"App"}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("kivy.app"):
                for al in n.names:
                    if al.name == "App":
                        app_aliases.add(al.asname or al.name)
        has_app = any(
            isinstance(n, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id in app_aliases) or
                (isinstance(b, ast.Attribute) and b.attr == "App") for b in n.bases)
            for n in ast.walk(tree))
        if not has_app:
            add("warn", "no `class X(App)` found -> the app may not launch",
                "Define an App subclass with a build() method.")
        # `class App(App):` shadows kivy's App and fails to launch / self-test. Repair-free
        # fixes this automatically, but flag it live so it's obvious what happened.
        if re.search(r"(?m)^\s*class\s+App\s*\(\s*App\s*\)\s*:", code):
            add("warn", "your App subclass is named `App`, which shadows kivy's App -> fails to launch",
                "Rename it (e.g. class MyApp(App)) -- or just hit Repair free, which fixes this for you.")
        has_run = any(isinstance(n, ast.Attribute) and n.attr == "run" for n in ast.walk(tree))
        if not has_run and ".run(" not in code:
            add("warn", "no `.run()` call found -> the app may not start",
                "Call YourApp().run() under `if __name__ == '__main__':`.")
        # input() will hang on Android
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "input":
                add("warn", "calls input() -> no stdin on Android, the app will hang",
                    "Take input via a TextField widget instead.")
                break
        # kit-API correctness + undefined symbols + Kivy runtime footguns
        try:
            kit_checks(tree, code, add, app_only=(KIT_END not in code))
        except Exception as e:
            add("info", "kit check skipped (%s)" % e)
        try:
            kivy_checks(tree, code, add)
        except Exception as e:
            add("info", "kivy check skipped (%s)" % e)

    # --- regex heuristics (work with or without a parse) ---
    if re.search(r"(?m)Window\.(size|fullscreen)\s*=", code):
        add("warn", "sets Window.size / Window.fullscreen in code -> Buildozer owns sizing; can misrender on device",
            "Remove it; set orientation/fullscreen via the build config instead.")
    # network without INTERNET permission
    uses_net = bool(re.search(r"(?m)^\s*(?:import|from)\s+(?:requests|http\.client|urllib)\b", code) or
                    "urllib.request" in code or "urlopen(" in code or "requests." in code)
    if uses_net and "INTERNET" not in perms:
        add("warn", "makes network calls but INTERNET is not in permissions -> requests fail on device",
            "Add INTERNET to permissions.")
    # asset references that won't exist unless generated at runtime
    for m in re.finditer(r"""(?:source\s*=\s*|SoundLoader\.load\(\s*|Image\(\s*source\s*=\s*)['"]([^'"]+\.(?:png|jpg|jpeg|gif|wav|ogg|mp3|ttf|atlas))['"]""", code):
        add("warn", "references asset '%s' which won't exist in the APK unless you create it at runtime" % m.group(1),
            "Draw it with kivy.graphics or generate the file in code, or remove the reference.")
    # relative file write
    for m in re.finditer(r"""open\(\s*['"]([^'"/][^'"]*)['"]\s*,\s*['"][wa]b?['"]""", code):
        if "user_data_dir" not in code:
            add("warn", "writes to relative path '%s' -> Android sandboxes the cwd, the write fails" % m.group(1),
                "Save under self.user_data_dir / App.get_running_app().user_data_dir.")
            break

    # de-dup while preserving order
    out, seen = [], set()
    for it in issues:
        key = (it["sev"], it["msg"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


# ----------------------------------------------------------------- deeper static checks
# v2's most common failure was not a Python bug -- it was the model calling a kit member
# that doesn't exist. These checks catch that class of fault for free, before any build.
_THEME_ALIASES = {
    "BG": "bg", "BG2": "bg2", "SURFACE": "surface", "SURFACE2": "surface2",
    "TXT": "text", "TEXT": "text", "FG": "text", "MUTED": "muted", "LINE": "line",
    "ACCENT": "accent", "ACCENT2": "accent", "PRIMARY": "primary",
    "PRIMARY_D": "primary_d", "GOOD": "ok", "OK": "ok", "SUCCESS": "ok",
    "BAD": "danger", "DANGER": "danger", "ERROR": "danger", "WARN": "warn",
    "WARNING": "warn", "ON_PRIMARY": "on_primary", "RADIUS": "radius",
    "PAD": "pad", "PADDING": "pad", "GAP": "gap", "SPACING": "gap",
}
_KW_ALIASES = {
    ("IconButton", "glyph"): "text", ("IconButton", "icon"): "text",
    ("IconButton", "size"): "diameter", ("AppBar", "sub"): "subtitle",
    ("AppBar", "caption"): "subtitle", ("TextField", "placeholder"): "hint",
    ("TextField", "hint_text"): "hint", ("PillButton", "style"): "variant",
    ("PillButton", "kind"): "variant", ("Card", "bg"): "fill",
    ("Card", "color"): "fill", ("GradientBackground", "start"): "top",
    ("GradientBackground", "end"): "bottom",
}


def theme_fix_for(attr):
    """Best-guess correct spelling for a wrong Theme attribute, or None."""
    if attr in KIT_INFO["theme_attrs"]:
        return None
    up = attr.upper()
    if up in _THEME_ALIASES:
        return _THEME_ALIASES[up]
    low = attr.lower()
    if low in KIT_INFO["theme_attrs"]:
        return low
    return None


_BOUND_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _bound_names(tree):
    """Every name bound anywhere in the file. Deliberately flat (not scope-aware) so the
    undefined-name check stays conservative -- it should never cry wolf."""
    names = set()

    def bind_target(t):
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                bind_target(e)
        elif isinstance(t, ast.Starred):
            bind_target(t.value)

    for n in ast.walk(tree):
        if isinstance(n, _BOUND_NODES):
            names.add(n.name)
            a = getattr(n, "args", None)
            if a is not None:
                for arg in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs):
                    names.add(arg.arg)
                if a.vararg:
                    names.add(a.vararg.arg)
                if a.kwarg:
                    names.add(a.kwarg.arg)
        elif isinstance(n, ast.Lambda):
            a = n.args
            for arg in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs):
                names.add(arg.arg)
            if a.vararg:
                names.add(a.vararg.arg)
            if a.kwarg:
                names.add(a.kwarg.arg)
        elif isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                bind_target(t)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            bind_target(n.target)
        elif isinstance(n, (ast.comprehension,)):
            bind_target(n.target)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    bind_target(item.optional_vars)
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif isinstance(n, ast.Import):
            for al in n.names:
                names.add(al.asname or al.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for al in n.names:
                names.add(al.asname or al.name)
        elif isinstance(n, ast.NamedExpr):
            bind_target(n.target)
    return names


def _builtin_names():
    import builtins
    return set(dir(builtins)) | {"__name__", "__file__", "__doc__", "self", "cls"}


def kit_checks(tree, code, add, app_only=False):
    """Kit-API + symbol checks. app_only=True when the kit isn't in `code`."""
    # --- wrong Theme attribute (the #1 v2 crash) ---
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "Theme":
            if n.attr.startswith("_"):
                continue
            if n.attr not in KIT_INFO["theme_attrs"]:
                sug = theme_fix_for(n.attr)
                add("error",
                    "Theme.%s does not exist -> AttributeError the moment that widget is built"
                    % n.attr,
                    ("Use Theme.%s instead." % sug) if sug else
                    ("Valid attributes: " + ", ".join(THEME_ATTRS)))

    # --- wrong keyword on a kit constructor ---
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
            continue
        cls = n.func.id
        if cls not in KIT_INFO["ctor_kwargs"]:
            continue
        allowed = KIT_INFO["ctor_kwargs"][cls] | KIVY_COMMON_KW
        for kw in n.keywords:
            if kw.arg is None or kw.arg in allowed:
                continue
            alias = _KW_ALIASES.get((cls, kw.arg))
            if alias:
                add("error",
                    "%s(%s=...) is not a real keyword -> TypeError on construction"
                    % (cls, kw.arg),
                    "Use %s=... instead." % alias)
            else:
                add("warn",
                    "%s(%s=...) isn't a kit keyword; it only works if Kivy's base widget "
                    "happens to accept it" % (cls, kw.arg),
                    "Kit keywords for %s: %s" % (cls, ", ".join(sorted(KIT_INFO["ctor_kwargs"][cls])) or "(none)"))

    # --- undefined names (catches invented kit widgets and plain typos) ---
    known = _bound_names(tree) | _builtin_names() | set(KIT_PUBLIC)
    if app_only:
        # everything the kit defines OR imports is in scope for the spliced-in app
        known |= set(KIT_INFO["names"]) | set(KIT_INFO["imported"])
    seen_bad = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id in known or n.id in seen_bad or n.id.startswith("__"):
                continue
            seen_bad.add(n.id)
            hint = ""
            low = n.id.lower()
            for k in KIT_PUBLIC:
                if k.lower() == low:
                    hint = "Did you mean %s? (names are case-sensitive)" % k
                    break
            add("error", "'%s' is used but never defined or imported -> NameError" % n.id,
                hint or "Define it, import it, or use a kit component (%s)."
                % ", ".join(KIT_PUBLIC))


def kivy_checks(tree, code, add):
    """Kivy/Android runtime footguns that a syntax check can't see."""
    # single-file constraint: no .kv files can ship
    if re.search(r"Builder\.load_file\s*\(", code) or re.search(r"""['"][\w./-]+\.kv['"]""", code):
        add("error", "loads an external .kv file -> the APK is built from a single main.py",
            "Move the layout into Python, or use Builder.load_string() with an inline string.")
    # shelling out doesn't exist on Android
    for m in re.finditer(r"(?m)\b(os\.system|subprocess\.(?:run|Popen|call|check_output))\s*\(", code):
        add("error", "calls %s -> there is no shell on Android, this raises or silently does nothing" % m.group(1),
            "Do the work in Python, or use pyjnius behind an `if platform == \"android\":` guard.")
        break
    # blocking sleep on the UI thread freezes the app -> ANR
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and \
                n.func.attr == "sleep" and isinstance(n.func.value, ast.Name) and n.func.value.id == "time":
            add("warn", "calls time.sleep() -> if that's on the UI thread the app freezes and Android shows an ANR",
                "Use Clock.schedule_once / Clock.schedule_interval instead.")
            break
    # Widget subclass that forgets to chain __init__
    for n in ast.walk(tree):
        if not isinstance(n, ast.ClassDef):
            continue
        base_names = {b.id for b in n.bases if isinstance(b, ast.Name)}
        base_names |= {b.attr for b in n.bases if isinstance(b, ast.Attribute)}
        if not (base_names & {"Widget", "BoxLayout", "FloatLayout", "GridLayout", "Label",
                              "Button", "Card", "GradientBackground", "RelativeLayout",
                              "AnchorLayout", "StackLayout", "ScrollView", "Screen"}):
            continue
        for sub in n.body:
            if not (isinstance(sub, ast.FunctionDef) and sub.name == "__init__"):
                continue
            chained = False
            for c in ast.walk(sub):
                if not isinstance(c, ast.Call):
                    continue
                f = c.func
                # super().__init__(...)
                if isinstance(f, ast.Attribute) and f.attr == "__init__":
                    inner = f.value
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                            and inner.func.id == "super":
                        chained = True
                        break
                    # Base.__init__(self, ...)
                    if isinstance(inner, ast.Name):
                        chained = True
                        break
            if not chained:
                add("warn", "%s.__init__ never calls super().__init__(**kwargs) -> the widget "
                            "won't accept size_hint/pos_hint and can render wrong" % n.name,
                    "Start __init__ with super().__init__(**kwargs).")
            break
    # scheduling at 0 hammers the CPU and drains the battery
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and \
                n.func.attr == "schedule_interval" and n.args and len(n.args) > 1:
            iv = n.args[1]
            if isinstance(iv, ast.Constant) and isinstance(iv.value, (int, float)) and iv.value <= 0:
                add("warn", "Clock.schedule_interval(..., 0) runs every frame with no cap -> battery drain and jank",
                    "Use 1/60.0 for animation, or a larger interval for logic.")
                break
    # mutable default argument
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.args.defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    add("warn", "%s() has a mutable default argument -> shared state between calls, "
                                "a classic source of 'why is my list full of old data'" % n.name,
                        "Default to None and create the container inside the function.")
                    break
    # background threads touching widgets
    if re.search(r"threading\.Thread\s*\(", code) and "mainthread" not in code and \
            "Clock.schedule_once" not in code:
        add("warn", "starts a thread but never hands work back to the UI thread -> touching a widget "
                    "from a thread corrupts the render and crashes on device",
            "Import `from kivy.clock import mainthread` and decorate the UI callback, or use Clock.schedule_once.")
    # exit paths that Android doesn't like
    for m in re.finditer(r"(?m)\b(sys\.exit|exit|quit)\s*\(", code):
        add("info", "calls %s() -> on Android, use App.get_running_app().stop() so the app closes cleanly" % m.group(1),
            "Replace with App.get_running_app().stop().")
        break
    # bare except swallows the crash you're trying to debug
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler) and n.type is None:
            add("info", "uses a bare `except:` -> hides real errors and makes device crashes impossible to trace",
                "Catch `Exception as e` and log e.")
            break


def validate_code(code, requirements):
    """Return (errors, warnings) string lists. Back-compat surface for the build gate and
    the selftest; derived from analyze_code plus the original pygame/requirement checks."""
    issues = analyze_code(code, requirements, "")
    errors = [it["msg"] for it in issues if it["sev"] == "error"]
    warnings_ = [it["msg"] for it in issues if it["sev"] == "warn"]
    # extra warnings that aren't launch-blockers
    if re.search(r"(?m)^\s*(?:import|from)\s+pygame\b", code or ""):
        warnings_.append("imports pygame -> recipe is flaky on p4a; prefer pure Kivy")
    for r in [x.strip().lower() for x in (requirements or "").split(",") if x.strip()]:
        if r not in SAFE_REQS:
            warnings_.append("requirement '%s' has no known p4a recipe -> build may fail" % r)
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings_))

# ----------------------------------------------------------------- local auto-repair
# Everything this function fixes costs ZERO tokens. It runs before any AI round, so the
# expensive model is only ever asked about problems a deterministic pass can't solve.
def auto_repair(code, requirements="", permissions=""):
    """Return (code, requirements, permissions, [what_changed])."""
    fixes = []
    code = code or ""
    reqs = [x.strip() for x in (requirements or "").split(",") if x.strip()]
    perms = [x.strip().upper() for x in (permissions or "").replace(";", ",").split(",") if x.strip()]

    # 0. THE App NAME-COLLISION. Models love to write `class App(App):` -- a subclass
    #    named exactly `App`, shadowing kivy's own App. It looks fine but it breaks the
    #    self-test's `.run()` capture and can make instantiate fail with "no App subclass
    #    found". Fix it for free by aliasing the base import: the subclass keeps the name
    #    `App`, the base becomes `_KivyApp`, and everything just works.
    if re.search(r"(?m)^\s*class\s+App\s*\(\s*App\s*\)\s*:", code):
        if re.search(r"(?m)^\s*from\s+kivy\.app\s+import\s+App\s*(?:#.*)?$", code):
            code = re.sub(r"(?m)^(\s*)from\s+kivy\.app\s+import\s+App\s*(?:#.*)?$",
                          r"\1from kivy.app import App as _KivyApp", code, count=1)
        else:
            code = "from kivy.app import App as _KivyApp\n" + code
        code = re.sub(r"(?m)^(\s*class\s+App\s*\(\s*)App(\s*\)\s*:)", r"\1_KivyApp\2", code, count=1)
        fixes.append("renamed the base App import to _KivyApp so `class App(App)` stops "
                     "colliding with kivy's App (this was failing the self-test every run)")

    # 1. wrong-case / aliased Theme attributes -> the real ones
    def _theme_sub(m):
        attr = m.group(1)
        if attr in KIT_INFO["theme_attrs"]:
            return m.group(0)
        sug = theme_fix_for(attr)
        if sug:
            fixes.append("Theme.%s -> Theme.%s" % (attr, sug))
            return "Theme." + sug
        return m.group(0)
    code = re.sub(r"Theme\.([A-Za-z_][A-Za-z0-9_]*)", _theme_sub, code)

    # 2. aliased kit keywords -> the real ones
    for (cls, bad), good in _KW_ALIASES.items():
        pat = re.compile(r"(\b%s\s*\([^)]*?)\b%s\s*=" % (re.escape(cls), re.escape(bad)))
        new, n = pat.subn(lambda m: m.group(1) + good + "=", code)
        if n:
            fixes.append("%s(%s=) -> %s(%s=) x%d" % (cls, bad, cls, good, n))
            code = new

    # 3. Buildozer owns sizing -- drop Window.size / Window.fullscreen assignments
    out_lines, dropped = [], 0
    for line in code.splitlines():
        if re.match(r"^\s*Window\.(size|fullscreen)\s*=", line):
            dropped += 1
            continue
        out_lines.append(line)
    if dropped:
        code = "\n".join(out_lines)
        fixes.append("removed %d Window.size/fullscreen assignment(s) (Buildozer owns sizing)" % dropped)

    # 4. declare recipes the code actually imports
    low = {r.lower() for r in reqs}
    try:
        tree = ast.parse(code)
        imports = _collect_imports(tree)
    except Exception:
        imports = {}
    for root in imports:
        req = IMPORT_TO_REQ.get(root)
        if req and req not in low:
            reqs.append(req)
            low.add(req)
            fixes.append("added '%s' to requirements (imported as %s)" % (req, root))

    # 5. declare INTERNET when the app actually goes online
    uses_net = bool(re.search(r"(?m)^\s*(?:import|from)\s+(?:requests|http\.client|urllib)\b", code)
                    or "urlopen(" in code or "requests." in code)
    if uses_net and "INTERNET" not in perms:
        perms.append("INTERNET")
        fixes.append("added INTERNET permission (the app makes network calls)")

    # 6. a stray `if __name__` guard is the difference between running and not
    if "class" in code and "App" in code and ".run()" not in code:
        m = re.search(r"(?m)^class\s+(\w+)\s*\(\s*App\s*\)", code)
        if m:
            code = code.rstrip() + "\n\n\nif __name__ == \"__main__\":\n    %s().run()\n" % m.group(1)
            fixes.append("appended the missing %s().run() entry point" % m.group(1))

    return code, ",".join(dict.fromkeys(reqs)), ",".join(dict.fromkeys(perms)), fixes


# ----------------------------------------------------------------- kit composition
def compose(app_code):
    """Prepend the canonical kit to app-only code and re-derive validation over the full
    file (what the editor shows == what gets built). Self-heals an already-kitted file."""
    full = with_kit(app_code)
    syntax_ok, syntax_msg = syntax_check(full)
    return full, syntax_ok, syntax_msg


def build_forge_payload(text, desc):
    """Parse a model response into a forge payload. Never raises; always returns a dict.
    main_py here is the APP code as the model wrote it (kit is added later in handle_forge)."""
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
        # detect a truncated stream so the UI can say "retry" instead of "no code"
        if text.strip() and "<<<MAIN_PY>>>" in text and "<<<END>>>" not in text:
            return {"ok": False, "raw": text,
                    "error": "the model's response was cut off mid-app. Hit Forge again (or simplify the request)."}
        return {"ok": False, "raw": text, "error": "model returned no usable main.py"}
    fallback_name = slugify(" ".join((desc or "app").split()[:4]))
    name = slugify(sec.get("name", "") or fallback_name)
    title = (sec.get("title", "") or name.replace("_", " ").title()).strip()
    orientation = (sec.get("orientation", "") or "portrait").strip().lower()
    if orientation not in ("portrait", "landscape", "all"):
        orientation = "portrait"
    requirements = fix_requirements(sec.get("requirements", ""))
    permissions = clean_perms(sec.get("permissions", ""))
    build_overrides, build_warnings = parse_build_overrides(sec.get("build", ""))
    if "orientation" in build_overrides:
        orientation = build_overrides["orientation"]
    notes = sec.get("notes", "")
    syntax_ok, syntax_msg = syntax_check(main_py)
    errors, warns = validate_code(main_py, requirements)
    issues = analyze_code(main_py, requirements, permissions)
    return {
        "ok": True, "name": name, "title": title, "orientation": orientation,
        "requirements": requirements, "permissions": permissions, "notes": notes,
        "main_py": main_py, "syntax_ok": syntax_ok, "syntax_msg": syntax_msg,
        "errors": errors, "warnings": warns, "issues": issues,
        "build_overrides": build_overrides, "build_warnings": build_warnings,
        "raw": text,
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


# Buildozer's bundled Gradle (8.x) runs on JDK 17-24; JDK 25+ (class file major 69) crashes
# it. 17 is the safe target.
GRADLE_JDK_MIN = 17
GRADLE_JDK_MAX = 24


def host_has_kivy():
    try:
        return importlib.util.find_spec("kivy") is not None
    except Exception:
        return False


# ------------------------------------------------------------- distro-aware install hints
def _pkg_manager():
    """Best-effort detection of the system package manager, for actionable error hints."""
    for pm in ("pacman", "apt-get", "dnf", "zypper"):
        if shutil.which(pm):
            return "apt" if pm == "apt-get" else pm
    return ""


def pkg_hint(pacman="", apt="", dnf="", zypper=""):
    """Return a copy-pasteable install command for THIS machine's package manager.
    CachyOS/Arch (pacman) is a first-class citizen; apt/dnf/zypper are covered too."""
    pm = _pkg_manager()
    table = {"pacman": pacman, "apt": apt, "dnf": dnf, "zypper": zypper}
    cmd = table.get(pm) or apt or pacman or ""
    return cmd


# xvfb-run is a Debian wrapper; Arch's xorg-server-xvfb ships only the Xvfb binary. We
# treat EITHER as usable, and apkforge launches Xvfb itself when the wrapper is absent.
def host_can_display():
    """The self-test needs a display: a live X11 $DISPLAY, or xvfb-run, or a bare Xvfb we
    can drive ourselves (Arch/CachyOS), or a Wayland session with XWayland's Xvfb fallback."""
    return bool(os.environ.get("DISPLAY")) \
        or shutil.which("xvfb-run") is not None \
        or shutil.which("Xvfb") is not None


def host_can_test():
    return host_has_kivy() and host_can_display()


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
    # test-run capability (not required to build, but enables the pre-build crash check)
    if host_can_test():
        checks.append(["test-run (kivy + display)", True])
    elif host_has_kivy():
        checks.append(["test-run: kivy ok, no display (install xvfb)", False])
    else:
        checks.append(["test-run: kivy not on host (pip install kivy) - optional", False])
    return checks

# ----------------------------------------------------------------- smoke + templates
SMOKE_APP = '''from kivy.app import App
from kivy.metrics import dp

Theme.seed("Dawg Smoke Test")


class SmokeApp(App):
    def build(self):
        self.n = 0
        root = GradientBackground()
        root.add_widget(AppBar(title="Dawg Smoke Test", subtitle="toolchain check"))
        card = Card(orientation="vertical", padding=dp(22), spacing=dp(16),
                    size_hint=(None, None), width=dp(300), height=dp(240),
                    pos_hint={"center_x": 0.5, "center_y": 0.5})
        self.lbl = heading("Taps: 0")
        card.add_widget(self.lbl)
        card.add_widget(body("If this builds, installs and runs,\\nyour APK toolchain is good."))
        btn = PillButton(text="TAP ME", variant="primary")
        btn.bind(on_release=self.tap)
        card.add_widget(btn)
        root.add_widget(card)
        return root

    def tap(self, *a):
        self.n += 1
        self.lbl.text = "Taps: %d" % self.n
        toast("tap %d" % self.n)


if __name__ == "__main__":
    SmokeApp().run()
'''

SMOKE_TEXT = (
    "<<<NAME>>>\nsmoke_test\n<<<TITLE>>>\nDawg Smoke Test\n"
    "<<<ORIENTATION>>>\nportrait\n<<<REQUIREMENTS>>>\npython3,kivy\n"
    "<<<PERMISSIONS>>>\n\n<<<BUILD>>>\n\n<<<MAIN_PY>>>\n" + SMOKE_APP +
    "\n<<<NOTES>>>\nTap counter. If this builds and runs on the phone, your toolchain is good.\n<<<END>>>"
)

# ---- manual-mode starters (app code; the kit is prepended when served/built) ----
# Manual mode starts genuinely empty -- an empty editor, no kit, no scaffold, nothing
# to delete before you start. The starters below are opt-in from the dropdown.
_T_BLANK = ""

_T_MIN = '''from kivy.app import App
from kivy.uix.label import Label


class MyApp(App):
    def build(self):
        return Label(text="Hello from Kivy", font_size="24sp")


if __name__ == "__main__":
    MyApp().run()
'''

_T_KIT = '''from kivy.app import App
from kivy.metrics import dp

Theme.seed("My App")


class MyApp(App):
    def build(self):
        root = GradientBackground()
        root.add_widget(AppBar(title="My App", subtitle="built with the Dawg kit"))
        card = Card(orientation="vertical", padding=dp(20), spacing=dp(14),
                    size_hint=(0.9, None), height=dp(260),
                    pos_hint={"center_x": 0.5, "center_y": 0.55})
        card.add_widget(heading("Welcome"))
        card.add_widget(body("Edit this card. Add widgets, wire up buttons."))
        b = PillButton(text="PRIMARY ACTION", variant="primary")
        b.bind(on_release=lambda *a: toast("tapped"))
        card.add_widget(b)
        g = PillButton(text="SECONDARY", variant="ghost")
        card.add_widget(g)
        root.add_widget(card)
        return root


if __name__ == "__main__":
    MyApp().run()
'''

_T_FORM = '''from kivy.app import App
from kivy.metrics import dp
from kivy.uix.scrollview import ScrollView
from kivy.uix.boxlayout import BoxLayout

Theme.seed("Notes")


class MyApp(App):
    def build(self):
        root = GradientBackground()
        root.add_widget(AppBar(title="Notes", subtitle="type + add"))
        col = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12),
                        size_hint=(1, 1), pos_hint={"top": 0.86})
        self.field = TextField(hint="write a note", size_hint=(1, None), height=dp(52))
        col.add_widget(self.field)
        add = PillButton(text="ADD", variant="primary", size_hint=(1, None), height=dp(50))
        add.bind(on_release=self.add_note)
        col.add_widget(add)
        sv = ScrollView()
        self.list = BoxLayout(orientation="vertical", spacing=dp(8), size_hint=(1, None),
                              padding=(0, dp(8)))
        self.list.bind(minimum_height=self.list.setter("height"))
        sv.add_widget(self.list)
        col.add_widget(sv)
        root.add_widget(col)
        return root

    def add_note(self, *a):
        t = (self.field.text or "").strip()
        if not t:
            return
        c = Card(orientation="vertical", padding=dp(12), size_hint=(1, None), height=dp(56))
        c.add_widget(body(t))
        self.list.add_widget(c)
        self.field.text = ""
        toast("added")


if __name__ == "__main__":
    MyApp().run()
'''

_T_GAME = '''from kivy.app import App
from kivy.uix.widget import Widget
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse
from kivy.metrics import dp

Theme.seed("Tap Ball")


class Board(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.x_pos = 120.0
        self.y_pos = 240.0
        self.vx = 180.0
        self.vy = 140.0
        self.r = dp(28)
        self.score = 0
        Clock.schedule_interval(self.step, 1 / 60.0)

    def step(self, dt):
        self.x_pos += self.vx * dt
        self.y_pos += self.vy * dt
        if self.x_pos < 0 or self.x_pos + self.r * 2 > self.width:
            self.vx = -self.vx
        if self.y_pos < 0 or self.y_pos + self.r * 2 > self.height:
            self.vy = -self.vy
        self.canvas.clear()
        with self.canvas:
            Color(*Theme.accent)
            Ellipse(pos=(self.x_pos, self.y_pos), size=(self.r * 2, self.r * 2))

    def on_touch_down(self, touch):
        dx = touch.x - (self.x_pos + self.r)
        dy = touch.y - (self.y_pos + self.r)
        if dx * dx + dy * dy <= (self.r * 1.4) ** 2:
            self.score += 1
            self.vx *= 1.06
            self.vy *= 1.06
            toast("score %d" % self.score)
        return True


class MyApp(App):
    def build(self):
        root = GradientBackground()
        root.add_widget(AppBar(title="Tap Ball", subtitle="tap the moving ball"))
        root.add_widget(Board(size_hint=(1, 1)))
        return root


if __name__ == "__main__":
    MyApp().run()
'''

TEMPLATES = {
    "blank": {"label": "Blank", "desc": "Empty file. Nothing at all.",
              "code": _T_BLANK, "kit": False},
    "min":   {"label": "Minimal Kivy", "desc": "Plain Kivy app, one label, no kit",
              "code": _T_MIN, "kit": False},
    "kit":   {"label": "Kit starter", "desc": "AppBar + Card + buttons (Dawg kit)",
              "code": _T_KIT, "kit": True},
    "form":  {"label": "Form + list", "desc": "TextField that appends to a scrolling list",
              "code": _T_FORM, "kit": True},
    "game":  {"label": "Game loop", "desc": "60fps canvas, touch + score",
              "code": _T_GAME, "kit": True},
}

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
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


# A transient hiccup (rate-limit, a 5xx, a dropped connection, a read timeout) shouldn't
# surface as a hard failure that kills a forge/fix round -- so we retry those a few times
# with backoff. A PERMANENT error (bad key 401/403, unknown model 404/400) is re-raised
# immediately: retrying it just wastes time and never succeeds.
_RETRY_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _post_json_resilient(url, key, payload, tries=3):
    delay = 1.5
    last = None
    for attempt in range(1, tries + 1):
        try:
            return _post_json(url, key, payload)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in _RETRY_HTTP or attempt == tries:
                raise               # permanent, or out of attempts -> let caller report it
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt == tries:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 12)  # 1.5s -> 3s -> 6s ... capped
    if last:
        raise last                  # defensive; loop above always returns or raises


def est_tokens(text):
    """Cheap token estimate (~4 chars/token) for pre-flight sizing and cache stats."""
    return max(1, len(text or "") // 4)


def _cache_key(messages, model, max_tokens):
    h = hashlib.sha256()
    h.update((model or "").encode())
    h.update(str(max_tokens).encode())
    for msg in messages:
        h.update((msg.get("role", "") + "\x00" + (msg.get("content") or "")).encode())
    return h.hexdigest()[:32]


def cache_get(key):
    if not CONFIG.get("cache", True):
        return None
    try:
        p = os.path.join(CACHEDIR, key + ".json")
        if os.path.exists(p):
            with open(p) as f:
                d = json.load(f)
            return d.get("text"), d.get("provider")
    except Exception:
        pass
    return None


def cache_put(key, text, provider):
    if not CONFIG.get("cache", True):
        return
    try:
        os.makedirs(CACHEDIR, exist_ok=True)
        with open(os.path.join(CACHEDIR, key + ".json"), "w") as f:
            json.dump({"text": text, "provider": provider, "at": time.time()}, f)
        # keep the cache small: 200 newest entries
        files = sorted(glob.glob(os.path.join(CACHEDIR, "*.json")), key=os.path.getmtime)
        for old in files[:-200]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def cache_clear():
    n = 0
    for p in glob.glob(os.path.join(CACHEDIR, "*.json")):
        try:
            os.remove(p)
            n += 1
        except Exception:
            pass
    return n


def _provider_error(name, e, model):
    """Turn an HTTP error from a provider into something the user can act on.
    The whole point: a rejected KEY and a wrong MODEL must not look the same."""
    try:
        raw = e.read().decode("utf-8")
    except Exception:
        raw = ""
    detail = ""
    try:
        j = json.loads(raw)
        detail = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("message")
        detail = detail or j.get("error") or ""
    except Exception:
        detail = raw[:200]
    code = e.code
    if code in (401, 403):
        # 403 with an allowlist note is this sandbox, not the user's key
        if "allowlist" in (detail or "").lower() or "egress" in (detail or "").lower():
            return "%s network blocked: %s" % (name, detail)
        return ("%s rejected the API key (HTTP %s). The key is wrong, expired, or has no "
                "credit. Re-check it in Settings. [%s]" % (name, code, detail or "no detail"))
    if code == 404 or (code == 400 and "model" in (detail or "").lower()):
        return ("%s doesn't recognise the model '%s' (HTTP %s). Pick a different model in "
                "Settings. [%s]" % (name, model, code, detail or "no detail"))
    if code == 429:
        return "%s rate-limited / out of quota (HTTP 429). [%s]" % (name, detail or "")
    if code >= 500:
        return "%s server error (HTTP %s) -- their side, try again shortly. [%s]" % (name, code, detail or "")
    return "%s HTTP %s: %s" % (name, code, detail or raw[:200])


def call_ai(messages, temperature=0.4, max_tokens=None, label="call"):
    """One metered, cached, budget-guarded model call."""
    check_budget()
    model = CONFIG.get("sf_model") or SF_MODEL
    if max_tokens is None:
        max_tokens = int(CONFIG.get("max_tokens") or 12000)
    key = _cache_key(messages, model, max_tokens)
    hit = cache_get(key)
    if hit and hit[0]:
        saved = sum(est_tokens(m.get("content")) for m in messages) + est_tokens(hit[0])
        meter(0, 0, cached=True, saved=saved)
        return hit[0], (hit[1] or "cache") + " [cached, 0 tokens]"

    sf = sf_key()
    gq = groq_key()
    errs = []
    if sf:
        sf_u = chat_url(CONFIG.get("sf_url") or SF_URL)
        try:
            d = _post_json_resilient(sf_u, sf, {
                "model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
            })
            text = d["choices"][0]["message"]["content"]
            u = d.get("usage") or {}
            meter(u.get("prompt_tokens") or est_tokens("".join(m.get("content") or "" for m in messages)),
                  u.get("completion_tokens") or est_tokens(text))
            prov = "SiliconFlow / " + model
            cache_put(key, text, prov)
            return text, prov
        except urllib.error.HTTPError as e:
            errs.append(_provider_error("SiliconFlow", e, model))
        except Exception as e:
            errs.append("SiliconFlow (%s): %s" % (sf_u, e))
    if gq:
        gq_u = chat_url(CONFIG.get("groq_url") or GROQ_URL)
        gq_model = CONFIG.get("groq_model") or GROQ_MODEL
        try:
            d = _post_json_resilient(gq_u, gq, {
                "model": gq_model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
            })
            text = d["choices"][0]["message"]["content"]
            u = d.get("usage") or {}
            meter(u.get("prompt_tokens") or est_tokens("".join(m.get("content") or "" for m in messages)),
                  u.get("completion_tokens") or est_tokens(text))
            prov = "Groq (fallback)"
            cache_put(key, text, prov)
            return text, prov
        except urllib.error.HTTPError as e:
            errs.append(_provider_error("Groq", e, gq_model))
        except Exception as e:
            errs.append("Groq: %s" % e)
    if not sf and not gq:
        raise RuntimeError("No API key set. Open Settings (the gear, top right), paste your "
                           "SiliconFlow key, and click Save -- or set SILICONFLOW_API_KEY before launching.")
    raise RuntimeError(" | ".join(errs) or "AI call failed")


def _recompute(payload, repair=None):
    """payload['main_py'] is a FULL file (kit + app). Optionally run the free local
    repair pass, then heal the kit and recompute every check."""
    if not payload.get("ok"):
        return payload
    if repair is None:
        repair = bool(CONFIG.get("auto_repair", True))
    full = ensure_kit(payload["main_py"])
    if repair:
        app = strip_kit(full)
        app, reqs, perms, fixes = auto_repair(app, payload.get("requirements", ""),
                                              payload.get("permissions", ""))
        if fixes:
            full = with_kit(app)
            payload["requirements"] = fix_requirements(reqs)
            payload["permissions"] = clean_perms(perms)
            payload["repairs"] = (payload.get("repairs") or []) + fixes
    payload["main_py"] = full
    payload["app_py"] = strip_kit(full)
    payload["kit_lines"] = kit_line_offset()
    payload["syntax_ok"], payload["syntax_msg"] = syntax_check(full)
    payload["errors"], payload["warnings"] = validate_code(full, payload.get("requirements", ""))
    payload["issues"] = analyze_code(full, payload.get("requirements", ""), payload.get("permissions", ""))
    return payload


def _finish_ai(payload, provider, requirements, permissions):
    """Shared tail for fix/polish: the model returns APP CODE ONLY, we re-attach the kit."""
    if payload.get("ok"):
        payload["main_py"] = with_kit(strip_kit(payload["main_py"]))
        if not payload.get("requirements"):
            payload["requirements"] = fix_requirements(requirements)
        if not payload.get("permissions"):
            payload["permissions"] = clean_perms(permissions)
        _recompute(payload)
    payload["provider"] = provider
    payload["usage"] = dict(USAGE)
    return payload


def ai_fix(main_py, error, requirements, permissions):
    """Fix a fault. Sends APP CODE ONLY -- the 300-line kit never crosses the wire."""
    app = strip_kit(main_py or "")
    msg = ("ERROR / FINDINGS:\n" + (error or "(none given)")
           + "\n\nDECLARED requirements: " + (requirements or "python3,kivy")
           + "\nDECLARED permissions: " + (permissions or "(none)")
           + "\n\nAPP CODE (the kit sits above this, unchanged and in scope):\n" + app)
    messages = [{"role": "system", "content": FIX_PROMPT}, {"role": "user", "content": msg}]
    # low temperature: a fix should be a fix, not a creative rewrite
    text, provider = call_ai(messages, temperature=0.15, label="fix")
    payload = build_forge_payload(text, "fix")
    return _finish_ai(payload, provider, requirements, permissions)


def ai_polish(main_py, requirements, permissions):
    app = strip_kit(main_py or "")
    msg = "APP CODE TO RESTYLE (the kit sits above this, unchanged and in scope):\n" + app
    messages = [{"role": "system", "content": POLISH_PROMPT}, {"role": "user", "content": msg}]
    text, provider = call_ai(messages, temperature=0.35, label="polish")
    payload = build_forge_payload(text, "polish")
    return _finish_ai(payload, provider, requirements, permissions)


# ----------------------------------------------------------------- build worker
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


# ----------------------------------------------------------------- headless test worker
_RUNNER = r'''"""Phased self-test harness.

v2 ran the app for two seconds and called it a pass. That misses everything that only
breaks when a user actually touches the thing. This drives the app through the phases a
real launch goes through, reports each one separately, and never lets one phase's
failure hide another's result.
"""
import os, sys, threading, traceback

PHASES = []          # (name, ok, detail)
LIVE_ERRORS = []     # exceptions raised from callbacks/clock during the soak

def _trim(detail, limit=600):
    """Keep the TAIL of a traceback -- the exception type and message live at the end,
    and that is the only part a fix round actually needs."""
    d = (detail or "").strip()
    if len(d) <= limit:
        return d
    return "..." + d[-limit:]

def phase(name, ok, detail=""):
    PHASES.append((name, ok, detail))
    print("DAWG_PHASE\t%s\t%s\t%s" % (name, "ok" if ok else "fail",
                                      _trim(detail).replace("\n", " | ")))
    sys.stdout.flush()

def _watchdog():
    import time
    time.sleep(40)
    sys.stderr.write("DAWG_TEST_TIMEOUT\n")
    sys.stderr.flush()
    os._exit(124)

threading.Thread(target=_watchdog, daemon=True).start()

def _hook(exc_type, exc, tb):
    LIVE_ERRORS.append("".join(traceback.format_exception(exc_type, exc, tb)))
sys.excepthook = _hook
threading.excepthook = lambda a: LIVE_ERRORS.append(
    "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))

# ---------------------------------------------------------------- phase 1: compile
src = ""
try:
    with open("main.py") as f:
        src = f.read()
    code_obj = compile(src, "main.py", "exec")
    phase("compile", True)
except Exception:
    phase("compile", False, traceback.format_exc())
    print("DAWG_TEST_FAIL"); os._exit(1)

# ---------------------------------------------------------------- phase 2: import
# Run the module with .run() neutralised so we control the lifecycle ourselves. We patch
# in THREE places so a real Kivy loop can never actually start (which would block, or exit
# the process): App.run, App.async_run, and the low-level kivy.base.runTouchApp. Whichever
# entry point the app uses, we just record the instance instead of launching it.
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.base import EventLoop
import kivy.base as _kbase

_captured = {"app": None}
_real_run = App.run
def _capture_run(self, *a, **k):
    _captured["app"] = self          # remember the instance; do NOT start the loop
App.run = _capture_run
if hasattr(App, "async_run"):
    async def _capture_async_run(self, *a, **k):
        _captured["app"] = self
    App.async_run = _capture_async_run
_real_rta = _kbase.runTouchApp
def _noop_rta(*a, **k):
    return None                      # never spin a real mainloop during the self-test
_kbase.runTouchApp = _noop_rta

ns = {"__name__": "__main__", "__file__": "main.py"}
try:
    exec(code_obj, ns)
    phase("import", True)
except SystemExit:
    # even if the module bailed with sys.exit, whatever it defined is still in ns
    phase("import", True, "module called sys.exit (tolerated)")
except Exception:
    phase("import", False, traceback.format_exc())
    print("DAWG_TEST_FAIL"); os._exit(1)

# Find the App. Prefer the instance .run()/async_run captured; otherwise hunt the namespace
# for ANY App subclass -- including one named exactly `App` (a very common model mistake:
# `class App(App):`). We pick the LAST such class defined, which is almost always the real
# app rather than a helper/base.
def _is_app_subclass(v):
    try:
        return isinstance(v, type) and issubclass(v, App) and v is not App
    except Exception:
        return False

app = _captured["app"]
if app is None:
    candidates = [v for v in ns.values() if _is_app_subclass(v)]
    # also catch a subclass that shadowed the name `App` itself
    shadow = ns.get("App")
    if _is_app_subclass(shadow) and shadow not in candidates:
        candidates.append(shadow)
    if candidates:
        try:
            app = candidates[-1]()
        except Exception:
            phase("instantiate", False, traceback.format_exc())
            print("DAWG_TEST_FAIL"); os._exit(1)
if app is None:
    phase("instantiate", False,
          "no App subclass found. Make sure you `class YourApp(App):` and call "
          "`YourApp().run()` at the bottom under `if __name__ == '__main__':`.")
    print("DAWG_TEST_FAIL"); os._exit(1)
phase("instantiate", True, type(app).__name__)

# ---------------------------------------------------------------- phase 3: build()
root = None
try:
    App.run = _real_run
    EventLoop.ensure_window()
    root = app.build()
    if root is None:
        # some apps build via kv / load_kv; tolerate but note it
        phase("build", True, "build() returned None (kv-driven or root set elsewhere)")
    else:
        app.root = root
        Window.add_widget(root)
        phase("build", True, type(root).__name__)
except Exception:
    phase("build", False, traceback.format_exc())
    print("DAWG_TEST_FAIL"); os._exit(1)

def _walk(w, depth=0):
    yield w, depth
    for c in list(getattr(w, "children", []) or []):
        for item in _walk(c, depth + 1):
            yield item

widgets = list(_walk(root)) if root is not None else []
phase("widget_tree", True, "%d widgets, depth %d"
      % (len(widgets), max([d for _, d in widgets], default=0)))

# ---------------------------------------------------------------- phase 4: render
def pump(n=3):
    for _ in range(n):
        EventLoop.idle()

try:
    pump(4)
    phase("render", True, "4 frames drawn")
except Exception:
    phase("render", False, traceback.format_exc())

# ---------------------------------------------------------------- phase 5: touch
# Press every button in the tree. This is where "works on my screen" apps fall over:
# a handler that references a missing attribute only blows up on the first tap.
class _T:
    def __init__(self, x, y):
        self.x = x; self.y = y; self.pos = (x, y)
        self.spos = (0, 0); self.opos = (x, y); self.dpos = (0, 0)
        self.profile = ["pos"]; self.ud = {}; self.is_touch = True
        self.grab_current = None; self.button = "left"; self.time_start = 0
        self.dx = self.dy = 0; self.osx = self.osy = 0
        self.push_attrs = ()
    def grab(self, *a, **k): pass
    def ungrab(self, *a, **k): pass
    def push(self, *a, **k): pass
    def pop(self, *a, **k): pass

tapped, tap_errors = 0, []
try:
    from kivy.uix.behaviors import ButtonBehavior
    from kivy.uix.button import Button as _Btn
    for w, _d in widgets:
        if not isinstance(w, (_Btn, ButtonBehavior)):
            continue
        try:
            w.dispatch("on_press")
            w.dispatch("on_release")
            tapped += 1
            pump(1)
        except Exception:
            tb = traceback.format_exc().strip().splitlines()
            # drop the harness frames; keep the app frames and the exception line
            useful = [l for l in tb if "__dawg_run.py" not in l and "kivy/_event" not in l]
            tap_errors.append("%s (label %r): %s"
                              % (type(w).__name__, getattr(w, "text", "")[:24],
                                 " | ".join(useful[-4:])))
    if tap_errors:
        phase("touch", False, "%d/%d handlers raised -- %s"
              % (len(tap_errors), tapped + len(tap_errors), " ;; ".join(tap_errors[:3])))
    else:
        phase("touch", True, "%d button(s) pressed cleanly" % tapped)
except Exception:
    phase("touch", False, traceback.format_exc())

# ---------------------------------------------------------------- phase 6: rotate
try:
    w0, h0 = Window.size
    Window.size = (h0, w0)
    pump(2)
    Window.size = (w0, h0)
    pump(2)
    phase("rotate", True, "survived a portrait<->landscape flip")
except Exception:
    phase("rotate", False, traceback.format_exc())

# ---------------------------------------------------------------- phase 7: soak
try:
    on_start = getattr(app, "on_start", None)
    if callable(on_start):
        on_start()
    import time as _t
    t_end = _t.time() + 3.0
    frames = 0
    while _t.time() < t_end:
        EventLoop.idle()
        frames += 1
    if LIVE_ERRORS:
        phase("soak", False, "%d exception(s) from timers/callbacks\n%s"
              % (len(LIVE_ERRORS), LIVE_ERRORS[0][:600]))
    else:
        phase("soak", True, "%d frames over 3s, no callback exceptions" % frames)
except Exception:
    phase("soak", False, traceback.format_exc())

# ---------------------------------------------------------------- phase 8: teardown
try:
    stop = getattr(app, "on_stop", None)
    if callable(stop):
        stop()
    phase("teardown", True)
except Exception:
    phase("teardown", False, traceback.format_exc())

failed = [p for p in PHASES if not p[1]]
if failed:
    print("DAWG_TEST_FAIL")
    os._exit(1)
print("DAWG_TEST_OK")
os._exit(0)
'''

_BENIGN = ("Cutbuffer", "xclip", "xsel", "Unable to open the clipboard",
           "[INFO", "[WARNING", "[DEBUG", "sdl2 - Unable")


def _missing_module(text):
    m = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]", text or "")
    return m.group(1).split(".")[0] if m else None


def _start_xvfb():
    """Start a private Xvfb on the first free display number and return (":N", proc).
    Used on Arch/CachyOS, whose xorg-server-xvfb ships Xvfb but not the xvfb-run wrapper.
    Returns (None, None) if it can't come up. Caller must _stop_xvfb(proc) when done."""
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        return None, None
    for n in range(99, 129):  # high numbers avoid clashing with a real :0/:1
        if os.path.exists("/tmp/.X%d-lock" % n):
            continue
        disp = ":%d" % n
        try:
            proc = subprocess.Popen(
                [xvfb, disp, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            return None, None
        # give the server a moment; if it died instantly the display number was taken
        for _ in range(50):
            if proc.poll() is not None:
                break
            if os.path.exists("/tmp/.X%d-lock" % n):
                return disp, proc
            time.sleep(0.05)
        if proc.poll() is None:
            # came up without a lock file (rare); trust it
            return disp, proc
        # died -> try the next number
    return None, None


def _stop_xvfb(proc):
    if not proc:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    except Exception:
        pass


def run_test(test_id, main_py, requirements):
    rec = TESTS[test_id]

    def log(line):
        rec["log"].append(line)
        if len(rec["log"]) > 4000:
            del rec["log"][:1000]

    if not host_has_kivy():
        rec["status"] = "skipped"
        rec["summary"] = "kivy isn't installed on this machine, so the app can't be test-run here. Install it once with: pip install --user kivy   (optional -- it only powers the pre-build crash check, the APK build doesn't need it)."
        log(rec["summary"])
        return
    if not host_can_display():
        rec["status"] = "skipped"
        hint = pkg_hint(pacman="sudo pacman -S xorg-server-xvfb",
                        apt="sudo apt install -y xvfb",
                        dnf="sudo dnf install -y xorg-x11-server-Xvfb",
                        zypper="sudo zypper install xorg-x11-server-Xvfb")
        rec["summary"] = ("no display available for the headless self-test. Install Xvfb to "
                          "enable it: " + (hint or "install your distro's Xvfb package")) + \
                         "  (optional -- the APK build doesn't need it)."
        log(rec["summary"])
        return

    tdir = os.path.join(TESTDIR, test_id)
    try:
        os.makedirs(tdir, exist_ok=True)
        with open(os.path.join(tdir, "main.py"), "w") as f:
            f.write(main_py or "")
        with open(os.path.join(tdir, "__dawg_run.py"), "w") as f:
            f.write(_RUNNER)
    except Exception as e:
        rec["status"] = "fail"
        rec["summary"] = "couldn't stage the test: %s" % e
        log(rec["summary"])
        return

    # Pick how to give the app a display. Order of preference:
    #   1. xvfb-run  - a clean throwaway virtual display (Debian ships it)
    #   2. Xvfb      - Arch/CachyOS ship the binary but NOT the wrapper, so we run it
    #                  ourselves on a free display number and tear it down after.
    #   3. live $DISPLAY - only if neither of the above exists (last resort; may pop a
    #                  visible window). On a pure Wayland session with no XWayland this
    #                  won't be set, which is exactly why the Xvfb path above matters.
    env = dict(os.environ, KIVY_NO_ARGS="1", KIVY_LOG_LEVEL="warning",
               PYTHONUNBUFFERED="1", KIVY_NO_CONSOLELOG="0",
               SDL_AUDIODRIVER="dummy")  # no audio device on a virtual display
    xvfb_proc = None
    if shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", sys.executable, "__dawg_run.py"]
    elif shutil.which("Xvfb"):
        disp, xvfb_proc = _start_xvfb()
        if disp is None:
            rec["status"] = "skipped"
            rec["summary"] = "found Xvfb but couldn't start it; self-test skipped (build still works)."
            log(rec["summary"])
            return
        env["DISPLAY"] = disp
        cmd = [sys.executable, "__dawg_run.py"]
    elif os.environ.get("DISPLAY"):
        cmd = [sys.executable, "__dawg_run.py"]
    else:
        rec["status"] = "skipped"
        rec["summary"] = "no display and no Xvfb available; self-test skipped (build still works)."
        log(rec["summary"])
        return
    log("$ " + " ".join(cmd))
    log("(self-test: compile -> import -> build -> render -> tap every button -> rotate -> 3s soak)")
    log("")
    try:
        proc = subprocess.run(cmd, cwd=tdir, env=env, capture_output=True, text=True, timeout=75)
        out = (proc.stdout or "") + (proc.stderr or "")
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rec["status"] = "timeout"
        rec["summary"] = "the app didn't settle within the time limit -- on a phone this shows up as a hang/ANR. Check for a blocking loop, input(), or a long operation in build()/__init__."
        log(rec["summary"])
        return
    except FileNotFoundError as e:
        rec["status"] = "skipped"
        rec["summary"] = "test runner unavailable: %s" % e
        log(rec["summary"])
        return
    except Exception as e:
        rec["status"] = "fail"
        rec["summary"] = "test run error: %s" % e
        log(rec["summary"])
        return
    finally:
        _stop_xvfb(xvfb_proc)

    # pull the structured phase report out of the stream
    phases = []
    for line in out.splitlines():
        if line.startswith("DAWG_PHASE\t"):
            parts = line.split("\t")
            while len(parts) < 4:
                parts.append("")
            phases.append({"name": parts[1], "ok": parts[2] == "ok", "detail": parts[3]})
    rec["phases"] = phases

    if phases:
        log("--- self-test phases ---")
        for p in phases:
            log("  [%s] %-12s %s" % ("PASS" if p["ok"] else "FAIL", p["name"], p["detail"]))
        log("")

    # surface non-benign output lines
    for line in out.splitlines():
        if line.startswith("DAWG_PHASE\t"):
            continue
        if line.strip() and not any(b in line for b in _BENIGN):
            log(line)

    has_tb = "Traceback (most recent call last)" in out
    miss = _missing_module(out)
    failed = [p for p in phases if not p["ok"]]
    if rc == 124 or "DAWG_TEST_TIMEOUT" in out:
        rec["status"] = "timeout"
        rec["summary"] = ("the app hung (no clean exit) -- likely a blocking loop or input() "
                          "that would ANR on the phone.")
    elif miss and miss in ANDROID_ONLY:
        rec["status"] = "warn"
        rec["summary"] = "imports the android-only module '%s', which can't run on desktop. That's expected -- just make sure the import is guarded by `if platform == \"android\":` so desktop test runs (and the launch path) skip it." % miss
    elif miss and miss in {x.strip().lower() for x in (requirements or "").split(",")} and miss in SAFE_REQS:
        rec["status"] = "warn"
        rec["summary"] = "host is missing '%s', so it couldn't be fully test-run here, but it IS declared and Buildozer will bundle it into the APK. Looks fine to build." % miss
    elif failed:
        rec["status"] = "fail"
        names = ", ".join(p["name"] for p in failed)
        rec["summary"] = ("self-test failed at: %s. The detail above is the real traceback -- "
                          "hit AUTO-FIX to send it back for a targeted fix." % names)
        rec["error_text"] = "\n\n".join(
            "phase '%s' failed: %s" % (p["name"], p["detail"]) for p in failed)
    elif has_tb or rc not in (0,):
        rec["status"] = "fail"
        rec["summary"] = "the app crashed (traceback above). Hit AUTO-FIX to send the error back for a fix."
        rec["error_text"] = out[-4000:]
    elif "DAWG_TEST_OK" in out:
        rec["status"] = "pass"
        n = len(phases)
        rec["summary"] = ("all %d self-test phases passed -- it builds its UI, survives taps, "
                          "a rotation and a 3s soak with no exceptions. Good to build." % n)
    else:
        rec["status"] = "warn"
        rec["summary"] = "finished without a clear pass/fail signal. Review the output above."
    log("")
    log(rec["summary"])

# ----------------------------------------------------------------- agent loop
# The pipeline the app used to make you drive by hand: forge -> repair -> lint ->
# self-test -> fix -> repeat. Every round tries the FREE local repair first and only
# pays for a model call when the fault genuinely needs one.
def _job_log(rec, text, kind="info"):
    rec["steps"].append({"t": time.time(), "kind": kind, "text": text})
    if len(rec["steps"]) > 400:
        del rec["steps"][:100]


def _run_selftest_sync(main_py, requirements):
    """Run the phased harness inline and return the TESTS record."""
    tid = "job" + uuid.uuid4().hex[:8]
    TESTS[tid] = {"log": [], "status": "running", "summary": "", "phases": []}
    run_test(tid, main_py, requirements)
    return TESTS[tid]


def run_agent(job_id, desc, seed_payload=None, rounds=None):
    rec = JOBS[job_id]
    if rounds is None:
        rounds = int(CONFIG.get("agent_rounds") or 3)
    rounds = max(1, min(6, int(rounds)))
    try:
        # ---- round 0: get code on the table -------------------------------------
        if seed_payload is not None:
            payload = seed_payload
            _job_log(rec, "starting from the code already in the editor", "step")
        else:
            _job_log(rec, "forging from your description...", "step")
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": desc}]
            text, provider = call_ai(messages, temperature=0.4, label="forge")
            payload = build_forge_payload(text, desc)
            if not payload.get("ok"):
                rec["status"] = "failed"
                rec["error"] = payload.get("error", "the model returned no usable app")
                _job_log(rec, rec["error"], "fail")
                return
            payload["main_py"] = with_kit(payload["main_py"])
            payload["provider"] = provider
            _recompute(payload)
            _job_log(rec, "forged '%s' via %s" % (payload.get("title") or "app", provider), "ok")
        rec["payload"] = payload

        for rnd in range(1, rounds + 1):
            rec["round"] = rnd
            # ---- free local repair --------------------------------------------
            if payload.get("repairs"):
                for f in payload["repairs"]:
                    _job_log(rec, "repaired locally (0 tokens): " + f, "ok")
                payload["repairs"] = []

            # ---- static gate ---------------------------------------------------
            hard = [i for i in payload.get("issues", []) if i["sev"] == "error"]
            if not payload.get("syntax_ok"):
                findings = "SYNTAX: " + (payload.get("syntax_msg") or "")
                _job_log(rec, "syntax error: " + (payload.get("syntax_msg") or ""), "fail")
            elif hard:
                findings = "\n".join("- %s (%s)" % (i["msg"], i["fix"]) for i in hard)
                _job_log(rec, "%d blocking issue(s) static analysis can't fix itself" % len(hard), "warn")
            else:
                findings = ""
                _job_log(rec, "static analysis clean", "ok")

            # ---- self-test -----------------------------------------------------
            if not findings:
                if not host_can_test():
                    _job_log(rec, "self-test skipped (no kivy/xvfb on this host) -- "
                                  "static analysis passed, so it's ready to build", "warn")
                    rec["status"] = "done"
                    rec["payload"] = payload
                    return
                _job_log(rec, "running the self-test (build, tap, rotate, soak)...", "step")
                t = _run_selftest_sync(payload["main_py"], payload.get("requirements", ""))
                rec["phases"] = t.get("phases", [])
                rec["test_status"] = t.get("status")
                for p in t.get("phases", []):
                    _job_log(rec, "%s %s%s" % ("PASS" if p["ok"] else "FAIL", p["name"],
                                               (" - " + p["detail"]) if p["detail"] else ""),
                             "ok" if p["ok"] else "fail")
                if t.get("status") in ("pass", "warn", "skipped"):
                    _job_log(rec, t.get("summary", "self-test finished"), "ok")
                    rec["status"] = "done"
                    rec["payload"] = payload
                    return
                findings = t.get("error_text") or t.get("summary") or "the self-test failed"

            # ---- last round? stop before paying for a fix we can't verify -------
            if rnd >= rounds:
                _job_log(rec, "out of rounds -- stopping here so it doesn't keep spending. "
                              "Review the findings and hit AUTO-FIX if you want another go.", "warn")
                rec["status"] = "stalled"
                rec["payload"] = payload
                return

            # ---- pay for a fix --------------------------------------------------
            before = payload["main_py"]
            _job_log(rec, "asking the model for a targeted fix (round %d/%d)" % (rnd, rounds), "step")
            try:
                fixed = ai_fix(payload["main_py"], findings,
                               payload.get("requirements", ""), payload.get("permissions", ""))
            except Exception as e:
                rec["status"] = "failed"
                rec["error"] = str(e)
                _job_log(rec, "fix call failed: %s" % e, "fail")
                rec["payload"] = payload
                return
            if not fixed.get("ok"):
                _job_log(rec, "the model's fix didn't parse -- keeping the previous version", "fail")
                rec["status"] = "stalled"
                rec["payload"] = payload
                return
            # keep identity from the original forge; a fix shouldn't rename the app
            for k in ("name", "title", "orientation"):
                if payload.get(k):
                    fixed[k] = payload[k]
            if strip_kit(fixed["main_py"]).strip() == strip_kit(before).strip():
                _job_log(rec, "the model returned identical code -- it can't fix this one. "
                              "Stopping rather than paying for the same answer again.", "warn")
                rec["status"] = "stalled"
                rec["payload"] = fixed
                return
            payload = fixed
            rec["payload"] = payload
            _job_log(rec, "fix applied, re-checking", "ok")

        rec["status"] = "stalled"
    except Exception as e:
        rec["status"] = "failed"
        rec["error"] = str(e)
        _job_log(rec, "agent error: %s" % e, "fail")
    finally:
        rec["usage"] = dict(USAGE)


# ----------------------------------------------------------------- server helpers
def safe_archs(a):
    known = {"arm64-v8a", "armeabi-v7a", "x86", "x86_64"}
    parts = [x.strip() for x in (a or "").replace(";", ",").split(",") if x.strip() in known]
    return ",".join(dict.fromkeys(parts)) or ANDROID_ARCHS


def manual_payload(name, code, title="", requirements="python3,kivy", permissions="",
                   orientation="portrait", kit=True, repair=False):
    """Wrap hand-written / template app code into a forge-shaped payload.

    kit=False means the file is exactly what you typed -- no kit, no scaffold. That's
    what MANUAL mode opens with, so a blank editor really is a blank file.
    """
    code = code or ""
    full = with_kit(code) if kit else code
    if repair:
        base = strip_kit(full) if kit else full
        base, requirements, permissions, _fx = auto_repair(base, requirements, permissions)
        full = with_kit(base) if kit else base
    sok, smsg = syntax_check(full)
    reqs = fix_requirements(requirements)
    errs, warns = validate_code(full, reqs)
    issues = analyze_code(full, reqs, permissions)
    nm = slugify(name or "app")
    return {
        "ok": True, "name": nm, "title": (title or nm.replace("_", " ").title()),
        "orientation": orientation if orientation in ("portrait", "landscape", "all") else "portrait",
        "requirements": reqs, "permissions": clean_perms(permissions), "notes": "",
        "main_py": full, "app_py": strip_kit(full) if kit else full,
        "kit": bool(kit), "kit_lines": kit_line_offset() if kit else 0,
        "syntax_ok": sok, "syntax_msg": smsg,
        "errors": errs, "warnings": warns, "issues": issues,
        "build_overrides": {}, "build_warnings": [], "provider": "local (no AI, 0 tokens)",
        "usage": dict(USAGE),
    }


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

    def _send_file(self, path, ctype, download_name):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % download_name)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
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
        if path == "/api/testlog":
            qs = parse_qs(urlparse(self.path).query)
            tid = (qs.get("id") or [""])[0]
            rec = TESTS.get(tid)
            if not rec:
                return self._send(404, {"error": "no such test"})
            return self._send(200, {
                "status": rec["status"],
                "log": "\n".join(rec["log"][-800:]),
                "summary": rec.get("summary", ""),
                "phases": rec.get("phases", []),
                "error_text": rec.get("error_text", ""),
            })
        if path == "/api/apk":
            qs = parse_qs(urlparse(self.path).query)
            bid = (qs.get("id") or [""])[0]
            rec = BUILDS.get(bid)
            if not rec or not rec.get("apk") or not os.path.exists(rec["apk"]):
                return self._send(404, {"error": "apk not ready"})
            return self._send_file(rec["apk"], "application/vnd.android.package-archive",
                                   os.path.basename(rec["apk"]))
        if path == "/api/ping":
            return self._send(200, {"app": "androdawg", "version": VERSION, "ok": True})
        if path == "/api/keytest":
            return self._keytest()
        if path == "/api/doctor":
            return self._send(200, {"checks": doctor(), "can_test": host_can_test()})
        if path == "/api/templates":
            return self._send(200, {"templates": [
                {"id": k, "label": v["label"], "desc": v["desc"], "kit": v.get("kit", True)}
                for k, v in TEMPLATES.items()
            ]})
        if path == "/api/devices":
            devs = []
            if _devices is not None:
                for k in _devices.names():
                    p = _devices.get(k)
                    devs.append({"id": k, "label": p["label"], "category": p["category"]})
            return self._send(200, {"devices": devs, "default": getattr(_devices, "DEFAULT", ""),
                                    "available": _devices is not None and host_has_kivy()})
        if path == "/api/template":
            qs = parse_qs(urlparse(self.path).query)
            tid = (qs.get("id") or [""])[0]
            t = TEMPLATES.get(tid)
            if not t:
                return self._send(404, {"error": "no such template"})
            return self._send(200, manual_payload(tid if tid != "blank" else "app",
                                                  t["code"], kit=t.get("kit", True)))
        if path == "/api/usage":
            return self._send(200, {
                "usage": dict(USAGE),
                "budget": int(CONFIG.get("token_budget") or 0),
                "left": budget_left(),
                "kit_lines": kit_line_offset(),
            })
        if path == "/api/job":
            qs = parse_qs(urlparse(self.path).query)
            jid = (qs.get("id") or [""])[0]
            rec = JOBS.get(jid)
            if not rec:
                return self._send(404, {"error": "no such job"})
            return self._send(200, {
                "status": rec["status"], "round": rec.get("round", 0),
                "steps": rec["steps"][-160:], "payload": rec.get("payload"),
                "phases": rec.get("phases", []), "error": rec.get("error", ""),
                "usage": dict(USAGE),
            })
        if path == "/api/smoketest":
            payload = build_forge_payload(SMOKE_TEXT, "smoke test")
            if payload.get("ok"):
                payload["main_py"] = with_kit(payload["main_py"])
                _recompute(payload)
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
                "max_tokens": int(CONFIG.get("max_tokens") or 12000),
                "token_budget": int(CONFIG.get("token_budget") or 0),
                "cache": bool(CONFIG.get("cache", True)),
                "auto_repair": bool(CONFIG.get("auto_repair", True)),
                "agent_rounds": int(CONFIG.get("agent_rounds") or 3),
            })
        if path in ("/icon.png", "/favicon.ico", "/favicon.png", "/apple-touch-icon.png"):
            try:
                return self._send(200, _brand_icon_bytes(), "image/png")
            except Exception:
                return self._send(404, {"error": "no icon"})
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
        if path == "/api/autoforge":
            return self.handle_autoforge(body)
        if path == "/api/lint":
            return self.handle_lint(body)
        if path == "/api/repair":
            return self.handle_repair(body)
        if path == "/api/manual":
            return self._send(200, manual_payload(
                body.get("name", "app"), body.get("main_py", ""),
                title=body.get("title", ""),
                requirements=body.get("requirements", "python3,kivy"),
                permissions=body.get("permissions", ""),
                orientation=body.get("orientation", "portrait"),
                kit=bool(body.get("kit", False))))
        if path == "/api/cache_clear":
            return self._send(200, {"removed": cache_clear()})
        if path == "/api/build":
            return self.handle_build(body)
        if path == "/api/testrun":
            return self.handle_testrun(body)
        if path == "/api/fix":
            return self.handle_fix(body)
        if path == "/api/polish":
            return self.handle_polish(body)
        if path == "/api/config":
            return self.handle_config(body)
        if path == "/api/project_zip":
            return self.handle_project_zip(body)
        if path == "/api/preview":
            return self.handle_preview(body)
        if path == "/api/quit":
            self._send(200, {"bye": True})
            threading.Timer(0.4, lambda: os._exit(0)).start()
            return
        return self._send(404, {"error": "not found"})

    def handle_config(self, body):
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
        for k, lo, hi in (("max_tokens", 1000, 32000), ("token_budget", 0, 100000000),
                          ("agent_rounds", 1, 6)):
            if k in body:
                try:
                    CONFIG[k] = max(lo, min(hi, int(body[k] or 0)))
                except Exception:
                    pass
        for k in ("cache", "auto_repair"):
            if k in body:
                CONFIG[k] = bool(body[k])
        ok = save_config(CONFIG)
        return self._send(200, {
            "saved": ok,
            "sf_key_set": bool(sf_key()),
            "groq_key_set": bool(groq_key()),
        })

    def handle_lint(self, body):
        """Instant, free static analysis -- powers live feedback while you type."""
        code = body.get("main_py") or ""
        reqs = fix_requirements(body.get("requirements", ""))
        perms = clean_perms(body.get("permissions", ""))
        sok, smsg = syntax_check(code)
        issues = analyze_code(code, reqs, perms) if code.strip() else []
        errs = [i["msg"] for i in issues if i["sev"] == "error"]
        return self._send(200, {
            "syntax_ok": sok, "syntax_msg": smsg, "issues": issues,
            "errors": errs, "warnings": [i["msg"] for i in issues if i["sev"] == "warn"],
            "kit_lines": kit_line_offset() if KIT_END in code else 0,
        })

    def handle_repair(self, body):
        """Run the free local repair pass and hand back what changed."""
        code = body.get("main_py") or ""
        has_kit = KIT_END in code
        app = strip_kit(code) if has_kit else code
        app, reqs, perms, fixes = auto_repair(app, body.get("requirements", ""),
                                              body.get("permissions", ""))
        full = with_kit(app) if has_kit else app
        payload = {
            "ok": True, "main_py": full, "app_py": app,
            "name": slugify(body.get("name", "app")),
            "title": body.get("title", "") or slugify(body.get("name", "app")).replace("_", " ").title(),
            "orientation": body.get("orientation", "portrait"),
            "requirements": fix_requirements(reqs), "permissions": clean_perms(perms),
            "notes": "", "build_overrides": {}, "build_warnings": [],
            "repairs": fixes, "provider": "local repair (0 tokens)",
        }
        _recompute(payload, repair=False)
        payload["usage"] = dict(USAGE)
        return self._send(200, payload)

    def handle_autoforge(self, body):
        desc = (body.get("description") or "").strip()
        seed = None
        if body.get("main_py"):
            seed = manual_payload(body.get("name", "app"), strip_kit(body["main_py"]),
                                  title=body.get("title", ""),
                                  requirements=body.get("requirements", "python3,kivy"),
                                  permissions=body.get("permissions", ""),
                                  orientation=body.get("orientation", "portrait"),
                                  kit=True)
        elif not desc:
            return self._send(400, {"error": "give me a description or some code to work on"})
        jid = uuid.uuid4().hex[:12]
        JOBS[jid] = {"status": "running", "steps": [], "round": 0, "payload": None}
        threading.Thread(target=run_agent,
                         args=(jid, desc, seed, body.get("rounds")), daemon=True).start()
        return self._send(200, {"job_id": jid})

    def _keytest(self):
        """Diagnose a key by testing it TWO ways: curl and Python urllib.
        If curl works but Python doesn't, the bug is in our request construction.
        If both fail, the key genuinely doesn't work for this endpoint."""
        key = sf_key()
        if not key:
            return self._send(200, {"ok": False, "curl_ok": False, "py_ok": False,
                "detail": "No API key is set. Paste your SiliconFlow key in the field above and Save first."})
        model = CONFIG.get("sf_model") or SF_MODEL
        url = chat_url(CONFIG.get("sf_url") or SF_URL)
        masked = key[:6] + "..." + key[-4:] if len(key) > 12 else "***"
        payload = json.dumps({
            "model": model, "messages": [{"role": "user", "content": "say ok"}],
            "temperature": 0, "max_tokens": 3,
        })
        diag = {"url": url, "model": model, "key_masked": masked,
                "key_len": len(key), "key_prefix": key[:3]}

        # --- test 1: curl (the ground truth, no Python in the way) ---
        curl_ok, curl_detail = False, ""
        try:
            cmd = [
                "curl", "-s", "-w", "\n%{http_code}", "-X", "POST", url,
                "-H", "Content-Type: application/json",
                "-H", "Authorization: Bearer " + key,
                "-d", payload,
                "--max-time", "15",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            parts = proc.stdout.rsplit("\n", 1)
            body = parts[0] if len(parts) > 1 else proc.stdout
            code = int(parts[-1]) if len(parts) > 1 and parts[-1].strip().isdigit() else 0
            if code == 200:
                curl_ok = True
                curl_detail = "curl got HTTP 200 -- key works via curl"
            else:
                curl_detail = "curl got HTTP %d: %s" % (code, body[:300])
            diag["curl_http"] = code
            diag["curl_body"] = body[:400]
        except Exception as e:
            curl_detail = "curl failed: %s" % e

        # --- test 2: Python urllib (what call_ai actually uses) ---
        py_ok, py_detail = False, ""
        try:
            req = urllib.request.Request(url, data=payload.encode("utf-8"), headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": UA,
            }, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                py_body = r.read().decode("utf-8")
                py_ok = True
                py_detail = "Python urllib got HTTP 200 -- key works"
                diag["py_http"] = r.status
        except urllib.error.HTTPError as e:
            try:
                py_body = e.read().decode("utf-8")[:400]
            except Exception:
                py_body = ""
            py_detail = "Python urllib got HTTP %d: %s" % (e.code, py_body[:300])
            diag["py_http"] = e.code
            diag["py_body"] = py_body
        except Exception as e:
            py_detail = "Python urllib error: %s" % e

        # --- verdict ---
        if curl_ok and py_ok:
            detail = "Key works. Both curl and Python reached %s with model %s." % (url, model)
        elif curl_ok and not py_ok:
            detail = ("CURL WORKS but Python fails -- the bug is in the request headers. "
                      "Curl: %s. Python: %s" % (curl_detail, py_detail))
        elif not curl_ok and not py_ok:
            detail = ("Both curl and Python fail -- the key or model is rejected by the server. "
                      "Curl: %s. Python: %s" % (curl_detail, py_detail))
        else:
            detail = "Curl: %s. Python: %s" % (curl_detail, py_detail)

        diag["curl_ok"] = curl_ok
        diag["py_ok"] = py_ok
        return self._send(200, {"ok": curl_ok or py_ok, "detail": detail, "diag": diag})

    def handle_forge(self, body):
        desc = (body.get("description") or "").strip()
        if not desc:
            return self._send(400, {"error": "empty description"})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # TOKEN DISCIPLINE: v2 replayed every previous full response (kit and all) into
        # every follow-up, so a fourth refine cost four whole apps. Now the context is
        # the short user turns plus the CURRENT app code once, kit stripped.
        for turn in (body.get("history") or [])[-6:]:
            if turn.get("role") == "user" and turn.get("content"):
                messages.append({"role": "user", "content": str(turn["content"])[:600]})
        cur = strip_kit(body.get("main_py") or "")
        if cur.strip():
            messages.append({"role": "assistant",
                             "content": "<<<MAIN_PY>>>\n" + cur + "\n<<<END>>>"})
        messages.append({"role": "user", "content": desc})
        try:
            text, provider = call_ai(messages, temperature=0.4, label="forge")
        except Exception as e:
            return self._send(502, {"error": str(e)})
        payload = build_forge_payload(text, desc)
        if payload.get("ok"):
            payload["main_py"] = with_kit(payload["main_py"])
            _recompute(payload)
        payload["provider"] = provider
        payload["usage"] = dict(USAGE)
        return self._send(200, payload)

    def handle_fix(self, body):
        main_py = body.get("main_py") or ""
        error = body.get("error") or ""
        if not main_py.strip():
            return self._send(400, {"error": "no main_py to fix"})
        if not error.strip():
            # derive findings from analysis if the caller didn't pass an explicit error
            reqs = fix_requirements(body.get("requirements", ""))
            issues = analyze_code(main_py, reqs, body.get("permissions", ""))
            error = "\n".join("- " + it["msg"] for it in issues) or "no explicit error; review for robustness"
        try:
            payload = ai_fix(main_py, error, body.get("requirements", ""), body.get("permissions", ""))
        except Exception as e:
            return self._send(502, {"error": str(e)})
        return self._send(200, payload)

    def handle_polish(self, body):
        main_py = body.get("main_py") or ""
        if not main_py.strip():
            return self._send(400, {"error": "no main_py to polish"})
        try:
            payload = ai_polish(main_py, body.get("requirements", ""), body.get("permissions", ""))
        except Exception as e:
            return self._send(502, {"error": str(e)})
        return self._send(200, payload)

    def handle_testrun(self, body):
        main_py = body.get("main_py") or ""
        requirements = fix_requirements(body.get("requirements", ""))
        if not main_py.strip():
            return self._send(400, {"error": "no main_py to test"})
        sok, smsg = syntax_check(main_py)
        if not sok:
            return self._send(400, {"error": "fix the syntax error first: " + smsg})
        tid = uuid.uuid4().hex[:12]
        TESTS[tid] = {"log": [], "status": "running", "summary": ""}
        threading.Thread(target=run_test, args=(tid, main_py, requirements), daemon=True).start()
        return self._send(200, {"test_id": tid})

    def handle_preview(self, body):
        """Launch the phone-frame desktop preview of the current app -- a live window sized
        like a real Android screen, so you SEE it before a 40-minute build. Fire-and-forget:
        it opens its own window on the user's desktop session."""
        main_py = body.get("main_py") or ""
        device = (body.get("device") or (getattr(_devices, "DEFAULT", "") if _devices else "")).strip()
        if not main_py.strip():
            return self._send(400, {"error": "nothing to preview yet -- forge or write an app first"})
        sok, smsg = syntax_check(main_py)
        if not sok:
            return self._send(400, {"error": "fix the syntax error before previewing: " + smsg})
        if not host_has_kivy():
            return self._send(400, {"error": "the preview needs desktop Kivy. Install it once: "
                                             "pip install --user kivy  (the APK build itself doesn't need it)."})
        if not os.path.exists(_PREVIEW_PY):
            return self._send(400, {"error": "preview.py isn't installed next to apkforge.py -- re-run install.sh."})
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return self._send(400, {"error": "no desktop display detected, so a preview window can't open here."})
        pdir = os.path.join(PROJECTS, "_preview")
        os.makedirs(pdir, exist_ok=True)
        try:
            with open(os.path.join(pdir, "main.py"), "w") as f:
                f.write(main_py)
        except Exception as e:
            return self._send(500, {"error": "couldn't stage the preview: %s" % e})
        cmd = [sys.executable, _PREVIEW_PY, os.path.join(pdir, "main.py")]
        if device:
            cmd += ["--device", device]
        try:
            subprocess.Popen(cmd, cwd=pdir, env=dict(os.environ),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return self._send(500, {"error": "couldn't launch the preview: %s" % e})
        return self._send(200, {"started": True, "device": device or "default"})

    def _overrides_from(self, body):
        """Re-validate build overrides coming from the client (never trust a raw dict)."""
        ov = body.get("build_overrides") or {}
        if not isinstance(ov, dict):
            return {}, []
        lines = "\n".join("%s = %s" % (k, v) for k, v in ov.items())
        clean, warns = parse_build_overrides(lines)
        return clean, warns

    def handle_project_zip(self, body):
        name = slugify(body.get("name", "app"))
        title = (body.get("title") or name).strip()
        main_py = body.get("main_py") or ""
        orientation = (body.get("orientation") or "portrait").strip().lower()
        if orientation not in ("portrait", "landscape", "all"):
            orientation = "portrait"
        requirements = fix_requirements(body.get("requirements", ""))
        permissions = clean_perms(body.get("permissions", ""))
        archs = safe_archs(body.get("archs", ANDROID_ARCHS))
        overrides, _ = self._overrides_from(body)
        if not main_py.strip():
            return self._send(400, {"error": "no main_py to package"})
        # render assets into bytes so the zip is build-ready out of the box (prefer the
        # richer iconsmith, fall back to the built-in generator)
        icon_b = splash_b = None
        try:
            if _iconsmith is not None:
                icon_b = _iconsmith.icon_png(title or name, 512)
                splash_b = _iconsmith.presplash_png(title or name, 720)
            else:
                icon_b = icon_png(title or name, 512)
                splash_b = presplash_png(title or name, 720)
        except Exception:
            icon_b = splash_b = None
        pcolor = None
        try:
            pcolor = presplash_hex(title or name)
        except Exception:
            pcolor = None
        spec = make_spec(title, name, requirements, permissions, orientation,
                         archs=archs, icon=bool(icon_b), presplash=bool(splash_b),
                         presplash_color=pcolor, overrides=overrides)
        readme = (
            "%s -- Buildozer project forged by The Dawg.\n\n"
            "Build the APK on a Linux box with the SDK/NDK:\n"
            "  cd %s\n"
            "  buildozer android debug\n\n"
            "APK lands in bin/.\n\n"
            "Preview it first (no build needed), in a phone-shaped desktop window:\n"
            "  pip install --user kivy      # once\n"
            "  ./run_preview.sh             # or: python3 ../preview.py %s/main.py\n"
            % (title, name, name)
        )
        run_preview = (
            "#!/usr/bin/env bash\n"
            "# Preview this app at real Android-phone size before building the APK.\n"
            "cd \"$(dirname \"$0\")\"\n"
            "exec python3 preview.py \"%s/main.py\" --device pixel_8 \"$@\"\n" % name
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name + "/main.py", main_py)
            z.writestr(name + "/buildozer.spec", spec)
            z.writestr(name + "/README.txt", readme)
            if icon_b:
                z.writestr(name + "/icon.png", icon_b)
            if splash_b:
                z.writestr(name + "/presplash.png", splash_b)
            # bundle the preview tooling at the zip root so the project previews anywhere
            for mod in ("preview.py", "devices.py", "iconsmith.py"):
                p = os.path.join(_HERE, mod)
                try:
                    if os.path.exists(p):
                        with open(p, "r") as mf:
                            z.writestr(mod, mf.read())
                except Exception:
                    pass
            zi = zipfile.ZipInfo("run_preview.sh")
            zi.external_attr = 0o755 << 16   # make it executable
            z.writestr(zi, run_preview)
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

    def handle_build(self, body):
        name = slugify(body.get("name", "app"))
        title = (body.get("title") or name).strip()
        main_py = body.get("main_py") or ""
        orientation = (body.get("orientation") or "portrait").strip().lower()
        if orientation not in ("portrait", "landscape", "all"):
            orientation = "portrait"
        requirements = fix_requirements(body.get("requirements", ""))
        permissions = clean_perms(body.get("permissions", ""))
        archs = safe_archs(body.get("archs", ANDROID_ARCHS))
        overrides, _ = self._overrides_from(body)
        manual_spec = body.get("spec") or ""
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
            jhint = pkg_hint(pacman="sudo pacman -S jdk17-openjdk",
                             apt="sudo apt install -y openjdk-17-jdk",
                             dnf="sudo dnf install -y java-17-openjdk-devel",
                             zypper="sudo zypper install java-17-openjdk-devel") \
                    or "install a JDK 17 (must be 17-24, not 25+)"
            return self._send(400, {"error":
                "Java %s is active, but Buildozer's Gradle needs JDK 17-24. The build would "
                "run for ages then die at the Gradle step. Fix: %s  then relaunch The Dawg "
                "(it points JAVA_HOME at a compatible JDK automatically once one is "
                "installed)." % (jver, jhint)})
        project_dir = os.path.join(PROJECTS, name)
        os.makedirs(project_dir, exist_ok=True)
        if body.get("clean"):
            shutil.rmtree(os.path.join(project_dir, ".buildozer"), ignore_errors=True)
        with open(os.path.join(project_dir, "main.py"), "w") as f:
            f.write(main_py)
        # generate a pro launcher icon + matching splash (kills the white launch flash)
        icon_ok = splash_ok = False
        pcolor = None
        if not manual_spec.strip():
            try:
                icon_ok, splash_ok = write_assets(project_dir, title or name)
            except Exception:
                icon_ok = splash_ok = False
            try:
                pcolor = presplash_hex(title or name)
            except Exception:
                pcolor = None
        if manual_spec.strip() and "[app]" in manual_spec and "package.name" in manual_spec:
            spec = manual_spec  # user owns it in manual mode
        else:
            spec = make_spec(title, name, requirements, permissions, orientation,
                             archs=archs, icon=icon_ok, presplash=splash_ok,
                             presplash_color=pcolor, overrides=overrides)
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
<link rel="icon" type="image/png" href="/icon.png">
<link rel="apple-touch-icon" href="/icon.png">
<style>
  :root{
    --bg:#070a0e; --bg2:#0a0f15; --panel:#0e141c; --panel2:#131b25; --panel3:#182231;
    --line:#1e2a3a; --line2:#2a3a4f;
    --txt:#e2ecf7; --muted:#7b8da0; --dim:#546274;
    --green:#3ddc84; --cyan:#43c8f5; --violet:#8b7cf6;
    --danger:#ff5f6d; --amber:#ffb340;
    --r:12px; --r2:16px;
    --sh:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
    --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,"JetBrains Mono","DejaVu Sans Mono",monospace;
    --ui:system-ui,-apple-system,"Segoe UI",Inter,Roboto,"Helvetica Neue",sans-serif;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:
      radial-gradient(1100px 620px at 78% -12%, rgba(67,200,245,.09), transparent 62%),
      radial-gradient(900px 560px at 8% 4%, rgba(139,124,246,.08), transparent 60%),
      var(--bg);
    color:var(--txt);font-family:var(--ui);font-size:14.5px;line-height:1.55;
    -webkit-font-smoothing:antialiased}
  ::selection{background:rgba(67,200,245,.28)}

  /* ---------- scrollbars ---------- */
  ::-webkit-scrollbar{width:11px;height:11px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:#1d2836;border-radius:8px;border:3px solid transparent;background-clip:content-box}
  ::-webkit-scrollbar-thumb:hover{background:#2b3a4d;border:3px solid transparent;background-clip:content-box}

  /* ---------- header ---------- */
  header{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:14px;
    padding:12px 22px;border-bottom:1px solid var(--line);
    background:rgba(8,12,17,.82);backdrop-filter:blur(14px) saturate(160%)}
  .brand{display:flex;align-items:center;gap:11px;font-weight:650;letter-spacing:.4px;font-size:14.5px}
  .logo{width:30px;height:30px;border-radius:9px;flex:0 0 auto;
    background:linear-gradient(145deg,var(--cyan),var(--violet));
    display:grid;place-items:center;color:#04080d;font-weight:800;font-size:14px;
    box-shadow:0 0 0 1px rgba(67,200,245,.35),0 6px 18px -6px rgba(67,200,245,.55);
    font-family:var(--mono)}
  .brand em{font-style:normal;color:var(--cyan)}
  .ver{font-size:10.5px;color:var(--dim);font-weight:500;letter-spacing:1px;
    border:1px solid var(--line);padding:2px 7px;border-radius:20px;font-family:var(--mono)}
  .grow{margin-left:auto}
  .hdr-tools{display:flex;align-items:center;gap:8px}

  /* ---------- generic bits ---------- */
  main{max-width:1180px;margin:0 auto;padding:22px 22px 90px}
  .panel{background:linear-gradient(180deg,var(--panel),var(--bg2));
    border:1px solid var(--line);border-radius:var(--r2);padding:18px;margin-bottom:18px;
    box-shadow:var(--sh)}
  .panel.tight{padding:14px 16px}
  label,.lbl{display:block;color:var(--muted);font-size:11.5px;margin-bottom:7px;
    letter-spacing:.9px;text-transform:uppercase;font-weight:600}
  .sub{color:var(--dim);font-size:12.5px;font-weight:400;text-transform:none;letter-spacing:0}
  textarea,input[type=text],input[type=password],input[type=number],select{
    width:100%;background:var(--panel2);color:var(--txt);border:1px solid var(--line);
    border-radius:var(--r);padding:11px 13px;font-family:var(--ui);font-size:13.5px;
    outline:none;transition:border-color .15s, box-shadow .15s}
  textarea:focus,input:focus,select:focus{border-color:var(--cyan);
    box-shadow:0 0 0 3px rgba(67,200,245,.13)}
  textarea{resize:vertical}
  #desc{height:104px;font-size:14px;line-height:1.6}
  select{appearance:none;cursor:pointer;
    background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
    background-position:calc(100% - 17px) 50%,calc(100% - 12px) 50%;
    background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:34px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
  @media(max-width:760px){.grid4{grid-template-columns:1fr 1fr}}
  .hint{color:var(--dim);font-size:12.5px;margin:0}
  .hidden{display:none !important}

  /* ---------- buttons ---------- */
  button{cursor:pointer;border:1px solid var(--line2);background:var(--panel3);
    color:var(--txt);padding:10px 17px;border-radius:10px;font-family:var(--ui);
    font-size:13px;font-weight:600;letter-spacing:.3px;transition:.14s;
    display:inline-flex;align-items:center;gap:7px;white-space:nowrap}
  button:hover:not(:disabled){border-color:#3d5570;background:#1c2736;transform:translateY(-1px)}
  button:active:not(:disabled){transform:translateY(0)}
  button:disabled{opacity:.38;cursor:not-allowed}
  button.primary{background:linear-gradient(180deg,#3ddc84,#2bb96b);border-color:#2bb96b;color:#04140b}
  button.primary:hover:not(:disabled){box-shadow:0 6px 20px -8px rgba(61,220,132,.75);background:linear-gradient(180deg,#4ce792,#31c574)}
  button.accent{background:linear-gradient(180deg,#43c8f5,#2ba7d4);border-color:#2ba7d4;color:#03151d}
  button.accent:hover:not(:disabled){box-shadow:0 6px 20px -8px rgba(67,200,245,.75)}
  button.violet{background:linear-gradient(180deg,#8b7cf6,#6f5fe0);border-color:#6f5fe0;color:#0b0720}
  button.violet:hover:not(:disabled){box-shadow:0 6px 20px -8px rgba(139,124,246,.7)}
  button.warn{background:#2a2010;border-color:#5d4820;color:var(--amber)}
  button.ghost{background:transparent}
  button.sm{padding:7px 12px;font-size:12px;border-radius:9px}
  .chip{padding:7px 13px;font-size:12.5px;border-radius:20px;font-weight:500;
    background:var(--panel2);border-color:var(--line)}
  .chip:hover:not(:disabled){border-color:var(--cyan);color:var(--cyan)}

  /* ---------- tabs ---------- */
  .tabs{display:inline-flex;gap:4px;padding:4px;background:var(--panel);
    border:1px solid var(--line);border-radius:13px;margin-bottom:18px}
  .tab{padding:8px 20px;border-radius:9px;color:var(--muted);cursor:pointer;
    font-weight:600;font-size:13px;transition:.15s;user-select:none;letter-spacing:.3px}
  .tab:hover{color:var(--txt)}
  .tab.active{color:#04080d;background:linear-gradient(180deg,var(--cyan),#2ba7d4);
    box-shadow:0 4px 14px -6px rgba(67,200,245,.8)}

  /* ---------- status pills ---------- */
  .pills{display:flex;gap:7px;flex-wrap:wrap;align-items:center}
  .pill{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;padding:5px 11px;
    border:1px solid var(--line);border-radius:20px;color:var(--muted);
    background:var(--panel2);font-family:var(--mono);white-space:nowrap}
  .pill b{color:var(--txt);font-weight:650}
  .pill.ok{color:var(--green);border-color:rgba(61,220,132,.35);background:rgba(61,220,132,.09)}
  .pill.bad{color:var(--danger);border-color:rgba(255,95,109,.35);background:rgba(255,95,109,.09)}
  .pill.warn{color:var(--amber);border-color:rgba(255,179,64,.32);background:rgba(255,179,64,.09)}
  .pill.info{color:var(--cyan);border-color:rgba(67,200,245,.3);background:rgba(67,200,245,.08)}
  .dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex:0 0 auto}
  .pill.ok .dot{box-shadow:0 0 8px currentColor}
  .docwrap{position:relative}
  #docsum{cursor:pointer}
  #docsum:hover{border-color:var(--line2)}
  #doctor{position:absolute;right:0;top:34px;display:none;flex-direction:column;gap:6px;
    padding:12px;background:var(--panel3);border:1px solid var(--line2);border-radius:13px;
    box-shadow:0 20px 50px -16px rgba(0,0,0,.92);min-width:270px;z-index:40}
  #doctor.open{display:flex}
  #doctor .pill{justify-content:flex-start}

  /* ---------- editor ---------- */
  .editor{position:relative;border:1px solid var(--line);border-radius:var(--r);
    background:#070b10;overflow:hidden;display:flex;height:480px;min-height:180px;
    resize:vertical;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
  .editor:focus-within{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(67,200,245,.11)}
  .gutter{flex:0 0 auto;height:100%;padding:13px 10px 13px 14px;text-align:right;color:#39485c;
    font-family:var(--mono);font-size:12.5px;line-height:1.65;user-select:none;
    background:#080d13;border-right:1px solid var(--line);overflow:hidden;min-width:54px}
  .gutter span{display:block}
  .gutter span.kit{color:#26313f}
  #code{flex:1;height:100%;border:0;border-radius:0;background:transparent;
    font-family:var(--mono);font-size:12.5px;line-height:1.65;padding:13px 14px;
    white-space:pre;overflow:auto;tab-size:4;resize:none;box-shadow:none !important}
  #code:focus{box-shadow:none}
  .edbar{display:flex;align-items:center;gap:10px;margin:12px 0 8px;flex-wrap:wrap}

  /* ---------- issues ---------- */
  .issues{background:#070b10;border:1px solid var(--line);border-radius:var(--r);
    margin-top:14px;max-height:280px;overflow:auto}
  .issue{display:flex;gap:11px;padding:10px 13px;border-bottom:1px solid #101822;
    font-size:13px;align-items:flex-start}
  .issue:last-child{border-bottom:0}
  .issue:hover{background:#0a1017}
  .sev{flex:0 0 auto;font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:.6px;
    padding:3px 7px;border-radius:5px;margin-top:1px;min-width:52px;text-align:center}
  .sev.error{color:#ffd0d4;background:rgba(255,95,109,.18);border:1px solid rgba(255,95,109,.34)}
  .sev.warn{color:#ffe2b8;background:rgba(255,179,64,.15);border:1px solid rgba(255,179,64,.32)}
  .sev.info{color:#c4e9fb;background:rgba(67,200,245,.14);border:1px solid rgba(67,200,245,.3)}
  .sev.ok{color:#c2f5da;background:rgba(61,220,132,.14);border:1px solid rgba(61,220,132,.32)}
  .issue .fix{color:var(--dim);font-size:12px;margin-top:3px}
  .issue .fix b{color:var(--cyan);font-weight:600}

  /* ---------- agent rail ---------- */
  .rail{max-height:300px;overflow:auto;background:#070b10;border:1px solid var(--line);
    border-radius:var(--r);padding:6px 0;margin-top:12px}
  .stepr{display:flex;gap:11px;padding:7px 14px;font-size:12.8px;align-items:flex-start;
    font-family:var(--mono)}
  .stepr .ic{flex:0 0 auto;width:16px;text-align:center;font-size:12px;margin-top:1px}
  .stepr.ok .ic{color:var(--green)} .stepr.fail .ic{color:var(--danger)}
  .stepr.warn .ic{color:var(--amber)} .stepr.step .ic{color:var(--cyan)}
  .stepr.info .ic{color:var(--dim)}
  .stepr .tx{flex:1;color:var(--muted);word-break:break-word}
  .stepr.ok .tx,.stepr.step .tx{color:var(--txt)}

  /* ---------- phases ---------- */
  .phases{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}
  .ph{display:flex;align-items:center;gap:6px;font-size:11.5px;padding:5px 10px;
    border-radius:8px;font-family:var(--mono);border:1px solid var(--line);background:var(--panel2)}
  .ph.ok{color:var(--green);border-color:rgba(61,220,132,.3);background:rgba(61,220,132,.08)}
  .ph.bad{color:var(--danger);border-color:rgba(255,95,109,.35);background:rgba(255,95,109,.1)}

  /* ---------- logs ---------- */
  .log{height:320px;white-space:pre-wrap;overflow:auto;background:#070b10;
    border:1px solid var(--line);border-radius:var(--r);padding:13px;font-size:12px;
    color:#9fb6cc;font-family:var(--mono);line-height:1.6}
  #testout{height:210px}

  /* ---------- section headings ---------- */
  .sect{display:flex;align-items:center;gap:10px;margin:0 0 12px}
  .sect h3{margin:0;font-size:12px;letter-spacing:1.2px;text-transform:uppercase;
    color:var(--muted);font-weight:700}
  .sect .line{flex:1;height:1px;background:linear-gradient(90deg,var(--line),transparent)}

  /* ---------- modal ---------- */
  .overlay{position:fixed;inset:0;background:rgba(3,6,10,.72);backdrop-filter:blur(6px);
    display:flex;align-items:center;justify-content:center;z-index:60;padding:20px}
  .modal{background:linear-gradient(180deg,var(--panel),var(--bg2));border:1px solid var(--line2);
    border-radius:18px;width:min(620px,96vw);max-height:88vh;overflow:auto;padding:24px;
    box-shadow:0 30px 90px -20px rgba(0,0,0,.9)}
  .modal h2{margin:0 0 4px;font-size:16px;letter-spacing:.3px;font-weight:700}
  .modal .msub{color:var(--dim);font-size:12.5px;margin:0 0 20px}
  .field{margin-bottom:16px}
  .modal details{margin:6px 0 16px;border-top:1px solid var(--line);padding-top:14px}
  .modal summary{cursor:pointer;color:var(--muted);font-size:12.5px;font-weight:600;
    letter-spacing:.4px;user-select:none}
  .modal summary:hover{color:var(--cyan)}
  .check{display:flex;align-items:center;gap:9px;color:var(--txt);font-size:13px;
    margin:0;text-transform:none;letter-spacing:0;font-weight:500}
  .check input{width:auto;margin:0;accent-color:var(--cyan)}
  .checks{display:flex;flex-direction:column;gap:11px}

  /* ---------- toasts ---------- */
  #toasts{position:fixed;right:20px;bottom:20px;z-index:80;display:flex;
    flex-direction:column;gap:9px;align-items:flex-end}
  .toast{background:var(--panel3);border:1px solid var(--line2);border-left:3px solid var(--cyan);
    border-radius:11px;padding:11px 16px;font-size:13px;max-width:400px;
    box-shadow:0 14px 40px -14px rgba(0,0,0,.9);animation:tin .22s ease-out}
  .toast.ok{border-left-color:var(--green)}
  .toast.bad{border-left-color:var(--danger)}
  .toast.warn{border-left-color:var(--amber)}
  @keyframes tin{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}

  /* ---------- spinner + progress ---------- */
  .spin{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.2);
    border-top-color:currentColor;border-radius:50%;animation:sp .7s linear infinite}
  @keyframes sp{to{transform:rotate(360deg)}}
  .bar{height:2px;background:var(--line);border-radius:2px;overflow:hidden;margin-top:14px}
  .bar i{display:block;height:100%;width:30%;border-radius:2px;
    background:linear-gradient(90deg,var(--cyan),var(--violet));animation:sl 1.1s ease-in-out infinite}
  @keyframes sl{0%{margin-left:-30%}100%{margin-left:100%}}

  /* ---------- sticky action bar ---------- */
  .actions{position:sticky;bottom:0;z-index:20;margin:16px -18px -18px;padding:14px 18px;
    background:rgba(10,15,21,.93);backdrop-filter:blur(12px);
    border-top:1px solid var(--line);border-radius:0 0 var(--r2) var(--r2);
    display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .empty{text-align:center;padding:44px 20px;color:var(--dim)}
  .empty .big{font-size:38px;opacity:.35;margin-bottom:10px}
</style>
</head>
<body>
<header>
  <div class="brand">
    <div class="logo">D</div>
    <span>THE DAWG <em>// APK FORGE</em></span>
    <span class="ver">v3.0</span>
  </div>
  <span class="grow"></span>
  <div class="hdr-tools">
    <div class="docwrap">
      <span class="pill" id="docsum" onclick="toggleDoctor()"><span class="dot"></span>checking...</span>
      <div id="doctor"></div>
    </div>
    <span class="pill info" id="tokpill" title="tokens used this session"><span class="dot"></span><b>0</b> tok</span>
    <button class="sm ghost" onclick="openSettings()">&#9881;&#xFE0E; Settings</button>
    <button class="sm ghost" onclick="quitApp()">Quit</button>
  </div>
</header>

<main>
  <div class="tabs">
    <div class="tab active" id="tab_ai" onclick="setMode('ai')">AI FORGE</div>
    <div class="tab" id="tab_manual" onclick="setMode('manual')">MANUAL</div>
  </div>

  <!-- ============ AI panel ============ -->
  <div class="panel" id="ai_panel">
    <label>Describe the Android app you want
      <span class="sub">&mdash; the UI kit, launcher icon and splash are added for you</span></label>
    <textarea id="desc" placeholder="a dark pomodoro timer with start/pause, a circular countdown ring, and a session counter that survives a restart"></textarea>
    <div class="row" style="margin-top:12px">
      <button class="primary" id="forgeBtn" onclick="forge()">Forge app</button>
      <button class="violet" id="autoBtn" onclick="autoForge()" title="forge, then check, self-test and fix until it passes">Forge &amp; verify</button>
      <span class="hint">Ctrl+Enter to forge. <b style="color:var(--violet)">Forge &amp; verify</b> runs the whole loop: build &rarr; repair &rarr; self-test &rarr; fix.</span>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="chip" onclick="refine('Make it look more polished and modern: tighten the layout, spacing and hierarchy')">&#9733; polish look</button>
      <button class="chip" onclick="refine('Add sound effects generated at runtime, no external files')">&#9834; add sound</button>
      <button class="chip" onclick="refine('Add a settings screen and persist preferences in user_data_dir')">&#9881;&#xFE0E; add settings</button>
      <button class="chip" onclick="refine('Add a high score / stats screen saved between launches')">&#9733; add scores</button>
      <button class="chip" onclick="refine('Add a dark/light theme toggle that persists')">&#9681; theme toggle</button>
    </div>
  </div>

  <!-- ============ Manual panel ============ -->
  <div class="panel hidden" id="manual_panel">
    <div class="sect"><h3>Manual mode</h3><span class="line"></span></div>
    <p class="hint" style="margin-bottom:14px">
      The editor is empty and the file is exactly what you type &mdash; no kit, no scaffold.
      Add the Dawg UI kit or drop in a starter only if you want one.</p>
    <div class="row">
      <select id="tpl_sel" style="max-width:340px"></select>
      <button onclick="loadTemplate()">Insert starter</button>
      <button class="ghost" onclick="toggleKit()" id="kitBtn">+ Add UI kit</button>
      <button class="ghost" onclick="newBlank()">Clear</button>
    </div>
  </div>

  <!-- ============ Workspace ============ -->
  <div class="panel hidden" id="out">
    <div class="pills" id="meta" style="margin-bottom:16px"></div>

    <div class="grid4" style="margin-bottom:12px">
      <div><label>App name <span class="sub">(package)</span></label><input type="text" id="f_name" placeholder="my_app"></div>
      <div><label>Title</label><input type="text" id="f_title" placeholder="My App"></div>
      <div><label>Orientation</label>
        <select id="f_orient">
          <option value="portrait">portrait</option>
          <option value="landscape">landscape</option>
          <option value="all">all</option>
        </select>
      </div>
      <div><label>Permissions</label><input type="text" id="f_perms" placeholder="INTERNET,VIBRATE"></div>
    </div>
    <div style="margin-bottom:14px"><label>Requirements</label>
      <input type="text" id="f_reqs" placeholder="python3,kivy"></div>

    <div class="edbar">
      <span class="lbl" style="margin:0">main.py</span>
      <span class="pill" id="kitpill"></span>
      <span class="grow"></span>
      <span class="pill" id="livelint"></span>
    </div>
    <div class="editor">
      <div class="gutter" id="gutter"></div>
      <textarea id="code" spellcheck="false" autocomplete="off" autocapitalize="off"></textarea>
    </div>

    <div class="issues" id="validation"></div>

    <details style="margin-top:14px">
      <summary style="cursor:pointer;color:var(--muted);font-size:12.5px;font-weight:600">Advanced build config</summary>
      <div class="checks" style="margin-top:14px">
        <label class="check"><input type="checkbox" id="arch_a64" checked> arm64-v8a <span class="hint">&mdash; every modern phone; halves build time</span></label>
        <label class="check"><input type="checkbox" id="arch_a32"> armeabi-v7a <span class="hint">&mdash; old 32-bit hardware</span></label>
      </div>
      <div class="grid2" style="margin-top:14px;max-width:440px">
        <div><label>android.api</label><input type="text" id="b_api" value="34"></div>
        <div><label>android.minapi</label><input type="text" id="b_minapi" value="24"></div>
      </div>
    </details>

    <div id="testpanel" class="hidden" style="margin-top:18px">
      <div class="sect"><h3>Self-test</h3><span class="line"></span></div>
      <div class="phases" id="phases"></div>
      <div class="log" id="testout" style="margin-top:12px"></div>
    </div>

    <div class="actions">
      <button class="accent" id="buildBtn" onclick="buildApk()">Build APK</button>
      <button id="previewBtn" onclick="previewApp()" title="open the app in a phone-shaped window on your desktop -- no build needed">&#128241; Preview</button>
      <select id="previewDevice" title="preview device" style="max-width:180px"></select>
      <button id="testBtn" onclick="testRun()">Self-test</button>
      <button class="warn" id="fixBtn" onclick="autoFix()">Auto-fix</button>
      <button id="repairBtn" onclick="localRepair()" title="deterministic fixes, no API call">Repair free</button>
      <button id="polishBtn" onclick="polish()">Polish</button>
      <button class="ghost" id="zipBtn" onclick="downloadProject()">Download project</button>
    </div>
  </div>

  <!-- ============ Idle hint ============ -->
  <div class="panel" id="idle">
    <div class="empty">
      <div class="big">&#9874;</div>
      <div style="color:var(--muted);font-size:14.5px;margin-bottom:6px">Nothing forged yet</div>
      <div style="max-width:520px;margin:0 auto">Describe an app above, or switch to
        <b style="color:var(--cyan);cursor:pointer" onclick="setMode('manual')">MANUAL</b>
        to write it yourself in an empty file.</div>
      <div class="pills" style="justify-content:center;margin-top:18px">
        <span class="pill info">every app is static-checked before it can build</span>
        <span class="pill info">self-test taps every button before you burn 40 min</span>
        <span class="pill info">local repairs cost 0 tokens</span>
      </div>
    </div>
  </div>

  <!-- ============ Agent panel ============ -->
  <div class="panel hidden" id="agentwrap">
    <div class="sect"><h3>Forge &amp; verify</h3><span class="line"></span>
      <span class="pill" id="agentstat"></span></div>
    <div class="rail" id="rail"></div>
    <div class="bar hidden" id="agentbar"><i></i></div>
  </div>

  <!-- ============ Build log ============ -->
  <div class="panel hidden" id="logwrap">
    <div class="sect"><h3>Build log</h3><span class="line"></span>
      <button id="apkBtn" class="accent sm hidden" onclick="downloadApk()">&#8595; Download APK</button></div>
    <div class="log" id="log"></div>
  </div>
</main>

<!-- ============ Settings ============ -->
<div class="overlay hidden" id="settings" onclick="overlayClick(event)">
  <div class="modal">
    <h2>Settings</h2>
    <p class="msub">Keys live in ~/.androdawg/config.json (chmod 600). SiliconFlow is primary, Groq is the fallback.</p>

    <div class="field">
      <label>SiliconFlow API key <span class="sub" id="sfset"></span></label>
      <input type="password" id="sf_key" placeholder="sk-...  (blank keeps the current key)">
      <label class="check" style="margin-top:8px"><input type="checkbox" id="clear_sf"> clear stored key</label>
    </div>
    <div class="field">
      <label>Model</label>
      <select id="sf_model_sel" onchange="onModelChange()">
        <option value="deepseek-ai/DeepSeek-V4-Pro">deepseek-ai/DeepSeek-V4-Pro</option>
        <option value="deepseek-ai/DeepSeek-V4-Flash">deepseek-ai/DeepSeek-V4-Flash (cheaper)</option>
        <option value="deepseek-ai/DeepSeek-V3">deepseek-ai/DeepSeek-V3</option>
        <option value="Qwen/Qwen2.5-Coder-32B-Instruct">Qwen/Qwen2.5-Coder-32B-Instruct</option>
        <option value="__custom__">custom...</option>
      </select>
      <input type="text" id="sf_model_custom" class="hidden" placeholder="provider/model" style="margin-top:8px">
    </div>

    <details open>
      <summary>Token spend</summary>
      <div class="grid2" style="margin-top:14px">
        <div><label>Max tokens per call</label><input type="number" id="max_tokens" min="1000" max="32000" step="500"></div>
        <div><label>Session budget <span class="sub">(0 = off)</span></label><input type="number" id="token_budget" min="0" step="1000"></div>
      </div>
      <div style="margin-top:14px"><label>Auto-fix rounds <span class="sub">(forge &amp; verify)</span></label>
        <input type="number" id="agent_rounds" min="1" max="6"></div>
      <div class="checks" style="margin-top:16px">
        <label class="check"><input type="checkbox" id="cache"> Reuse identical responses <span class="hint">&mdash; repeats cost nothing</span></label>
        <label class="check"><input type="checkbox" id="auto_repair"> Repair locally first <span class="hint">&mdash; fix what code can fix before paying the model</span></label>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="sm ghost" onclick="clearCache()">Clear response cache</button>
        <span class="hint" id="usagenote"></span>
      </div>
    </details>

    <details>
      <summary>Endpoints &amp; Groq fallback</summary>
      <div class="field" style="margin-top:14px">
        <label>SiliconFlow endpoint</label>
        <input type="text" id="sf_url" placeholder="https://api.siliconflow.cn/v1">
      </div>
      <div class="field">
        <label>Groq API key <span class="sub" id="gqset"></span></label>
        <input type="password" id="groq_key" placeholder="gsk-...">
        <label class="check" style="margin-top:8px"><input type="checkbox" id="clear_groq"> clear stored key</label>
      </div>
      <div class="field"><label>Groq model</label><input type="text" id="groq_model" placeholder="llama-3.3-70b-versatile"></div>
      <div class="field"><label>Groq endpoint</label><input type="text" id="groq_url" placeholder="https://api.groq.com/openai/v1"></div>
    </details>

    <div class="row" style="margin-top:4px">
      <button class="primary" onclick="saveSettings()">Save</button>
      <button class="accent" onclick="testKey()" id="keytestBtn">Test key</button>
      <button class="ghost" onclick="closeSettings()">Close</button>
      <span class="hint" id="setmsg"></span>
    </div>
  </div>
</div>

<div id="toasts"></div>
<script>
var cur = null;          // current payload
var turns = [];          // short user turns only -- never full responses (token discipline)
var mode = 'ai';
var pollT = null, testT = null, jobT = null;
var lintT = null;
var useKit = false;      // manual mode starts with no kit at all

function $(id){return document.getElementById(id);}
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function show(id){$(id).classList.remove('hidden');}
function hide(id){$(id).classList.add('hidden');}

/* ---------------------------------------------------------------- toasts */
function toast(msg, kind){
  var d=document.createElement('div');
  d.className='toast '+(kind||'');
  d.textContent=msg;
  $('toasts').appendChild(d);
  setTimeout(function(){ d.style.transition='opacity .3s, transform .3s';
    d.style.opacity='0'; d.style.transform='translateX(20px)';
    setTimeout(function(){d.remove();},320); }, kind==='bad'?6500:3600);
}

/* ---------------------------------------------------------------- busy state */
function busy(btn, label){
  var b=$(btn); if(!b) return function(){};
  var old=b.innerHTML; b.disabled=true;
  b.innerHTML='<span class="spin"></span> '+label;
  return function(){ b.disabled=false; b.innerHTML=old; refreshButtons(); };
}

/* ---------------------------------------------------------------- mode */
function setMode(m){
  mode=m;
  $('tab_ai').classList.toggle('active', m==='ai');
  $('tab_manual').classList.toggle('active', m==='manual');
  $('ai_panel').classList.toggle('hidden', m!=='ai');
  $('manual_panel').classList.toggle('hidden', m!=='manual');
  if(m==='manual' && !cur){ newBlank(); }
}

/* Manual mode opens a genuinely empty file: no kit, no scaffold, nothing to delete. */
function newBlank(){
  useKit=false;
  cur={ok:true, name:'', title:'', orientation:'portrait', permissions:'',
       requirements:'python3,kivy', main_py:'', app_py:'', kit:false,
       issues:[], errors:[], warnings:[], syntax_ok:true, syntax_msg:'',
       provider:'local (no AI, 0 tokens)'};
  render(cur, true);
  $('code').focus();
}

async function toggleKit(){
  if(!cur){ newBlank(); }
  collect();
  useKit = !useKit;
  try{
    var r=await fetch('/api/manual',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:cur.name||'app', title:cur.title, main_py:cur.app_py||cur.main_py,
        requirements:cur.requirements, permissions:cur.permissions,
        orientation:cur.orientation, kit:useKit})});
    var d=await r.json();
    render(d);
    toast(useKit?'UI kit added above your code':'UI kit removed', 'ok');
  }catch(e){ toast('network: '+e,'bad'); }
}

/* ---------------------------------------------------------------- gutter */
function syncGutter(){
  var ta=$('code'), g=$('gutter');
  var lines=(ta.value||'').split('\n').length;
  var kitLines=(cur&&cur.kit_lines)?cur.kit_lines:0;
  var html='';
  for(var i=1;i<=lines;i++){
    html += '<span'+(i<=kitLines?' class="kit"':'')+'>'+i+'</span>';
  }
  g.innerHTML=html;
  g.scrollTop=ta.scrollTop;
}

/* ---------------------------------------------------------------- rendering */
function metaTags(p){
  var t=[];
  if(p.provider) t.push('<span class="pill">via <b>'+esc(p.provider)+'</b></span>');
  t.push('<span class="pill '+(p.syntax_ok?'ok':'bad')+'"><span class="dot"></span>syntax <b>'+
         (p.syntax_ok?'ok':'error')+'</b></span>');
  var ne=(p.errors||[]).length, nw=(p.warnings||[]).length;
  t.push('<span class="pill '+(ne?'bad':'ok')+'">errors <b>'+ne+'</b></span>');
  if(nw) t.push('<span class="pill warn">warnings <b>'+nw+'</b></span>');
  if(p.repairs && p.repairs.length)
    t.push('<span class="pill info">auto-repaired <b>'+p.repairs.length+'</b></span>');
  if(p.notes) t.push('<span class="pill">'+esc(p.notes)+'</span>');
  $('meta').innerHTML=t.join('');

  $('kitpill').innerHTML = p.kit===false
    ? 'no kit &mdash; plain file'
    : 'UI kit: lines 1&ndash;'+(p.kit_lines||0)+' <b>locked</b>';
  $('kitBtn').textContent = (p.kit===false) ? '+ Add UI kit' : '\u2212 Remove UI kit';
  useKit = (p.kit!==false);
}

function renderValidation(p){
  var rows=[], iss=p.issues||[];
  if(!p.syntax_ok){
    rows.push('<div class="issue"><div class="sev error">SYNTAX</div><div><div>'+
      esc(p.syntax_msg||'syntax error')+'</div></div></div>');
  }
  (p.repairs||[]).forEach(function(f){
    rows.push('<div class="issue"><div class="sev ok">FIXED</div><div><div>'+esc(f)+
      '</div><div class="fix">done locally &mdash; <b>0 tokens</b></div></div></div>');
  });
  iss.forEach(function(it){
    var sev=(it.sev||'info');
    rows.push('<div class="issue"><div class="sev '+sev+'">'+sev.toUpperCase()+
      '</div><div><div>'+esc(it.msg)+'</div>'+
      (it.fix?'<div class="fix">&#8627; '+esc(it.fix)+'</div>':'')+'</div></div>');
  });
  (p.build_warnings||[]).forEach(function(w){
    rows.push('<div class="issue"><div class="sev warn">BUILD</div><div><div>'+esc(w)+'</div></div></div>');
  });
  if(!rows.length){
    if(!(p.main_py||'').trim()){
      rows.push('<div class="empty"><div class="big">&#9634;</div>Empty file. Start typing, insert a starter, or add the UI kit.</div>');
    } else {
      rows.push('<div class="issue"><div class="sev ok">CLEAN</div><div><div>Syntax valid and no launch issues found.</div>'+
        '<div class="fix">Run <b>Self-test</b> to actually launch it, tap every button and soak it before you burn a build.</div></div></div>');
    }
  }
  $('validation').innerHTML=rows.join('');
}

function render(p, quiet){
  if(!p || !p.ok){ toast((p&&p.error)||'that failed','bad'); return; }
  cur=p;
  show('out'); hide('idle');
  $('f_name').value=p.name||'';
  $('f_title').value=p.title||'';
  $('f_orient').value=p.orientation||'portrait';
  $('f_perms').value=p.permissions||'';
  $('f_reqs').value=p.requirements||'python3,kivy';
  $('code').value=p.main_py||'';
  if(p.build_overrides){
    if(p.build_overrides.api) $('b_api').value=p.build_overrides.api;
    if(p.build_overrides.minapi) $('b_minapi').value=p.build_overrides.minapi;
  }
  metaTags(p); renderValidation(p); syncGutter(); refreshButtons(); updateUsage(p.usage);
  $('livelint').textContent='';
  if(p.repairs && p.repairs.length) toast(p.repairs.length+' issue(s) repaired for free','ok');
  if(!quiet) $('out').scrollIntoView({behavior:'smooth',block:'nearest'});
}

function collect(){
  if(!cur) cur={};
  cur.name=$('f_name').value.trim()||'app';
  cur.title=$('f_title').value.trim()||cur.name;
  cur.orientation=$('f_orient').value;
  cur.permissions=$('f_perms').value.trim();
  cur.requirements=$('f_reqs').value.trim()||'python3,kivy';
  cur.main_py=$('code').value;
  var archs=[];
  if($('arch_a64').checked) archs.push('arm64-v8a');
  if($('arch_a32').checked) archs.push('armeabi-v7a');
  cur.archs=archs.join(',')||'arm64-v8a';
  cur.build_overrides={api:$('b_api').value.trim(), minapi:$('b_minapi').value.trim(),
                       orientation:cur.orientation};
  return cur;
}

/* ---------------------------------------------------------------- usage meter */
function updateUsage(u){
  if(!u) return;
  var n=u.total||0;
  var txt=(n>=1000?(n/1000).toFixed(1)+'k':n)+' tok';
  var extra=[];
  if(u.calls) extra.push(u.calls+' calls');
  if(u.cached) extra.push(u.cached+' cached');
  $('tokpill').innerHTML='<span class="dot"></span><b>'+txt+'</b>'+
    (extra.length?' &middot; '+extra.join(' &middot; '):'');
  $('tokpill').title='session total: '+n+' tokens'+
    (u.saved?('  |  ~'+u.saved+' tokens saved by the cache'):'');
}
async function loadUsage(){
  try{ var r=await fetch('/api/usage'); var d=await r.json();
    updateUsage(d.usage);
    if(d.budget) $('tokpill').title += '  |  budget '+d.budget+', '+d.left+' left';
  }catch(e){}
}

/* ---------------------------------------------------------------- live lint */
function scheduleLint(){
  if(lintT) clearTimeout(lintT);
  $('livelint').textContent='checking...';
  lintT=setTimeout(runLint, 550);
}
async function runLint(){
  if(!cur) return;
  collect();
  try{
    var r=await fetch('/api/lint',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements,
                           permissions:cur.permissions})});
    var d=await r.json();
    cur.syntax_ok=d.syntax_ok; cur.syntax_msg=d.syntax_msg;
    cur.issues=d.issues; cur.errors=d.errors; cur.warnings=d.warnings;
    cur.repairs=[];
    metaTags(cur); renderValidation(cur);
    var ne=(d.errors||[]).length;
    $('livelint').innerHTML = !d.syntax_ok ? '<span style="color:var(--danger)">syntax error</span>'
      : (ne? '<span style="color:var(--danger)">'+ne+' blocking</span>'
           : '<span style="color:var(--green)">clean</span>');
  }catch(e){ $('livelint').textContent=''; }
}

/* ---------------------------------------------------------------- AI actions */
function refine(text){
  if(!cur || !(cur.main_py||'').trim()){
    $('desc').value=(($('desc').value||'')+' '+text).trim(); $('desc').focus(); return;
  }
  $('desc').value=text;
  forge();
}

async function forge(){
  var desc=$('desc').value.trim();
  if(!desc){ $('desc').focus(); return; }
  var done=busy('forgeBtn','forging');
  turns.push(desc);
  try{
    var r=await fetch('/api/forge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({description:desc,
        history:turns.slice(0,-1).map(function(t){return {role:'user',content:t};}),
        main_py:(cur&&cur.main_py)||''})});
    var d=await r.json();
    if(!r.ok){ toast(d.error||('forge failed ('+r.status+')'),'bad'); return; }
    render(d);
    toast('forged "'+(d.title||d.name)+'"','ok');
  }catch(e){ toast('network: '+e,'bad'); }
  finally{ done(); }
}

/* The whole pipeline: forge -> free repair -> static gate -> self-test -> fix -> repeat. */
async function autoForge(){
  var desc=$('desc').value.trim();
  if(!desc && !(cur&&cur.main_py)){ $('desc').focus(); return; }
  if(desc) turns.push(desc);
  var done=busy('autoBtn','running');
  show('agentwrap'); show('agentbar');
  $('rail').innerHTML=''; $('agentstat').textContent='starting';
  $('agentwrap').scrollIntoView({behavior:'smooth',block:'nearest'});
  try{
    var body={description:desc};
    if(!desc && cur){ collect(); body.main_py=cur.main_py; body.name=cur.name;
      body.title=cur.title; body.requirements=cur.requirements;
      body.permissions=cur.permissions; body.orientation=cur.orientation; }
    var r=await fetch('/api/autoforge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    var d=await r.json();
    if(!r.ok){ toast(d.error||'could not start','bad'); done(); hide('agentbar'); return; }
    pollJob(d.job_id, done);
  }catch(e){ toast('network: '+e,'bad'); done(); hide('agentbar'); }
}

var ICONS={ok:'\u2713', fail:'\u2717', warn:'!', step:'\u203A', info:'\u00B7'};
function pollJob(jid, done){
  if(jobT) clearInterval(jobT);
  var seen=0;
  jobT=setInterval(async function(){
    try{
      var r=await fetch('/api/job?id='+jid); var d=await r.json();
      var steps=d.steps||[];
      if(steps.length!==seen){
        $('rail').innerHTML=steps.map(function(s){
          return '<div class="stepr '+(s.kind||'info')+'"><div class="ic">'+
            (ICONS[s.kind]||'\u00B7')+'</div><div class="tx">'+esc(s.text)+'</div></div>';
        }).join('');
        $('rail').scrollTop=$('rail').scrollHeight;
        seen=steps.length;
      }
      $('agentstat').textContent = d.status==='running'
        ? ('round '+(d.round||1)) : d.status;
      updateUsage(d.usage);
      if(d.phases && d.phases.length) renderPhases(d.phases);
      if(d.status!=='running'){
        clearInterval(jobT); jobT=null; hide('agentbar');
        if(d.payload) render(d.payload);
        if(d.status==='done') toast('verified \u2014 self-test passed, ready to build','ok');
        else if(d.status==='stalled') toast('stopped early to save tokens \u2014 see the rail','warn');
        else toast(d.error||'the run failed','bad');
        done();
      }
    }catch(e){ clearInterval(jobT); jobT=null; hide('agentbar'); done(); }
  }, 900);
}

async function autoFix(){
  if(!cur) return; collect();
  var done=busy('fixBtn','fixing');
  try{
    var err=(cur.test_error||'')||
      (cur.issues||[]).filter(function(i){return i.sev==='error'||i.sev==='warn';})
        .map(function(i){return '- '+i.msg;}).join('\n');
    var r=await fetch('/api/fix',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements,
                           permissions:cur.permissions, error:err})});
    var d=await r.json();
    if(!r.ok){ toast(d.error||'fix failed','bad'); return; }
    d.name=cur.name||d.name; d.title=cur.title||d.title;
    render(d); toast('fix applied \u2014 re-run the self-test','ok');
  }catch(e){ toast('network: '+e,'bad'); }
  finally{ done(); }
}

async function localRepair(){
  if(!cur) return; collect();
  var done=busy('repairBtn','repairing');
  try{
    var r=await fetch('/api/repair',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(cur)});
    var d=await r.json();
    if(!r.ok){ toast(d.error||'repair failed','bad'); return; }
    render(d);
    if(!(d.repairs||[]).length) toast('nothing a local pass could fix','warn');
  }catch(e){ toast('network: '+e,'bad'); }
  finally{ done(); }
}

async function polish(){
  if(!cur) return; collect();
  var done=busy('polishBtn','polishing');
  try{
    var r=await fetch('/api/polish',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements,
                           permissions:cur.permissions})});
    var d=await r.json();
    if(!r.ok){ toast(d.error||'polish failed','bad'); return; }
    d.name=cur.name||d.name; d.title=cur.title||d.title;
    render(d); toast('restyled','ok');
  }catch(e){ toast('network: '+e,'bad'); }
  finally{ done(); }
}

/* ---------------------------------------------------------------- self-test */
function renderPhases(ph){
  $('phases').innerHTML=(ph||[]).map(function(p){
    return '<span class="ph '+(p.ok?'ok':'bad')+'" title="'+esc(p.detail||'')+'">'+
      (p.ok?'\u2713':'\u2717')+' '+esc(p.name)+'</span>';
  }).join('');
}

async function testRun(){
  if(!cur) return; collect();
  if(!(cur.main_py||'').trim()){ toast('nothing to test yet','warn'); return; }
  show('testpanel'); $('testout').textContent='starting self-test...'; $('phases').innerHTML='';
  var done=busy('testBtn','testing');
  try{
    var r=await fetch('/api/testrun',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements})});
    var d=await r.json();
    if(!r.ok){ $('testout').textContent=d.error||'test failed'; toast(d.error||'test failed','bad');
      done(); return; }
    pollTest(d.test_id, done);
  }catch(e){ $('testout').textContent='network: '+e; done(); }
}

function pollTest(tid, done){
  if(testT) clearInterval(testT);
  testT=setInterval(async function(){
    try{
      var r=await fetch('/api/testlog?id='+tid); var d=await r.json();
      $('testout').textContent=(d.log||'');
      $('testout').scrollTop=$('testout').scrollHeight;
      if(d.phases && d.phases.length) renderPhases(d.phases);
      if(d.status && d.status!=='running'){
        clearInterval(testT); testT=null;
        if(cur) cur.test_error=d.error_text||'';
        if(d.status==='pass') toast('self-test passed \u2014 safe to build','ok');
        else if(d.status==='fail') toast('self-test failed \u2014 hit Auto-fix to send the traceback back','bad');
        else toast(d.summary||d.status,'warn');
        done();
      }
    }catch(e){ clearInterval(testT); testT=null; done(); }
  }, 700);
}

/* ---------------------------------------------------------------- preview */
async function loadDevices(){
  try{
    var r=await fetch('/api/devices'); var d=await r.json();
    var sel=$('previewDevice'); if(!sel) return;
    sel.innerHTML='';
    (d.devices||[]).forEach(function(dev){
      var o=document.createElement('option'); o.value=dev.id; o.textContent=dev.label;
      if(dev.id===d.default) o.selected=true; sel.appendChild(o);
    });
    if(!d.available){
      var pb=$('previewBtn');
      if(pb){ pb.disabled=true; pb.title='install desktop Kivy to preview: pip install --user kivy'; }
    }
  }catch(e){}
}
async function previewApp(){
  if(!cur) return; collect();
  if(!(cur.main_py||'').trim()){ toast('nothing to preview yet','warn'); return; }
  var dev=($('previewDevice')||{}).value||'';
  var done=busy('previewBtn','opening');
  try{
    var r=await fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, device:dev})});
    var d=await r.json();
    if(!r.ok){ toast(d.error||'preview failed','bad'); }
    else{ toast('opening preview window…','ok'); }
  }catch(e){ toast('network: '+e,'bad'); }
  done();
}

/* ---------------------------------------------------------------- build */
async function buildApk(){
  if(!cur) return; collect();
  if(!(cur.main_py||'').trim()){ toast('nothing to build yet','warn'); return; }
  if(cur.errors && cur.errors.length){
    if(!confirm(cur.errors.length+' blocking error(s) \u2014 the server will refuse this build.\n\n'+
      cur.errors.slice(0,4).join('\n')+'\n\nTry anyway?')) return;
  }
  var done=busy('buildBtn','starting');
  hide('apkBtn');
  try{
    var r=await fetch('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(cur)});
    var d=await r.json();
    if(!r.ok){ toast(d.error||'build refused','bad'); done(); return; }
    show('logwrap'); $('log').textContent='build started ('+d.build_id+')...\n';
    $('logwrap').scrollIntoView({behavior:'smooth',block:'nearest'});
    pollBuild(d.build_id, done);
  }catch(e){ toast('network: '+e,'bad'); done(); }
}

function pollBuild(bid, done){
  if(pollT) clearInterval(pollT);
  pollT=setInterval(async function(){
    try{
      var r=await fetch('/api/log?id='+bid); var d=await r.json();
      $('log').textContent=d.log||'';
      $('log').scrollTop=$('log').scrollHeight;
      if(d.status==='done' || d.status==='failed'){
        clearInterval(pollT); pollT=null; done();
        if(d.status==='done' && d.apk){ window._apkId=bid; show('apkBtn');
          toast('APK ready','ok'); }
        else toast('build failed \u2014 check the log','bad');
      }
    }catch(e){ clearInterval(pollT); pollT=null; done(); }
  }, 1500);
}

function downloadApk(){ if(window._apkId) window.location='/api/apk?id='+window._apkId; }

async function downloadProject(){
  if(!cur) return; collect();
  try{
    var r=await fetch('/api/project_zip',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(cur)});
    if(!r.ok){ var e=await r.json(); toast('zip error: '+(e.error||r.status),'bad'); return; }
    var blob=await r.blob();
    var a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=(cur.name||'app')+'_buildozer.zip';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href);},1000);
    toast('project downloaded','ok');
  }catch(e){ toast('network: '+e,'bad'); }
}

/* ---------------------------------------------------------------- templates */
async function loadTemplate(force){
  var id=force||$('tpl_sel').value;
  try{
    var r=await fetch('/api/template?id='+encodeURIComponent(id));
    var d=await r.json();
    if(!r.ok){ toast(d.error||'template failed','bad'); return; }
    turns=[]; render(d);
  }catch(e){ toast('network: '+e,'bad'); }
}

async function loadTemplates(){
  try{
    var r=await fetch('/api/templates'); var d=await r.json();
    var sel=$('tpl_sel'); sel.innerHTML='';
    (d.templates||[]).forEach(function(t){
      var o=document.createElement('option');
      o.value=t.id; o.textContent=t.label+' \u2014 '+t.desc;
      sel.appendChild(o);
    });
    sel.value='kit';
  }catch(e){}
}

function refreshButtons(){
  var has=!!cur, hasCode=has && (($('code').value||'').trim().length>0);
  ['buildBtn','testBtn','fixBtn','polishBtn','zipBtn','repairBtn'].forEach(function(id){
    var b=$(id); if(b && !b.querySelector('.spin')) b.disabled=!hasCode;
  });
}

/* ---------------------------------------------------------------- doctor */
function toggleDoctor(){ $('doctor').classList.toggle('open'); }
async function loadDoctor(){
  try{
    var r=await fetch('/api/doctor'); var d=await r.json();
    var checks=d.checks||[];
    $('doctor').innerHTML=checks.map(function(c){
      return '<span class="pill '+(c[1]?'ok':'bad')+'"><span class="dot"></span>'+esc(c[0])+'</span>';
    }).join('')||'<span class="pill">no checks</span>';
    var okN=checks.filter(function(c){return c[1];}).length;
    var bad=checks.length-okN;
    var sum=$('docsum');
    sum.className='pill '+(bad?'warn':'ok');
    sum.innerHTML='<span class="dot"></span>environment <b>'+okN+'/'+checks.length+'</b>'+
      (bad?' \u25BE':' \u25BE');
    sum.title=bad? bad+' check(s) need attention \u2014 click for detail'
                 : 'everything the forge needs is installed';
  }catch(e){ $('docsum').innerHTML='<span class="dot"></span>doctor failed';
             $('docsum').className='pill bad'; }
}

/* ---------------------------------------------------------------- settings */
function onModelChange(){
  var sel=$('sf_model_sel'), c=$('sf_model_custom');
  if(sel.value==='__custom__'){ c.classList.remove('hidden'); c.focus(); }
  else c.classList.add('hidden');
}
async function openSettings(){
  try{
    var r=await fetch('/api/config'); var d=await r.json();
    var sel=$('sf_model_sel'), c=$('sf_model_custom');
    var m=d.sf_model||'', found=false;
    for(var i=0;i<sel.options.length;i++){ if(sel.options[i].value===m){found=true;break;} }
    if(found){ sel.value=m; c.classList.add('hidden'); c.value=''; }
    else { sel.value='__custom__'; c.classList.remove('hidden'); c.value=m; }
    if(d.sf_url) $('sf_url').value=d.sf_url;
    if(d.groq_model) $('groq_model').value=d.groq_model;
    if(d.groq_url) $('groq_url').value=d.groq_url;
    $('max_tokens').value=d.max_tokens; $('token_budget').value=d.token_budget;
    $('agent_rounds').value=d.agent_rounds;
    $('cache').checked=!!d.cache; $('auto_repair').checked=!!d.auto_repair;
    $('sf_key').value=''; $('groq_key').value='';
    $('clear_sf').checked=false; $('clear_groq').checked=false;
    $('sfset').textContent=d.sf_key_set?'(stored)':(d.sf_env?'(from env)':'(not set)');
    $('gqset').textContent=d.groq_key_set?'(stored)':(d.groq_env?'(from env)':'(not set)');
    $('setmsg').textContent='';
    var u=await (await fetch('/api/usage')).json();
    $('usagenote').textContent='this session: '+(u.usage.total||0)+' tokens over '+
      (u.usage.calls||0)+' calls'+(u.usage.cached?(', '+u.usage.cached+' served from cache'):'');
  }catch(e){}
  show('settings');
}
function closeSettings(){ hide('settings'); }
function overlayClick(e){ if(e.target && e.target.id==='settings') closeSettings(); }

async function testKey(){
  await saveSettings(true);
  var done=busy('keytestBtn','testing');
  $('setmsg').textContent='testing key via curl + Python...';
  try{
    var r=await fetch('/api/keytest',{method:'POST'});
    var d=await r.json();
    var di=d.diag||{};
    var lines=[];
    lines.push(d.ok?'\u2713 '+d.detail:'\u2717 '+d.detail);
    if(di.url) lines.push('URL: '+di.url);
    if(di.model) lines.push('Model: '+di.model);
    if(di.key_masked) lines.push('Key: '+di.key_masked+' ('+di.key_len+' chars, starts with '+di.key_prefix+')');
    if(di.curl_http) lines.push('curl HTTP: '+di.curl_http);
    if(di.py_http) lines.push('Python HTTP: '+di.py_http);
    if(!d.ok && di.curl_body) lines.push('Server said: '+di.curl_body.slice(0,200));
    if(!d.ok && di.py_body && !di.curl_body) lines.push('Server said: '+di.py_body.slice(0,200));
    $('setmsg').innerHTML='<pre style="white-space:pre-wrap;font-size:12px;color:'+(d.ok?'var(--green)':'var(--danger)')
      +';margin:8px 0 0;max-height:200px;overflow:auto">'+esc(lines.join('\n'))+'</pre>';
    toast(d.ok?'key works':'key test failed -- see details below', d.ok?'ok':'bad');
    loadDoctor();
  }catch(e){ $('setmsg').textContent='error: '+e; }
  finally{ done(); }
}

async function saveSettings(silent){
  var sel=$('sf_model_sel');
  var model=(sel.value==='__custom__')?$('sf_model_custom').value.trim():sel.value;
  var body={sf_key:$('sf_key').value, groq_key:$('groq_key').value, sf_model:model,
    sf_url:$('sf_url').value, groq_model:$('groq_model').value, groq_url:$('groq_url').value,
    clear_sf:$('clear_sf').checked, clear_groq:$('clear_groq').checked,
    max_tokens:parseInt($('max_tokens').value||'12000',10),
    token_budget:parseInt($('token_budget').value||'0',10),
    agent_rounds:parseInt($('agent_rounds').value||'3',10),
    cache:$('cache').checked, auto_repair:$('auto_repair').checked};
  try{
    var r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    var d=await r.json();
    $('setmsg').textContent=d.saved?'saved':'save failed (check ~/.androdawg perms)';
    loadDoctor();
    if(d.saved && !silent){ toast('settings saved','ok'); setTimeout(closeSettings,450); }
  }catch(e){ $('setmsg').textContent='error: '+e; }
}

async function clearCache(){
  try{ var r=await fetch('/api/cache_clear',{method:'POST'}); var d=await r.json();
    toast('cleared '+d.removed+' cached response(s)','ok'); }
  catch(e){ toast('network: '+e,'bad'); }
}

async function quitApp(){
  try{ await fetch('/api/quit',{method:'POST'}); }catch(e){}
  document.body.innerHTML='<div style="color:#546274;font-family:system-ui;padding:56px;'+
    'font-size:15px">The Dawg stopped. You can close this window.</div>';
  setTimeout(function(){ try{window.close();}catch(e){} },400);
}

/* ---------------------------------------------------------------- wiring */
$('desc').addEventListener('keydown', function(e){
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter') forge(); });
$('code').addEventListener('input', function(){ syncGutter(); scheduleLint(); refreshButtons(); });
$('code').addEventListener('scroll', function(){ $('gutter').scrollTop=this.scrollTop; });
$('code').addEventListener('keydown', function(e){
  if(e.key==='Tab'){                       // a code editor that eats Tab is not an editor
    e.preventDefault();
    var s=this.selectionStart, en=this.selectionEnd;
    this.value=this.value.slice(0,s)+'    '+this.value.slice(en);
    this.selectionStart=this.selectionEnd=s+4;
    syncGutter();
  }
});
document.addEventListener('click', function(e){
  if(!e.target.closest('.docwrap')) $('doctor').classList.remove('open'); });
document.addEventListener('keydown', function(e){
  if(e.key==='Escape'){ closeSettings(); $('doctor').classList.remove('open'); }
  if((e.ctrlKey||e.metaKey)&&e.key==='s'){ e.preventDefault(); if(cur) testRun(); }
});

loadDoctor(); loadTemplates(); loadUsage(); loadDevices(); refreshButtons();
setInterval(loadUsage, 15000);
</script>
</body>
</html>
"""


# ----------------------------------------------------------------- main
_BRAND_ICON_CACHE = None


def _brand_icon_bytes():
    """PNG bytes for the app-window / panel icon.

    Prefer a brand icon.png shipped next to the script (so the window icon, the
    taskbar icon and the .desktop icon all match). Fall back to a generated
    deterministic mark so the window never shows a generic browser globe.
    """
    global _BRAND_ICON_CACHE
    if _BRAND_ICON_CACHE is not None:
        return _BRAND_ICON_CACHE
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "icon.png"),
        os.path.join(WORKDIR, "icon.png"),
        os.path.expanduser("~/.local/share/androdawg/icon.png"),
    ]
    for p in candidates:
        try:
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    data = f.read()
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    _BRAND_ICON_CACHE = data
                    return data
        except Exception:
            pass
    try:
        _BRAND_ICON_CACHE = icon_png("the dawg apk forge", 256)
    except Exception:
        # 1x1 transparent PNG as an absolute last resort
        _BRAND_ICON_CACHE = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc````"
            b"\x00\x00\x00\x05\x00\x01\xa5\xf6E@\x00\x00\x00\x00IEND\xaeB`\x82"
        )
    return _BRAND_ICON_CACHE


def _write_window_icon():
    """Drop a PNG icon into the app-window profile dir and return its path.

    Chromium/Brave --app windows in KDE/GNOME will pick this up for the window
    so it shows the dawg, not a browser globe.
    """
    try:
        prof = os.path.join(WORKDIR, "appwindow")
        os.makedirs(prof, exist_ok=True)
        ipath = os.path.join(prof, "androdawg.png")
        with open(ipath, "wb") as f:
            f.write(_brand_icon_bytes())
        return ipath
    except Exception:
        return None


def launch_app_window(url):
    """Open the UI in its own frameless window (Chromium/Brave --app), not a tab.

    We force a dedicated WM class (--class=AndroDawg) so the desktop groups the
    window under its OWN panel/taskbar entry with its own icon and name instead
    of lumping it in with the browser. Paired with a .desktop file that sets
    StartupWMClass=AndroDawg (written by install.sh), KDE/GNOME show the dawg
    icon in the panel rather than a Brave tab.
    """
    forced = os.environ.get("DAWG_BROWSER", "").strip()
    candidates = ["brave-browser", "brave", "chromium", "chromium-browser",
                  "google-chrome", "google-chrome-stable", "microsoft-edge", "vivaldi"]
    if forced:
        candidates = [forced] + candidates
    profile = os.path.join(WORKDIR, "appwindow")
    icon_path = _write_window_icon()
    wmclass = "AndroDawg"
    for name in candidates:
        exe = shutil.which(name)
        if not exe:
            continue
        cmd = [
            exe,
            "--app=" + url,
            "--user-data-dir=" + profile,
            "--class=" + wmclass,           # X11: sets WM_CLASS -> own panel entry
            "--name=" + wmclass,            # some WMs read --name for the instance
            "--window-size=1100,840",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        # Chromium honours --window-name for the WM_NAME on some builds; harmless if not.
        try:
            env = dict(os.environ)
            if icon_path:
                # Hint for portals/launchers that map by desktop file id.
                env.setdefault("CHROME_DESKTOP", "androdawg.desktop")
            subprocess.Popen(
                cmd, env=env,
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
    """Return the version of a running instance at url, or None if none/not-ours."""
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
    # Pin a Gradle-compatible JDK for the build. Buildozer's bundled Gradle runs on
    # JDK 17-24 but dies on 25+ (class file major 69) -- and 25+ is now the default on
    # both Kali and up-to-date Arch/CachyOS (jdk-openjdk). If the active java is already
    # in range we leave it; otherwise we hunt for one and point JAVA_HOME at it. 17 is
    # preferred; 18-24 are accepted fallbacks. If none exists, doctor + preflight flag it.
    _jver, _jmaj = java_version()
    if _jmaj is None or not (GRADLE_JDK_MIN <= _jmaj <= GRADLE_JDK_MAX):
        _patterns = (
            "/usr/lib/jvm/java-17-openjdk*", "/usr/lib/jvm/temurin-17-jdk*",
            "/usr/lib/jvm/*-17-*", "/usr/lib/jvm/*17*",           # 17 first (safest)
            "/usr/lib/jvm/java-1[89]-openjdk*", "/usr/lib/jvm/java-2[0-4]-openjdk*",
            "/usr/lib/jvm/temurin-1[89]-jdk*", "/usr/lib/jvm/temurin-2[0-4]-jdk*",
            "/usr/lib/jvm/*-1[89]-*", "/usr/lib/jvm/*-2[0-4]-*",  # then 18-24
        )
        for pat in _patterns:
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
    bound = PORT
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
