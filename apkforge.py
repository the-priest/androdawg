#!/usr/bin/env python3
"""
THE DAWG // APK FORGE  v2
Describe an Android app (or write it yourself) -> forge a polished, single-file Kivy
app -> static-check it -> RUN it headless to catch launch crashes BEFORE the 40-min
build -> auto-fix issues in a loop -> compile a real .apk with Buildozer.

Stdlib-only server + browser UI. SiliconFlow/DeepSeek-V4-Flash primary, Groq fallback.
Keys are set in the in-app Settings (gear) or via env (SILICONFLOW_API_KEY / GROQ_API_KEY).

What's new in v2:
  - MANUAL mode: hand-write main.py + spec, same validate/test/build pipeline.
  - A built-in pro UI kit is prepended to forged apps so they don't look default-grey.
  - Auto-generated gradient launcher icon + matching splash (kills the white-launch flash).
  - The AI may set a whitelisted, value-validated build config (can't brick a build).
  - Headless "TEST RUN" executes the app under Xvfb and reports real tracebacks.
  - AUTO-FIX feeds errors back to the model; POLISH restyles with the kit.
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

VERSION = "2.0"

WORKDIR = os.path.expanduser("~/AndroDawg")
PROJECTS = os.path.join(WORKDIR, "projects")
TESTDIR = os.path.join(WORKDIR, "testruns")

# arm64 only -> covers every modern phone (incl. ROG Phone 5S / SD888+) and halves
# build time. add ,armeabi-v7a here if you ever need to support 32-bit hardware.
ANDROID_ARCHS = "arm64-v8a"

BUILDS = {}   # build_id -> {"log":[...], "status": "...", "apk": path|None}
TESTS = {}    # test_id  -> {"log":[...], "status": "...", "summary": str}

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
KIT = '# ===== DAWG UI KIT (pure Kivy, no external deps) =====\n# A small, battle-tested component kit that makes generated apps look modern\n# instead of default-Kivy grey. Pure kivy + stdlib only -> always builds on p4a.\nimport hashlib\nfrom kivy.metrics import dp, sp\nfrom kivy.animation import Animation\nfrom kivy.clock import Clock\nfrom kivy.properties import ListProperty\nfrom kivy.graphics import Color, RoundedRectangle, Rectangle, Line\nfrom kivy.uix.widget import Widget\nfrom kivy.uix.label import Label\nfrom kivy.uix.button import Button\nfrom kivy.uix.boxlayout import BoxLayout\nfrom kivy.uix.floatlayout import FloatLayout\nfrom kivy.uix.textinput import TextInput\nfrom kivy.core.window import Window\n\n\ndef _hx(h):\n    h = h.lstrip("#")\n    if len(h) == 6:\n        h += "ff"\n    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4, 6))\n\n\ndef _mix(a, b, t):\n    return tuple(a[i] * (1 - t) + b[i] * t for i in range(4))\n\n\nclass Theme:\n    """Central palette. accent is derived from a seed so each app feels distinct."""\n    bg        = _hx("#0c0f14")\n    bg2       = _hx("#11161f")\n    surface   = _hx("#161d29")\n    surface2  = _hx("#1d2736")\n    line      = _hx("#27303f")\n    text      = _hx("#eaf0f7")\n    muted     = _hx("#8b97a8")\n    primary   = _hx("#4f7cff")\n    primary_d = _hx("#3b63e0")\n    accent    = _hx("#27e0b0")\n    danger    = _hx("#ff5d6c")\n    ok        = _hx("#37d98a")\n    warn      = _hx("#ffba49")\n    on_primary = _hx("#ffffff")\n    radius    = dp(16)\n    pad       = dp(18)\n    gap       = dp(12)\n\n    @classmethod\n    def seed(cls, name):\n        """Tint the accent/primary from an app name so identity is consistent."""\n        if not name:\n            return\n        hue = int(hashlib.sha256(name.encode()).hexdigest(), 16) % 360\n        cls.primary = cls._hsl(hue, 0.78, 0.62)\n        cls.primary_d = cls._hsl(hue, 0.78, 0.50)\n        cls.accent = cls._hsl((hue + 150) % 360, 0.70, 0.58)\n\n    @staticmethod\n    def _hsl(h, s, l):\n        import colorsys\n        r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)\n        return (r, g, b, 1)\n\n\nclass GradientBackground(FloatLayout):\n    """Full-bleed vertical gradient drawn as strips (no texture flip surprises)."""\n    def __init__(self, top=None, bottom=None, strips=48, **kw):\n        super().__init__(**kw)\n        self._top = top or Theme.bg\n        self._bottom = bottom or Theme.bg2\n        self._strips = strips\n        self.bind(pos=self._redraw, size=self._redraw)\n        self._redraw()\n\n    def _redraw(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            n = self._strips\n            for i in range(n):\n                Color(*_mix(self._top, self._bottom, i / (n - 1)))\n                Rectangle(pos=(self.x, self.y + self.height * (1 - (i + 1) / n)),\n                          size=(self.width, self.height / n + 1))\n\n\nclass _Rounded:\n    """Mixin: paints a rounded background + optional border into canvas.before."""\n    def _paint(self, fill, radius=None, border=None, bw=1.2):\n        self._fill = fill\n        self._radius = radius if radius is not None else Theme.radius\n        self._border = border\n        self._bw = bw\n        self.bind(pos=self._rp, size=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*self._fill)\n            self._rr = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])\n            if self._border:\n                Color(*self._border)\n                Line(rounded_rectangle=(self.x, self.y, self.width, self.height,\n                                        self._radius), width=self._bw)\n\n\nclass Card(BoxLayout, _Rounded):\n    """A rounded surface panel with a faint border + soft drop shadow."""\n    def __init__(self, fill=None, radius=None, padding=None, **kw):\n        kw.setdefault("orientation", "vertical")\n        kw.setdefault("padding", padding if padding is not None else Theme.pad)\n        kw.setdefault("spacing", Theme.gap)\n        super().__init__(**kw)\n        self._paint(fill or Theme.surface, radius, border=Theme.line)\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            # soft shadow: two translucent offset rects\n            Color(0, 0, 0, 0.22)\n            RoundedRectangle(pos=(self.x, self.y - dp(3)),\n                             size=(self.width, self.height), radius=[self._radius])\n            Color(*self._fill)\n            RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])\n            if self._border:\n                Color(*self._border)\n                Line(rounded_rectangle=(self.x, self.y, self.width, self.height,\n                                        self._radius), width=self._bw)\n\n\nclass AppBar(BoxLayout, _Rounded):\n    """Top title bar. Use as the first child of your root."""\n    def __init__(self, title="App", subtitle="", **kw):\n        kw.setdefault("orientation", "vertical")\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(64) if not subtitle else dp(78))\n        kw.setdefault("padding", (Theme.pad, dp(8)))\n        super().__init__(**kw)\n        self._paint(Theme.surface, radius=0, border=None)\n        t = Label(text=title, font_size=sp(20), bold=True, color=Theme.text,\n                  halign="left", valign="middle", shorten=True)\n        t.bind(size=lambda w, *a: setattr(w, "text_size", w.size))\n        self.add_widget(t)\n        if subtitle:\n            s = Label(text=subtitle, font_size=sp(12), color=Theme.muted,\n                      halign="left", valign="middle")\n            s.bind(size=lambda w, *a: setattr(w, "text_size", w.size))\n            self.add_widget(s)\n\n\nclass PillButton(Button):\n    """Rounded, animated, theme-coloured button. variant: \'primary\'|\'ghost\'|\'danger\'."""\n    cur = ListProperty([0, 0, 0, 0])  # animated fill colour\n\n    def __init__(self, text="", variant="primary", radius=None, **kw):\n        kw.setdefault("font_size", sp(16))\n        kw.setdefault("bold", True)\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(52))\n        super().__init__(text=text, **kw)\n        self.background_normal = ""\n        self.background_down = ""\n        self.background_color = (0, 0, 0, 0)\n        self._radius = radius if radius is not None else dp(14)\n        self._variant = variant\n        self._set_colors()\n        self.bind(pos=self._rp, size=self._rp, cur=self._rp,\n                  on_press=self._down, on_release=self._up)\n        self._rp()\n\n    def _set_colors(self):\n        if self._variant == "ghost":\n            self._base = (0, 0, 0, 0); self._edge = Theme.line; self.color = Theme.text\n        elif self._variant == "danger":\n            self._base = Theme.danger; self._edge = None; self.color = (1, 1, 1, 1)\n        else:\n            self._base = Theme.primary; self._edge = None; self.color = Theme.on_primary\n        self.cur = list(self._base)\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*self.cur)\n            RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius])\n            if self._edge:\n                Color(*self._edge)\n                Line(rounded_rectangle=(self.x, self.y, self.width, self.height,\n                                        self._radius), width=1.3)\n\n    def _down(self, *a):\n        target = _mix(self._base, (1, 1, 1, 1), 0.18) if self._variant != "ghost" \\\n            else (1, 1, 1, 0.08)\n        Animation.cancel_all(self, "cur")\n        Animation(cur=list(target), d=0.06).start(self)\n\n    def _up(self, *a):\n        Animation.cancel_all(self, "cur")\n        Animation(cur=list(self._base), d=0.12).start(self)\n\n\nclass IconButton(Button):\n    """Circular icon/text button."""\n    def __init__(self, text="+", diameter=dp(48), variant="primary", **kw):\n        kw.setdefault("font_size", sp(20))\n        kw.setdefault("bold", True)\n        kw.setdefault("size_hint", (None, None))\n        kw.setdefault("size", (diameter, diameter))\n        super().__init__(text=text, **kw)\n        self.background_normal = ""; self.background_down = ""\n        self.background_color = (0, 0, 0, 0)\n        self._variant = variant\n        self.color = Theme.on_primary if variant == "primary" else Theme.text\n        self.bind(pos=self._rp, size=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        d = min(self.width, self.height)\n        with self.canvas.before:\n            Color(*(Theme.primary if self._variant == "primary" else Theme.surface2))\n            RoundedRectangle(pos=self.pos, size=(d, d), radius=[d / 2.0])\n\n\nclass TextField(TextInput):\n    """Rounded, padded, theme-coloured single/multi-line input."""\n    def __init__(self, hint="", **kw):\n        kw.setdefault("multiline", False)\n        kw.setdefault("font_size", sp(16))\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(50))\n        kw.setdefault("padding", (dp(14), dp(13)))\n        super().__init__(**kw)\n        self.background_normal = ""; self.background_active = ""\n        self.background_color = (0, 0, 0, 0)\n        self.foreground_color = Theme.text\n        self.cursor_color = Theme.primary\n        self.hint_text = hint\n        self.hint_text_color = Theme.muted\n        self.bind(pos=self._rp, size=self._rp, focus=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*Theme.surface2)\n            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])\n            Color(*(Theme.primary if self.focus else Theme.line))\n            Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(12)),\n                 width=1.4 if self.focus else 1.1)\n\n\nclass Divider(Widget):\n    def __init__(self, **kw):\n        kw.setdefault("size_hint_y", None)\n        kw.setdefault("height", dp(1))\n        super().__init__(**kw)\n        self.bind(pos=self._rp, size=self._rp)\n        self._rp()\n\n    def _rp(self, *a):\n        self.canvas.before.clear()\n        with self.canvas.before:\n            Color(*Theme.line)\n            Rectangle(pos=self.pos, size=self.size)\n\n\ndef heading(text, size=24):\n    l = Label(text=text, font_size=sp(size), bold=True, color=Theme.text,\n              size_hint_y=None, halign="left", valign="middle")\n    l.bind(width=lambda w, *a: setattr(w, "text_size", (w.width, None)),\n           texture_size=lambda w, *a: setattr(w, "height", w.texture_size[1] + dp(6)))\n    return l\n\n\ndef body(text, muted=True, size=14):\n    l = Label(text=text, font_size=sp(size),\n              color=Theme.muted if muted else Theme.text,\n              size_hint_y=None, halign="left", valign="top")\n    l.bind(width=lambda w, *a: setattr(w, "text_size", (w.width, None)),\n           texture_size=lambda w, *a: setattr(w, "height", w.texture_size[1]))\n    return l\n\n\ndef toast(message, duration=1.6):\n    """Floating, auto-dismissing message at the bottom of the window."""\n    lbl = Label(text=message, color=Theme.text, font_size=sp(14),\n                size_hint=(None, None), padding=(dp(16), dp(10)))\n    lbl.texture_update()\n    lbl.size = (lbl.texture_size[0] + dp(32), lbl.texture_size[1] + dp(20))\n    with lbl.canvas.before:\n        Color(*Theme.surface2)\n        r = RoundedRectangle(radius=[dp(12)])\n    def _sync(*a):\n        r.pos = lbl.pos; r.size = lbl.size\n    lbl.bind(pos=_sync, size=_sync)\n    lbl.pos = ((Window.width - lbl.width) / 2, dp(60))\n    Window.add_widget(lbl)\n    def _gone(*a):\n        try:\n            Window.remove_widget(lbl)\n        except Exception:\n            pass\n    Clock.schedule_once(_gone, duration)\n# ===== END DAWG UI KIT =====\n'


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
    _, _, _, bg, _ = _palette(name)
    return "#%02x%02x%02x" % bg


def write_assets(project_dir, name):
    """Generate icon.png + presplash.png into project_dir. Returns (icon_ok, splash_ok)."""
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
SYSTEM_PROMPT = """You are The Dawg (APK edition), an elite Android app smith. The user describes an app; you forge a COMPLETE, runnable, single-file Kivy app that gets cross-compiled to an .apk with Buildozer / python-for-android and must look like a polished Google-Play app and launch first try on a modern arm64 phone.

A POLISHED UI KIT IS ALREADY DEFINED ABOVE YOUR CODE. Do NOT paste it, do NOT redefine it, do NOT import it -- these names are already in the module namespace and you call them directly:
- Theme            : dark palette. Theme.BG, Theme.SURFACE, Theme.TXT, Theme.MUTED, Theme.ACCENT, Theme.ACCENT2, Theme.GOOD, Theme.WARN, Theme.BAD (each is an rgba list). Call Theme.seed("Your App Name") ONCE at startup to derive a unique accent from the name.
- GradientBackground(**kw) : a FloatLayout that paints a vertical gradient. Use it as your root and add children on top.
- AppBar(title="", subtitle="")          : top bar.
- Card(**kw)        : rounded raised surface (a BoxLayout); set padding/spacing/orientation as usual, add children.
- PillButton(text="", variant="primary") : rounded button, variants "primary" | "ghost" | "danger". bind on_release.
- IconButton(glyph="+")  : round compact button (glyph is a short unicode char).
- TextField(hint="")     : rounded text input; read .text.
- Divider()         : thin separator line.
- heading(text)     : big bold Label (returns a Label).
- body(text)        : normal Label.
- toast(text)       : transient on-screen message; call from anywhere.

HARD RULES
- Output a single self-contained app. No placeholders, no TODO, no "...". Real working code top to bottom.
- Kivy ONLY. NEVER tkinter / PyQt / PySide / GTK(gi) / wx / curses / pygame -- none of them survive python-for-android.
- Subclass App. Build your UI in build() returning a GradientBackground root with an AppBar + Card(s). Make it genuinely nice: clear hierarchy, generous spacing (dp), big touch targets (>= 48dp), obvious feedback on every tap. No dead grey default widgets.
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

POLISH_PROMPT = """You are The Dawg (APK edition), a senior Android UI engineer. You are handed a working single-file Kivy app and must make it look like a premium Google-Play app WITHOUT changing what it does or breaking it.

The file already contains the DAWG UI KIT between its markers (Theme, GradientBackground, AppBar, Card, PillButton, IconButton, TextField, Divider, heading, body, toast). DO NOT modify anything between the kit markers. Leave the kit byte-for-byte intact. Only restyle the APP code below the kit.

Make these improvements:
- Replace bare/default widgets with kit components (GradientBackground root, AppBar, Cards, PillButtons).
- Apply Theme colors; call Theme.seed(<app name>) at startup if not already.
- Tighten layout: consistent dp spacing/padding, clear visual hierarchy, big touch targets, satisfying feedback on tap.
- Keep it Kivy-only, keep all android imports guarded by platform, keep user_data_dir for saves, add no new external assets.
- Do NOT change requirements/permissions unless the restyle truly needs it.

Output EXACTLY the same section format you were given (<<<NAME>>> ... <<<END>>>) with the FULL updated file in <<<MAIN_PY>>> (kit included, unchanged). No prose, no fences."""

FIX_PROMPT = """You are The Dawg (APK edition), a Kivy/Android debugging expert. You are given a single-file Kivy app and an error it produced (a syntax error, a Python traceback from a desktop test run, or a static-analysis finding). Fix the ROOT CAUSE so the app launches cleanly on Android and on desktop.

The file contains the DAWG UI KIT between its markers. Keep the kit intact; fix the APP code (or wiring) below it. Common Android launch killers to check and fix:
- Unguarded android-only imports (must be behind `if platform == "android":`).
- A third-party import not listed in requirements (add it ONLY if it's in the allowed recipe set: pillow, requests, certifi, urllib3, idna, plyer, numpy; otherwise reimplement with stdlib/Kivy).
- File writes to a relative path / cwd instead of user_data_dir.
- References to image/sound files that don't exist (draw/generate instead).
- Network calls without the INTERNET permission.
- Setting Window.size / Window.fullscreen in code.
- Exceptions in __init__ / build().

Keep behavior the same; just make it correct and robust. Output EXACTLY the same section format you were given (<<<NAME>>> ... <<<END>>>) with the FULL corrected file in <<<MAIN_PY>>>. No prose, no fences."""

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
        # class App / run presence
        has_app = any(
            isinstance(n, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id == "App") or
                (isinstance(b, ast.Attribute) and b.attr == "App") for b in n.bases)
            for n in ast.walk(tree))
        if not has_app:
            add("warn", "no `class X(App)` found -> the app may not launch",
                "Define an App subclass with a build() method.")
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


def host_can_display():
    """A headless test run needs either a live $DISPLAY or xvfb-run to fake one."""
    return bool(os.environ.get("DISPLAY")) or shutil.which("xvfb-run") is not None


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
_T_BLANK = '''from kivy.app import App
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
            Color(*Theme.ACCENT)
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
    "blank": {"label": "Blank Kivy", "desc": "Minimal app, one label", "code": _T_BLANK},
    "kit":   {"label": "Kit starter", "desc": "AppBar + Card + buttons (Dawg kit)", "code": _T_KIT},
    "form":  {"label": "Form + list", "desc": "TextField that appends to a scrolling list", "code": _T_FORM},
    "game":  {"label": "Game loop", "desc": "60fps canvas, touch + score", "code": _T_GAME},
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


def call_ai(messages):
    sf = sf_key()
    gq = groq_key()
    errs = []
    if sf:
        sf_u = chat_url(CONFIG.get("sf_url") or SF_URL)
        try:
            d = _post_json(sf_u, sf, {
                "model": CONFIG.get("sf_model") or SF_MODEL, "messages": messages,
                "temperature": 0.4, "max_tokens": 16000,
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
                "temperature": 0.4, "max_tokens": 16000,
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


def _recompute(payload):
    """payload['main_py'] is a FULL file (kit + app). Heal the kit and recompute checks."""
    if not payload.get("ok"):
        return payload
    full = ensure_kit(payload["main_py"])
    payload["main_py"] = full
    payload["syntax_ok"], payload["syntax_msg"] = syntax_check(full)
    payload["errors"], payload["warnings"] = validate_code(full, payload.get("requirements", ""))
    payload["issues"] = analyze_code(full, payload.get("requirements", ""), payload.get("permissions", ""))
    return payload


def ai_fix(main_py, error, requirements, permissions):
    msg = "ERROR / FINDINGS:\n" + (error or "(none given)") + "\n\nCURRENT FILE (main.py):\n" + (main_py or "")
    messages = [{"role": "system", "content": FIX_PROMPT}, {"role": "user", "content": msg}]
    text, provider = call_ai(messages)
    payload = build_forge_payload(text, "fix")
    if payload.get("ok"):
        # the model returns the whole file incl. kit -> heal it back to canonical
        payload["main_py"] = ensure_kit(payload["main_py"])
        if not payload.get("requirements"):
            payload["requirements"] = fix_requirements(requirements)
        _recompute(payload)
    payload["provider"] = provider
    return payload


def ai_polish(main_py, requirements, permissions):
    msg = "FILE TO RESTYLE (main.py):\n" + (main_py or "")
    messages = [{"role": "system", "content": POLISH_PROMPT}, {"role": "user", "content": msg}]
    text, provider = call_ai(messages)
    payload = build_forge_payload(text, "polish")
    if payload.get("ok"):
        payload["main_py"] = ensure_kit(payload["main_py"])
        if not payload.get("requirements"):
            payload["requirements"] = fix_requirements(requirements)
        _recompute(payload)
    payload["provider"] = provider
    return payload


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
_RUNNER = r'''import os, sys, threading, traceback

def _watchdog():
    import time
    time.sleep(25)
    sys.stderr.write("DAWG_TEST_TIMEOUT\n")
    sys.stderr.flush()
    os._exit(124)

threading.Thread(target=_watchdog, daemon=True).start()

try:
    from kivy.app import App
    from kivy.clock import Clock
    _orig = App.run
    def _patched(self, *a, **k):
        # let it draw a couple frames, then stop so run() returns
        Clock.schedule_once(lambda *_: self.stop(), 2.0)
        return _orig(self, *a, **k)
    App.run = _patched
except Exception:
    traceback.print_exc()

ns = {"__name__": "__main__", "__file__": "main.py"}
try:
    with open("main.py", "r") as f:
        src = f.read()
    exec(compile(src, "main.py", "exec"), ns)
    print("DAWG_TEST_OK")
    os._exit(0)
except SystemExit:
    print("DAWG_TEST_OK")
    os._exit(0)
except Exception:
    traceback.print_exc()
    print("DAWG_TEST_FAIL")
    os._exit(1)
'''

_BENIGN = ("Cutbuffer", "xclip", "xsel", "Unable to open the clipboard",
           "[INFO", "[WARNING", "[DEBUG", "sdl2 - Unable")


def _missing_module(text):
    m = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]", text or "")
    return m.group(1).split(".")[0] if m else None


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
        rec["summary"] = "no display available. Install xvfb to enable headless test runs: sudo apt install -y xvfb"
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

    if os.environ.get("DISPLAY"):
        cmd = [sys.executable, "__dawg_run.py"]
    else:
        cmd = ["xvfb-run", "-a", sys.executable, "__dawg_run.py"]
    env = dict(os.environ, KIVY_NO_ARGS="1", KIVY_LOG_LEVEL="warning",
               PYTHONUNBUFFERED="1", KIVY_NO_CONSOLELOG="0")
    log("$ " + " ".join(cmd))
    log("(running the app headless for ~2s to catch launch crashes)")
    log("")
    try:
        proc = subprocess.run(cmd, cwd=tdir, env=env, capture_output=True, text=True, timeout=45)
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

    # surface non-benign output lines
    for line in out.splitlines():
        if line.strip() and not any(b in line for b in _BENIGN):
            log(line)

    has_tb = "Traceback (most recent call last)" in out
    miss = _missing_module(out)
    if rc == 124 or "DAWG_TEST_TIMEOUT" in out:
        rec["status"] = "timeout"
        rec["summary"] = "the app hung (no clean exit) -- likely a blocking loop or input() that would ANR on the phone."
    elif miss and miss in ANDROID_ONLY:
        rec["status"] = "warn"
        rec["summary"] = "imports the android-only module '%s', which can't run on desktop. That's expected -- just make sure the import is guarded by `if platform == \"android\":` so desktop test runs (and the launch path) skip it." % miss
    elif miss and miss in {x.strip().lower() for x in (requirements or "").split(",")} and miss in SAFE_REQS:
        rec["status"] = "warn"
        rec["summary"] = "host is missing '%s', so it couldn't be fully test-run here, but it IS declared and Buildozer will bundle it into the APK. Looks fine to build." % miss
    elif has_tb or rc not in (0,):
        rec["status"] = "fail"
        rec["summary"] = "the app crashed on launch (traceback above). Hit AUTO-FIX to send the error back for a fix, or fix it in the editor."
    elif "DAWG_TEST_OK" in out:
        rec["status"] = "pass"
        rec["summary"] = "launched clean and ran for ~2s with no crash. Good to build."
    else:
        rec["status"] = "warn"
        rec["summary"] = "finished without a clear pass/fail signal. Review the output above."
    log("")
    log(rec["summary"])

# ----------------------------------------------------------------- server helpers
def safe_archs(a):
    known = {"arm64-v8a", "armeabi-v7a", "x86", "x86_64"}
    parts = [x.strip() for x in (a or "").replace(";", ",").split(",") if x.strip() in known]
    return ",".join(dict.fromkeys(parts)) or ANDROID_ARCHS


def manual_payload(name, code, title="", requirements="python3,kivy", permissions="", orientation="portrait"):
    """Wrap hand-written / template app code into a forge-shaped payload (kit prepended)."""
    full = with_kit(code or "")
    sok, smsg = syntax_check(full)
    reqs = fix_requirements(requirements)
    errs, warns = validate_code(full, reqs)
    issues = analyze_code(full, reqs, permissions)
    nm = slugify(name or "app")
    return {
        "ok": True, "name": nm, "title": (title or nm.replace("_", " ").title()),
        "orientation": orientation if orientation in ("portrait", "landscape", "all") else "portrait",
        "requirements": reqs, "permissions": clean_perms(permissions), "notes": "",
        "main_py": full, "syntax_ok": sok, "syntax_msg": smsg,
        "errors": errs, "warnings": warns, "issues": issues,
        "build_overrides": {}, "build_warnings": [], "provider": "template (no AI)",
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
        if path == "/api/doctor":
            return self._send(200, {"checks": doctor(), "can_test": host_can_test()})
        if path == "/api/templates":
            return self._send(200, {"templates": [
                {"id": k, "label": v["label"], "desc": v["desc"]} for k, v in TEMPLATES.items()
            ]})
        if path == "/api/template":
            qs = parse_qs(urlparse(self.path).query)
            tid = (qs.get("id") or [""])[0]
            t = TEMPLATES.get(tid)
            if not t:
                return self._send(404, {"error": "no such template"})
            return self._send(200, manual_payload(tid, t["code"]))
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
        ok = save_config(CONFIG)
        return self._send(200, {
            "saved": ok,
            "sf_key_set": bool(sf_key()),
            "groq_key_set": bool(groq_key()),
        })

    def handle_forge(self, body):
        desc = (body.get("description") or "").strip()
        history = body.get("history") or []
        if not desc:
            return self._send(400, {"error": "empty description"})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += history[-8:]
        messages += [{"role": "user", "content": desc}]
        try:
            text, provider = call_ai(messages)
        except Exception as e:
            return self._send(502, {"error": str(e)})
        payload = build_forge_payload(text, desc)
        if payload.get("ok"):
            payload["main_py"] = with_kit(payload["main_py"])
            _recompute(payload)
        payload["provider"] = provider
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
        # render assets into bytes so the zip is build-ready out of the box
        icon_b = splash_b = None
        try:
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
            "APK lands in bin/.\n" % (title, name)
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
            return self._send(400, {"error":
                "Java %s is active, but Buildozer's Gradle needs JDK 17. The build would "
                "run for ages then die at the Gradle step. Fix: sudo apt install -y "
                "openjdk-17-jdk  then relaunch The Dawg (it uses JDK 17 automatically once "
                "installed)." % jver})
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
<link rel="shortcut icon" type="image/png" href="/icon.png">
<link rel="apple-touch-icon" href="/icon.png">
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
    border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0c110c,#080b08);
    position:sticky;top:0;z-index:20}
  header .dot{width:10px;height:10px;border-radius:50%;background:var(--green);
    box-shadow:0 0 10px var(--green)}
  header h1{font-size:15px;letter-spacing:2px;margin:0;font-weight:700}
  header h1 span{color:var(--cyan)}
  header h1 .ver{color:var(--muted);font-size:10px;font-weight:400;letter-spacing:1px}
  header .spacer{margin-left:auto}
  #doctor{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;max-width:540px}
  main{max-width:1020px;margin:0 auto;padding:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:14px;margin-bottom:16px}
  label{display:block;color:var(--muted);font-size:12px;margin-bottom:6px;letter-spacing:1px}
  textarea,input[type=text],.codebox,select{width:100%;background:var(--panel2);color:var(--txt);
    border:1px solid var(--line);border-radius:8px;padding:11px;
    font-family:inherit;font-size:13px}
  textarea{resize:vertical}
  #desc{height:96px}
  .codebox{height:420px;white-space:pre;overflow:auto;tab-size:4;line-height:1.5}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .grid4{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px}
  button{cursor:pointer;border:1px solid var(--line);background:var(--panel2);
    color:var(--txt);padding:10px 16px;border-radius:8px;font-family:inherit;
    font-size:13px;letter-spacing:1px;transition:.12s}
  button:hover{border-color:var(--green);box-shadow:0 0 0 1px var(--green) inset}
  button:disabled{opacity:.45;cursor:not-allowed;box-shadow:none;border-color:var(--line)}
  button.primary{background:#10220f;border-color:#1f4a1c;color:var(--green)}
  button.primary:hover{box-shadow:0 0 14px rgba(73,211,103,.35)}
  button.build{background:#0c1f24;border-color:#1d4a55;color:var(--cyan)}
  button.build:hover{box-shadow:0 0 14px rgba(54,199,226,.35)}
  button.warn{background:#241c0c;border-color:#5a4a1f;color:var(--amber)}
  button.warn:hover{box-shadow:0 0 14px rgba(232,179,65,.3)}
  .tabs{display:flex;gap:8px;margin-bottom:14px}
  .tab{padding:9px 18px;border-radius:8px 8px 0 0;border:1px solid var(--line);
    background:var(--panel2);color:var(--muted);letter-spacing:1px}
  .tab.active{color:var(--green);border-color:#1f4a1c;background:#10220f}
  .chip{padding:7px 12px;font-size:12px;border-radius:20px}
  .chip:hover{border-color:var(--cyan)}
  .meta{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 12px}
  .tag{font-size:11px;padding:4px 9px;border:1px solid var(--line);border-radius:6px;
    color:var(--muted);background:var(--panel2)}
  .tag b{color:var(--txt);font-weight:600}
  .ok{color:var(--green);border-color:#1f4a1c}
  .bad{color:var(--danger);border-color:#5a1f1f}
  .warnc{color:var(--amber);border-color:#5a4a1f}
  .hidden{display:none !important}
  .gear{font-size:12px;padding:8px 12px}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.62);display:flex;
    align-items:center;justify-content:center;z-index:50}
  .modal{background:var(--panel);border:1px solid var(--line);border-radius:12px;
    width:min(560px,92vw);max-height:88vh;overflow:auto;padding:18px}
  .modal h2{margin:0 0 14px;font-size:14px;letter-spacing:2px;color:var(--cyan)}
  .modal input[type=text],.modal input[type=password]{margin-bottom:4px}
  .modal .field{margin-bottom:14px}
  .modal .adv{margin:2px 0 8px;border-top:1px solid var(--line);padding-top:10px}
  .modal .adv summary{cursor:pointer;color:var(--muted);font-size:12px;letter-spacing:1px}
  .modal .clr{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:11px;margin-top:4px}
  .hint{color:var(--muted);font-size:12px;margin-top:8px}
  .sectlabel{color:var(--muted);font-size:11px;letter-spacing:2px;text-transform:uppercase;margin:2px 0 8px}
  #validation{background:#060806;border:1px solid var(--line);border-radius:8px;padding:10px;
    margin-top:12px;max-height:220px;overflow:auto;font-size:12.5px}
  .issue{display:flex;gap:8px;padding:5px 0;border-bottom:1px dashed #15201510}
  .issue .sev{flex:0 0 auto;font-weight:700;letter-spacing:1px}
  .issue .body{flex:1}
  .issue .fix{color:var(--muted);font-size:11.5px;margin-top:2px}
  .sev.error{color:var(--danger)} .sev.warn{color:var(--amber)} .sev.info{color:var(--cyan)}
  .sev.ok{color:var(--green)}
  #log,#testout{height:300px;white-space:pre-wrap;overflow:auto;background:#060806;
    border:1px solid var(--line);border-radius:8px;padding:11px;font-size:12px;color:#bfe0bf}
  #testout{height:170px}
  details.adv2{margin-top:12px;border:1px solid var(--line);border-radius:8px;padding:10px;background:var(--panel2)}
  details.adv2 summary{cursor:pointer;color:var(--muted);letter-spacing:1px;font-size:12px}
  .pillset{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px}
  .pillset label{display:flex;align-items:center;gap:6px;color:var(--txt);margin:0}
  .spin{display:inline-block;width:11px;height:11px;border:2px solid var(--muted);
    border-top-color:var(--cyan);border-radius:50%;animation:sp .7s linear infinite;vertical-align:-1px}
  @keyframes sp{to{transform:rotate(360deg)}}
  a.link{color:var(--cyan);text-decoration:none}
</style>
</head>
<body>
<header>
  <span class="dot"></span>
  <h1>THE DAWG // <span>APK FORGE</span> <span class="ver">v2.0</span></h1>
  <span class="spacer" style="margin-left:auto"></span>
  <div id="doctor"></div>
  <button class="gear" onclick="openSettings()">&#9881; settings</button>
  <button class="gear" onclick="quitApp()">quit</button>
</header>
<main>
  <div class="tabs">
    <div class="tab active" id="tab_ai" onclick="setMode('ai')">AI FORGE</div>
    <div class="tab" id="tab_manual" onclick="setMode('manual')">MANUAL</div>
  </div>

  <div class="card" id="ai_panel">
    <label>describe the android app you want</label>
    <textarea id="desc" placeholder="e.g. a dark-themed pomodoro timer with start/pause, a circular countdown, and a session counter that saves between launches"></textarea>
    <div class="row" style="margin-top:10px">
      <button class="primary" id="forgeBtn" onclick="forge()">FORGE APP</button>
      <span class="hint" style="margin:0">Ctrl/Cmd+Enter to forge. The kit + a launcher icon are added automatically.</span>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="chip" onclick="refine('Make it look more polished and modern, tighten the layout and spacing')">&#10022; polish look</button>
      <button class="chip" onclick="refine('Add sound effects generated at runtime (no external files)')">&#10022; add sound</button>
      <button class="chip" onclick="refine('Add a settings screen and persist preferences in user_data_dir')">&#10022; add settings</button>
      <button class="chip" onclick="refine('Add a high score / stats screen saved between launches')">&#10022; add scores</button>
    </div>
  </div>

  <div class="card hidden" id="manual_panel">
    <label>start from a template (the Dawg UI kit is included at the top so you can use Card / PillButton / TextField / etc.)</label>
    <div class="row">
      <select id="tpl_sel" style="max-width:320px"></select>
      <button onclick="loadTemplate()">LOAD TEMPLATE</button>
      <button onclick="loadTemplate('blank')">BLANK</button>
      <span class="hint" style="margin:0">then edit main.py below and use TEST RUN / BUILD.</span>
    </div>
  </div>

  <div class="card hidden" id="out">
    <div class="meta" id="meta"></div>

    <div class="grid4" style="margin-bottom:10px">
      <div><label>app name (package)</label><input type="text" id="f_name"></div>
      <div><label>title</label><input type="text" id="f_title"></div>
      <div><label>orientation</label>
        <select id="f_orient">
          <option value="portrait">portrait</option>
          <option value="landscape">landscape</option>
          <option value="all">all</option>
        </select>
      </div>
      <div><label>permissions</label><input type="text" id="f_perms" placeholder="INTERNET,VIBRATE"></div>
    </div>
    <div style="margin-bottom:10px"><label>requirements</label><input type="text" id="f_reqs" placeholder="python3,kivy"></div>

    <label>main.py (full file -- kit at top, your app below)</label>
    <textarea id="code" class="codebox" spellcheck="false"></textarea>

    <div id="validation"></div>

    <div class="row" style="margin-top:12px">
      <button class="build" id="buildBtn" onclick="buildApk()">BUILD APK</button>
      <button id="testBtn" onclick="testRun()">TEST RUN</button>
      <button class="warn" id="fixBtn" onclick="autoFix()">AUTO-FIX</button>
      <button id="polishBtn" onclick="polish()">POLISH</button>
      <button id="zipBtn" onclick="downloadProject()">DOWNLOAD PROJECT</button>
    </div>

    <details class="adv2">
      <summary>advanced build config</summary>
      <div class="pillset">
        <label><input type="checkbox" id="arch_a64" checked> arm64-v8a <span class="hint" style="margin:0">(ROG 5S + all modern)</span></label>
        <label><input type="checkbox" id="arch_a32"> armeabi-v7a <span class="hint" style="margin:0">(old 32-bit)</span></label>
      </div>
      <div class="grid" style="margin-top:10px;max-width:420px">
        <div><label>android.api</label><input type="text" id="b_api" value="34"></div>
        <div><label>android.minapi</label><input type="text" id="b_minapi" value="24"></div>
      </div>
      <div class="hint">arm64-v8a alone covers your ROG Phone 5S and every current device, and halves build time.</div>
    </details>

    <div id="testpanel" class="hidden" style="margin-top:14px">
      <div class="sectlabel">test run</div>
      <div id="testout"></div>
    </div>
  </div>

  <div class="card hidden" id="logwrap">
    <div class="row" style="margin-bottom:10px">
      <div class="sectlabel" style="margin:0">build log</div>
      <span class="spacer" style="margin-left:auto"></span>
      <button id="apkBtn" class="build hidden" onclick="downloadApk()">DOWNLOAD APK</button>
    </div>
    <div id="log"></div>
  </div>
</main>

<div class="overlay hidden" id="settings" onclick="overlayClick(event)">
  <div class="modal">
    <h2>SETTINGS</h2>
    <div class="field">
      <label>SiliconFlow API key <span id="sfset" class="hint"></span></label>
      <input type="password" id="sf_key" placeholder="sk-... (leave blank to keep current)">
      <div class="clr"><input type="checkbox" id="clear_sf"> clear stored key</div>
    </div>
    <div class="field">
      <label>SiliconFlow model</label>
      <select id="sf_model_sel" onchange="onModelChange()">
        <option value="deepseek-ai/DeepSeek-V4-Flash">deepseek-ai/DeepSeek-V4-Flash</option>
        <option value="deepseek-ai/DeepSeek-V3">deepseek-ai/DeepSeek-V3</option>
        <option value="Qwen/Qwen2.5-Coder-32B-Instruct">Qwen/Qwen2.5-Coder-32B-Instruct</option>
        <option value="__custom__">custom...</option>
      </select>
      <input type="text" id="sf_model_custom" class="hidden" placeholder="provider/model" style="margin-top:6px">
    </div>
    <details class="adv">
      <summary>advanced (endpoints + Groq fallback)</summary>
      <div class="field" style="margin-top:12px">
        <label>SiliconFlow endpoint</label>
        <input type="text" id="sf_url" placeholder="https://api.siliconflow.cn/v1">
      </div>
      <div class="field">
        <label>Groq API key (fallback) <span id="gqset" class="hint"></span></label>
        <input type="password" id="groq_key" placeholder="gsk-... (leave blank to keep current)">
        <div class="clr"><input type="checkbox" id="clear_groq"> clear stored key</div>
      </div>
      <div class="field">
        <label>Groq model</label>
        <input type="text" id="groq_model" placeholder="llama-3.3-70b-versatile">
      </div>
      <div class="field">
        <label>Groq endpoint</label>
        <input type="text" id="groq_url" placeholder="https://api.groq.com/openai/v1">
      </div>
    </details>
    <div class="row">
      <button class="primary" onclick="saveSettings()">SAVE</button>
      <button onclick="closeSettings()">CLOSE</button>
      <span id="setmsg" class="hint" style="margin:0"></span>
    </div>
    <div class="hint" style="margin-top:10px">Keys are stored in ~/.androdawg/config.json (chmod 600). SiliconFlow/DeepSeek is primary; Groq is the fallback.</div>
  </div>
</div>

<script>
var cur = null;
var history = [];
var mode = 'ai';
var pollT = null, testT = null;

function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function $(id){return document.getElementById(id);}
function show(id){$(id).classList.remove('hidden');}
function hide(id){$(id).classList.add('hidden');}

function setMode(m){
  mode=m;
  $('tab_ai').classList.toggle('active', m==='ai');
  $('tab_manual').classList.toggle('active', m==='manual');
  $('ai_panel').classList.toggle('hidden', m!=='ai');
  $('manual_panel').classList.toggle('hidden', m!=='manual');
}

function refine(text){
  if(!cur){ $('desc').value = (($('desc').value||'')+' '+text).trim(); $('desc').focus(); return; }
  $('desc').value = text;
  forge();
}

function metaTags(p){
  var t=[];
  if(p.provider) t.push('<span class="tag">via <b>'+esc(p.provider)+'</b></span>');
  t.push('<span class="tag '+(p.syntax_ok?'ok':'bad')+'">syntax: <b>'+(p.syntax_ok?'ok':'error')+'</b></span>');
  var ne=(p.errors||[]).length, nw=(p.warnings||[]).length;
  t.push('<span class="tag '+(ne?'bad':'ok')+'">errors: <b>'+ne+'</b></span>');
  t.push('<span class="tag '+(nw?'warnc':'')+'">warnings: <b>'+nw+'</b></span>');
  if(p.notes) t.push('<span class="tag">'+esc(p.notes)+'</span>');
  $('meta').innerHTML = t.join('');
}

function renderValidation(p){
  var rows=[];
  if(p.syntax_ok){ rows.push('<div class="issue"><div class="sev ok">OK</div><div class="body">syntax valid</div></div>'); }
  else { rows.push('<div class="issue"><div class="sev error">SYNTAX</div><div class="body">'+esc(p.syntax_msg||'syntax error')+'</div></div>'); }
  var iss = p.issues||[];
  iss.forEach(function(it){
    var sev=(it.sev||'info');
    rows.push('<div class="issue"><div class="sev '+sev+'">'+sev.toUpperCase()+'</div><div class="body">'+esc(it.msg)+
      (it.fix?'<div class="fix">&#8627; '+esc(it.fix)+'</div>':'')+'</div></div>');
  });
  (p.build_warnings||[]).forEach(function(w){
    rows.push('<div class="issue"><div class="sev warn">BUILD</div><div class="body">'+esc(w)+'</div></div>');
  });
  if(iss.length===0 && p.syntax_ok){ rows.push('<div class="issue"><div class="sev ok">OK</div><div class="body">no launch issues found by static analysis</div></div>'); }
  $('validation').innerHTML = rows.join('');
}

function render(p){
  if(!p || !p.ok){
    alert((p&&p.error)||'forge failed');
    return;
  }
  cur=p;
  show('out');
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
  metaTags(p);
  renderValidation(p);
  refreshButtons();
  $('out').scrollIntoView({behavior:'smooth',block:'nearest'});
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
  cur.archs = archs.join(',')||'arm64-v8a';
  cur.build_overrides = {api:$('b_api').value.trim(), minapi:$('b_minapi').value.trim(), orientation:cur.orientation};
  return cur;
}

function reval(){
  // local re-validation pass after manual edits (server is source of truth on build)
  if(!cur) return;
  collect();
}

async function forge(){
  var desc=$('desc').value.trim();
  if(!desc){ $('desc').focus(); return; }
  var btn=$('forgeBtn'); btn.disabled=true; var old=btn.textContent; btn.innerHTML='<span class="spin"></span> forging...';
  history.push({role:'user', content:desc});
  try{
    var r=await fetch('/api/forge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({description:desc, history:history.slice(0,-1)})});
    var d=await r.json();
    if(!r.ok){ alert(d.error||('forge failed ('+r.status+')')); return; }
    if(d.raw) history.push({role:'assistant', content:d.raw});
    render(d);
  }catch(e){ alert('network: '+e); }
  finally{ btn.disabled=false; btn.textContent=old; }
}

async function loadTemplate(force){
  var id = force || $('tpl_sel').value;
  try{
    var r=await fetch('/api/template?id='+encodeURIComponent(id));
    var d=await r.json();
    if(!r.ok){ alert(d.error||'template failed'); return; }
    history=[];
    render(d);
  }catch(e){ alert('network: '+e); }
}

async function autoFix(){
  if(!cur) return; collect();
  var btn=$('fixBtn'); btn.disabled=true; var old=btn.textContent; btn.innerHTML='<span class="spin"></span> fixing...';
  try{
    var err=(cur.issues||[]).filter(function(i){return i.sev==='error'||i.sev==='warn';}).map(function(i){return i.msg;}).join('\n');
    var r=await fetch('/api/fix',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements, permissions:cur.permissions, error:err})});
    var d=await r.json();
    if(!r.ok){ alert(d.error||'fix failed'); return; }
    render(d);
  }catch(e){ alert('network: '+e); }
  finally{ btn.disabled=false; btn.textContent=old; }
}

async function polish(){
  if(!cur) return; collect();
  var btn=$('polishBtn'); btn.disabled=true; var old=btn.textContent; btn.innerHTML='<span class="spin"></span> polishing...';
  try{
    var r=await fetch('/api/polish',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements, permissions:cur.permissions})});
    var d=await r.json();
    if(!r.ok){ alert(d.error||'polish failed'); return; }
    render(d);
  }catch(e){ alert('network: '+e); }
  finally{ btn.disabled=false; btn.textContent=old; }
}

async function testRun(){
  if(!cur) return; collect();
  show('testpanel'); $('testout').textContent='starting test run...';
  var btn=$('testBtn'); btn.disabled=true; var old=btn.textContent; btn.innerHTML='<span class="spin"></span> testing...';
  try{
    var r=await fetch('/api/testrun',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({main_py:cur.main_py, requirements:cur.requirements})});
    var d=await r.json();
    if(!r.ok){ $('testout').textContent=d.error||'test failed'; btn.disabled=false; btn.textContent=old; return; }
    pollTest(d.test_id, btn, old);
  }catch(e){ $('testout').textContent='network: '+e; btn.disabled=false; btn.textContent=old; }
}

function pollTest(tid, btn, old){
  if(testT) clearInterval(testT);
  testT=setInterval(async function(){
    try{
      var r=await fetch('/api/testlog?id='+tid); var d=await r.json();
      $('testout').textContent=(d.log||'')+'\n';
      $('testout').scrollTop=$('testout').scrollHeight;
      if(d.status && d.status!=='running'){
        clearInterval(testT); testT=null;
        btn.disabled=false; btn.textContent=old;
      }
    }catch(e){ clearInterval(testT); testT=null; btn.disabled=false; btn.textContent=old; }
  }, 700);
}

async function buildApk(){
  if(!cur) return; collect();
  if(cur.errors && cur.errors.length){
    if(!confirm('There are '+cur.errors.length+' blocking error(s). The server will refuse the build. Continue anyway?')) return;
  }
  var btn=$('buildBtn'); btn.disabled=true; var old=btn.textContent; btn.innerHTML='<span class="spin"></span> starting...';
  hide('apkBtn');
  try{
    var r=await fetch('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cur)});
    var d=await r.json();
    if(!r.ok){ alert(d.error||'build refused'); btn.disabled=false; btn.textContent=old; return; }
    show('logwrap'); $('log').textContent='build started ('+d.build_id+')...\n';
    $('logwrap').scrollIntoView({behavior:'smooth',block:'nearest'});
    pollBuild(d.build_id, btn, old);
  }catch(e){ alert('network: '+e); btn.disabled=false; btn.textContent=old; }
}

function pollBuild(bid, btn, old){
  if(pollT) clearInterval(pollT);
  pollT=setInterval(async function(){
    try{
      var r=await fetch('/api/log?id='+bid); var d=await r.json();
      $('log').textContent=d.log||'';
      $('log').scrollTop=$('log').scrollHeight;
      if(d.status==='done' || d.status==='failed'){
        clearInterval(pollT); pollT=null;
        btn.disabled=false; btn.textContent=old;
        if(d.status==='done' && d.apk){ window._apkId=bid; show('apkBtn'); }
      }
    }catch(e){ clearInterval(pollT); pollT=null; btn.disabled=false; btn.textContent=old; }
  }, 1500);
}

function downloadApk(){
  if(!window._apkId) return;
  window.location='/api/apk?id='+window._apkId;
}

async function downloadProject(){
  if(!cur) return; collect();
  try{
    var r=await fetch('/api/project_zip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cur)});
    if(!r.ok){ var e=await r.json(); alert('zip error: '+(e.error||r.status)); return; }
    var blob=await r.blob();
    var a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=(cur.name||'app')+'_buildozer.zip';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function(){URL.revokeObjectURL(a.href);},1000);
  }catch(e){ alert('network: '+e); }
}

function refreshButtons(){
  var has = !!cur;
  ['buildBtn','testBtn','fixBtn','polishBtn','zipBtn'].forEach(function(id){ $(id).disabled = !has; });
}

async function loadDoctor(){
  try{
    var r=await fetch('/api/doctor'); var d=await r.json();
    var html=(d.checks||[]).map(function(c){
      return '<span class="tag '+(c[1]?'ok':'bad')+'">'+(c[1]?'\u2713 ':'\u2717 ')+esc(c[0])+'</span>';
    }).join('');
    $('doctor').innerHTML=html||'<span class="tag">no checks</span>';
  }catch(e){ $('doctor').innerHTML='<span class="tag bad">doctor failed</span>'; }
}

async function loadTemplates(){
  try{
    var r=await fetch('/api/templates'); var d=await r.json();
    var sel=$('tpl_sel'); sel.innerHTML='';
    (d.templates||[]).forEach(function(t){
      var o=document.createElement('option'); o.value=t.id; o.textContent=t.label+' -- '+t.desc; sel.appendChild(o);
    });
  }catch(e){}
}

function onModelChange(){
  var sel=$('sf_model_sel'), cust=$('sf_model_custom');
  if(sel.value==='__custom__'){ cust.classList.remove('hidden'); cust.focus(); } else { cust.classList.add('hidden'); }
}
async function openSettings(){
  try{
    var r=await fetch('/api/config'); var d=await r.json();
    var sel=$('sf_model_sel'), cust=$('sf_model_custom');
    var m=d.sf_model||'deepseek-ai/DeepSeek-V4-Flash', found=false;
    for(var i=0;i<sel.options.length;i++){ if(sel.options[i].value===m){found=true;break;} }
    if(found){ sel.value=m; cust.classList.add('hidden'); cust.value=''; }
    else { sel.value='__custom__'; cust.classList.remove('hidden'); cust.value=m; }
    if(d.sf_url) $('sf_url').value=d.sf_url;
    if(d.groq_model) $('groq_model').value=d.groq_model;
    if(d.groq_url) $('groq_url').value=d.groq_url;
    $('sf_key').value=''; $('groq_key').value='';
    $('clear_sf').checked=false; $('clear_groq').checked=false;
    $('sfset').textContent=d.sf_key_set?'(stored)':(d.sf_env?'(from env)':'(not set)');
    $('gqset').textContent=d.groq_key_set?'(stored)':(d.groq_env?'(from env)':'');
    $('setmsg').textContent='';
  }catch(e){}
  show('settings');
}
function closeSettings(){ hide('settings'); }
function overlayClick(e){ if(e.target && e.target.id==='settings'){ closeSettings(); } }
async function saveSettings(){
  var sel=$('sf_model_sel');
  var model=(sel.value==='__custom__')?$('sf_model_custom').value.trim():sel.value;
  var body={ sf_key:$('sf_key').value, groq_key:$('groq_key').value, sf_model:model,
    sf_url:$('sf_url').value, groq_model:$('groq_model').value, groq_url:$('groq_url').value,
    clear_sf:$('clear_sf').checked, clear_groq:$('clear_groq').checked };
  try{
    var r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var d=await r.json();
    $('setmsg').textContent=d.saved?'saved':'save failed (check ~/.androdawg perms)';
    loadDoctor();
    if(d.saved) setTimeout(closeSettings,500);
  }catch(e){ $('setmsg').textContent='error: '+e; }
}

async function quitApp(){
  try{ await fetch('/api/quit',{method:'POST'}); }catch(e){}
  document.body.innerHTML='<div style="color:#6e8070;font-family:ui-monospace,monospace;padding:48px;font-size:14px">The Dawg stopped. You can close this window.</div>';
  setTimeout(function(){ try{window.close();}catch(e){} },400);
}

$('desc').addEventListener('keydown', function(e){ if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){ forge(); } });
$('code').addEventListener('input', function(){ if(cur){ collect(); } });
document.addEventListener('keydown', function(e){ if(e.key==='Escape'){ closeSettings(); } });
loadDoctor(); loadTemplates(); refreshButtons();
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
