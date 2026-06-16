# THE DAWG // APK FORGE  (v2)

Describe an Android app -> AI forges a complete single-file Kivy app that already
looks like a real product -> Buildozer compiles it into a real `.apk`, all locally
on the X395. Same shape as TheDawg: stdlib-only Python server + browser UI.
SiliconFlow/DeepSeek-V4-Flash primary, Groq fallback.

**v2 in one line:** apps now look modern instead of default-grey, you can write
your own code (manual mode), the tool test-runs the app to catch launch crashes
*before* the 40-minute build, and the window opens under its **own panel icon**
instead of looking like a Brave tab.

## Install (one paste, does everything)

`curl -fsSL https://raw.githubusercontent.com/the-priest/androdawg/main/install.sh | bash`

This wipes any previous install (keeping your saved API key), installs every
system dependency, sets up **JDK 17** (via Temurin, since Kali has no
`openjdk-17` package), installs buildozer + cython into your user site,
best-effort installs **xvfb + host Kivy** (so TEST RUN works), pulls
`apkforge.py` + `icon.png` from GitHub, and drops a clickable **app-menu icon**
("The Dawg APK Forge") wired so the running window shows its own icon in the
panel. Re-running it is a clean reinstall.

License: MIT (see `LICENSE`).

## Keys

Set them in the **Settings (gear)** panel inside the app, or via env:

`export SILICONFLOW_API_KEY=sk-...`

`export GROQ_API_KEY=gsk_...`

Settings-panel keys persist to `~/.androdawg/config.json` (chmod 600) and take
effect immediately - no restart. Stored keys win over env. You can also change
the SiliconFlow model / base URL there.

## Run

Click **The Dawg APK Forge** in your app menu, or run `androdawg`.

Opens in **its own app window** (Brave/Chromium `--app` mode, no browser tab),
under its **own taskbar/panel entry with the dawg icon** - not folded into the
browser. Falls back to your default browser if neither is installed. Set
`DAWG_BROWSER` to force a specific browser binary. It's **single-instance** -
launching again just focuses the running window. Use the **quit** button (top
right) to stop it. Projects and the built `.apk` land in `~/AndroDawg/projects`.

## Two modes

- **AI FORGE** - describe an app, the AI writes a complete single-file Kivy app
  using the built-in UI kit, declares its requirements/permissions/orientation,
  and can set safe build options (a custom `buildozer.spec` within guardrails).
- **MANUAL** - write the code yourself. Pick a starter template (blank, UI-kit
  starter, form+list, game loop), edit the fields, and optionally hand-write a
  full raw `buildozer.spec`. Same validate / test / build pipeline.

## Why the apps stopped looking like ass

Every forged app gets a small, battle-tested **UI kit** prepended (pure Kivy, no
extra deps, so it always survives p4a): dark theme with an accent derived from
the app name, gradient background, rounded cards, pill buttons with press
animation, app bar, text fields, toasts. The AI builds its screens out of those
instead of raw grey Kivy widgets. You see the full assembled file in the editor -
nothing is hidden.

It also auto-generates a real **launcher icon + presplash** (pure Python, no
PIL) and wires `android.presplash_color` so there's **no white flash on launch**.

## Why your APKs stopped failing to launch

Before a build is even offered, the tool runs checks that catch the usual
"installs but won't open" causes:

- **TEST RUN** - if host Kivy is available, your app is launched on a virtual
  display (xvfb), auto-quit after 2s, and watched for a crash. ~3 seconds tells
  you it starts, instead of finding out after a 40-minute build. No Kivy on the
  host -> it cleanly reports "skipped" and the build still works.
- **Static analyzer** - unguarded android-only imports, missing `class X(App)` /
  `.run()`, relative file writes without `user_data_dir`, network use without the
  INTERNET permission, references to assets that don't exist, etc. Each issue
  shows a severity and a fix hint.
- **AUTO-FIX** - sends the code + the exact error back to the AI and applies the
  fix (the UI kit is protected and self-heals so it can't be mangled).
- **POLISH** - a design pass that makes the app lean harder on the kit.

Hard blockers still refuse the build outright: syntax errors, non-Kivy GUI
toolkits (tkinter/PyQt/PySide/GTK/curses), and buildozer missing from PATH.

## Build config (advanced)

The AI can set only **safe** spec knobs - orientation, fullscreen, api (24-35),
minapi (21-30), wakelock, presplash color. Anything else it tries is dropped with
a warning, so it can't brick a build. In manual mode you can override the whole
spec yourself.

- Default arch is **arm64-v8a** - covers the **ROG Phone 5S** and every modern
  phone, and halves build time. Tick **armeabi-v7a** in the advanced panel if you
  need 32-bit too.
- api 34 / minapi 24 by default (ROG 5S runs Android 11+, so this is comfortable).

## Flow

1. **Check the TOOLCHAIN strip** - green for buildozer / java / keys / cache, plus
   whether test-run is available.
2. **SMOKE-TEST APP** - loads a built-in, guaranteed-buildable app. Build it once
   to confirm your toolchain works end to end before trusting AI output.
3. **AI FORGE** a description, or switch to **MANUAL** and start from a template.
4. **TEST RUN** to confirm it launches, **AUTO-FIX** anything flagged, **POLISH**
   for looks - or hand-edit the code.
5. **BUILD APK** -> streams the buildozer log -> **DOWNLOAD .APK**. Or **DOWNLOAD
   PROJECT (.zip)** to get `main.py` + `buildozer.spec` + generated icon/presplash
   and build it anywhere.

## Notes

- Only **Kivy** survives the python-for-android pipeline; generated apps are Kivy.
- First build downloads the Android SDK/NDK (~20-40 min); `~/.buildozer` caches it,
  later builds are minutes.
- Provider stack is fixed: SiliconFlow/DeepSeek-V4-Flash primary, Groq fallback.

## Test it yourself

`python3 selftest.py`

Runs adversarial parser cases, the full HTTP pipeline (mocked AI + mocked
buildozer), and the v2 endpoints - templates, fix, polish, build-override
whitelist, the icon route, and a real headless test-run. No keys / no Android
toolchain needed. Pass a smaller hammer count to go faster, e.g. `python3
selftest.py 1000`.
