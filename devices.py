#!/usr/bin/env python3
"""
THE DAWG // device profiles

A tiny, dependency-free catalogue of real Android screens so the desktop PREVIEW can pop
your app up at the exact logical size a phone would give it -- not some arbitrary window.

"Logical" size is what a Kivy app actually sees (density-independent px, i.e. dp). A phone
with a 1080x2400 physical panel at ~2.6x density hands the app roughly 411x915 dp, and that
is the number that matters for layout. Each profile stores that logical size plus the notch
style and screen-corner radius so preview.py can draw believable device chrome.

Standalone:
    python3 devices.py            # list every profile
    python3 devices.py pixel_8    # show one
"""

# key -> profile.  w/h are LOGICAL dp (what the app lays out in), matching the real device.
DEVICES = {
    # --- phones ---------------------------------------------------------------
    "pixel_8":      {"label": "Google Pixel 8",        "w": 412, "h": 915, "dpi": 428,
                     "notch": "punch", "radius": 30, "category": "phone"},
    "pixel_8_pro":  {"label": "Google Pixel 8 Pro",    "w": 448, "h": 998, "dpi": 489,
                     "notch": "punch", "radius": 34, "category": "phone"},
    "pixel_7a":     {"label": "Google Pixel 7a",       "w": 411, "h": 914, "dpi": 429,
                     "notch": "punch", "radius": 28, "category": "phone"},
    "pixel_4a":     {"label": "Google Pixel 4a (small)","w": 393, "h": 851, "dpi": 443,
                     "notch": "punch", "radius": 24, "category": "phone"},
    "galaxy_s24":   {"label": "Samsung Galaxy S24",    "w": 384, "h": 854, "dpi": 416,
                     "notch": "punch", "radius": 30, "category": "phone"},
    "galaxy_s23u":  {"label": "Samsung Galaxy S23 Ultra","w": 412, "h": 915, "dpi": 500,
                     "notch": "punch", "radius": 20, "category": "phone"},
    "oneplus_12":   {"label": "OnePlus 12",            "w": 450, "h": 1000, "dpi": 510,
                     "notch": "punch", "radius": 34, "category": "phone"},
    "moto_g":       {"label": "Moto G (budget)",       "w": 393, "h": 873, "dpi": 411,
                     "notch": "notch", "radius": 22, "category": "phone"},
    "generic_phone":{"label": "Generic phone (safe default)","w": 400, "h": 860, "dpi": 420,
                     "notch": "punch", "radius": 26, "category": "phone"},
    "small_phone":  {"label": "Small phone (stress test)","w": 360, "h": 760, "dpi": 400,
                     "notch": "none",  "radius": 20, "category": "phone"},
    # --- tablets --------------------------------------------------------------
    "pixel_tablet": {"label": "Google Pixel Tablet",   "w": 800, "h": 1280, "dpi": 276,
                     "notch": "none",  "radius": 24, "category": "tablet"},
    "galaxy_tab":   {"label": "Samsung Galaxy Tab S9", "w": 753, "h": 1205, "dpi": 274,
                     "notch": "none",  "radius": 22, "category": "tablet"},
}

DEFAULT = "pixel_8"

# friendly aliases so `--device pixel8` / `--device s24` still resolve
ALIASES = {
    "pixel8": "pixel_8", "pixel": "pixel_8", "pixel8pro": "pixel_8_pro",
    "pixel7a": "pixel_7a", "pixel4a": "pixel_4a", "s24": "galaxy_s24",
    "galaxys24": "galaxy_s24", "s23ultra": "galaxy_s23u", "s23u": "galaxy_s23u",
    "oneplus": "oneplus_12", "oneplus12": "oneplus_12", "moto": "moto_g",
    "phone": "generic_phone", "default": "pixel_8", "small": "small_phone",
    "tablet": "pixel_tablet", "pixeltablet": "pixel_tablet", "tab": "galaxy_tab",
}


def _norm(key):
    k = (key or "").strip().lower().replace("-", "_").replace(" ", "_")
    return k


def resolve(key):
    """Return (profile_key, profile_dict). Falls back to DEFAULT on an unknown name."""
    k = _norm(key)
    if k in DEVICES:
        return k, DEVICES[k]
    flat = k.replace("_", "")
    if flat in ALIASES:
        rk = ALIASES[flat]
        return rk, DEVICES[rk]
    if k in ALIASES:
        rk = ALIASES[k]
        return rk, DEVICES[rk]
    return DEFAULT, DEVICES[DEFAULT]


def get(key=DEFAULT):
    return resolve(key)[1]


def window_size(key=DEFAULT, max_h=880, scale=1.0):
    """Logical (w, h) for the preview window, scaled to fit a normal monitor.

    Real phone heights (~850-1000 dp) fit most screens, but we cap the long side to
    max_h by default so a tall phone doesn't run off a laptop display. `scale` multiplies
    on top of that for manual zoom."""
    p = get(key)
    w, h = float(p["w"]), float(p["h"])
    fit = min(1.0, float(max_h) / h) if max_h else 1.0
    s = fit * float(scale or 1.0)
    return max(240, int(round(w * s))), max(360, int(round(h * s)))


def names():
    return list(DEVICES.keys())


def table():
    rows = ["%-16s %-30s %-9s %-6s %-7s %s" %
            ("KEY", "DEVICE", "LOGICAL", "DPI", "NOTCH", "TYPE"),
            "-" * 78]
    for k, p in DEVICES.items():
        rows.append("%-16s %-30s %-9s %-6s %-7s %s" %
                    (k, p["label"], "%dx%d" % (p["w"], p["h"]),
                     p["dpi"], p["notch"], p["category"]))
    return "\n".join(rows)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        key, p = resolve(sys.argv[1])
        win = window_size(key)
        print("resolved '%s' -> %s (%s)" % (sys.argv[1], key, p["label"]))
        print("  logical: %dx%d dp   dpi: %d   notch: %s   corner: %ddp"
              % (p["w"], p["h"], p["dpi"], p["notch"], p["radius"]))
        print("  preview window: %dx%d px" % win)
    else:
        print(table())
