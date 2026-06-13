# THE DAWG // APK FORGE

Describe an Android app -> AI forges a complete single-file Kivy app -> Buildozer
compiles it into a real `.apk`, all locally on the X395. Same shape as TheDawg:
stdlib-only Python server + browser UI. SiliconFlow/DeepSeek-V4-Flash primary,
Groq fallback.

## Install (sole supported method)

`curl -fsSL https://raw.githubusercontent.com/the-priest/androdawg/main/install.sh | bash`

Installs system build deps, a buildozer venv (cython pinned), the launcher, and a
**desktop icon** - it shows up in your app menu as **"The Dawg APK Forge"**,
clickable. (Commit `icon.png` to the repo so the installer can fetch it; it falls
back gracefully if missing.)

## Keys

Set them in the **Settings (gear)** panel inside the app, or via env:

`export SILICONFLOW_API_KEY=sk-...`

`export GROQ_API_KEY=gsk_...`

Settings-panel keys persist to `~/.androdawg/config.json` (chmod 600) and take
effect immediately - no restart. Stored keys win over env. You can also change
the SiliconFlow model / base URL there.

## Run

Click **The Dawg APK Forge** in your app menu, or run `androdawg`.

Opens in **its own app window** (Brave/Chromium `--app` mode, no browser tab) -
falls back to your default browser if neither is installed. Set `DAWG_BROWSER`
to force a specific browser binary. It's **single-instance** - launching again
just focuses the running window instead of starting a second server. Use the
**quit** button (top right) to stop it. Projects and the built `.apk` land in
`~/AndroDawg/projects`.

## Flow

1. **Check the TOOLCHAIN strip** at the top - green for buildozer/java/keys/cache.
2. **SMOKE-TEST APP** - loads a built-in, guaranteed-buildable Kivy app. Build it
   once to confirm your toolchain works end to end before trusting AI output.
3. **Describe an app** -> **FORGE**. You get the `main.py` (editable), declared
   requirements/permissions, and a syntax + p4a sanity check.
4. **Refine** with the chips, or hand-edit the code.
5. **BUILD APK** -> streams the buildozer log -> **DOWNLOAD .APK**. Or
   **DOWNLOAD PROJECT (.zip)** to get the buildozer project (main.py +
   buildozer.spec) and build it yourself anywhere.

## What stops a bad build before it wastes 40 minutes

- syntax error in `main.py` -> build refused with the error
- non-Kivy GUI (tkinter/PyQt/PySide/GTK/curses) -> hard error, build blocked,
  `kivy-only fix` chip to re-forge
- requirement with no p4a recipe -> warning
- buildozer missing on PATH -> refused with how to fix

## Notes

- Only **Kivy** survives the python-for-android pipeline; generated apps are Kivy.
- First build downloads the Android SDK/NDK (~20-40 min); `~/.buildozer` caches it,
  later builds are minutes.
- `buildozer.spec` is generated from a known-good template (api 34, minapi 24,
  arm64-v8a). The AI only declares requirements/permissions/orientation; it does
  not get to hand-write the spec.
- Default arch is `arm64-v8a` (covers every modern phone, halves build time). Add
  `armeabi-v7a` in `ANDROID_ARCHS` at the top of `apkforge.py` if you need 32-bit.

## Test it yourself

`python3 selftest.py`

Runs adversarial parser cases + the full HTTP pipeline with a mocked AI and a
mocked buildozer (no keys / no toolchain needed).
