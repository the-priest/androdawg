# The Dawg / APK Forge — v3.1

Focus: stop shipping APKs that don't boot or have dead buttons, and make Auto-fix
actually know what's wrong. Plus a green-forward "Dawg" GUI refresh.

## The core problem in v3.0
The pre-build crash check ("launch the app, tap every button") only ran if desktop
Kivy happened to be installed. If it wasn't, the check SILENTLY became static-analysis
only — which cannot catch a button that crashes on tap or an app that won't boot — and
the build proceeded anyway. So you'd wait ~20 minutes for an APK that fails on the phone.

## What changed

### Reliability
- **Managed crash-check environment.** The Dawg now provisions its own isolated Kivy in
  a venv (~/.androdawg/testenv) on request, so the "does it actually launch and survive
  taps" check works on any machine without touching your system packages. One-time setup,
  automatic after that.
- **Hard build gate.** Build now LAUNCHES the app and taps every button before it spends
  20 minutes compiling. A crasher is refused (409) with the failing phase named; you can
  Auto-fix or explicitly "Build anyway". Clean apps pass straight through.
- **No more silent skip.** If the crash check isn't set up, you get a loud banner + a
  one-click "Enable crash check" instead of a quiet downgrade to static-only.
- **The checker never accuses the app.** If the crash check itself can't run (env quirk),
  that's now reported as "couldn't verify" and never blocks a build or triggers a bogus fix.

### Auto-fix actually knows what to fix
- Clicking Auto-fix now RUNS the app itself, captures the real traceback, and fixes that —
  instead of bailing with "run Self-test first".
- Traceback line numbers are remapped to match the code the model is actually holding
  (the kit is stripped before sending), and the offending source line is quoted inline.
  v3.0 told the model to subtract a ~330-line offset in its head — and the offset it named
  was even 2 lines off. Fixes now land on the right line.

### App quality (every generated app gets nicer, automatically)
- **Richer UI kit.** Softer 3-layer card shadows and a smoother 64-strip gradient, plus four
  new components every app can use: **Scaffold** (one call = gradient bg + AppBar + smooth
  scrolling content — the fast route to a designed-looking screen), **Row**, **Chip**, and
  **Meter** (progress bar). The generation prompt now steers apps toward Scaffold + Chips +
  Meter, so forged apps look like real Play-store apps, not default-Kivy grey.
- Every kit component is instantiated headless as part of validation, so a broken kit can't ship.
- Note: the progress bar is named **Meter**, not ProgressBar, on purpose — Kivy auto-applies
  its own built-in style rule to any class literally named ProgressBar, which would crash it.
- The prompt's component reference is still generated from the kit's AST, so the new
  components are advertised to the model automatically with their real constructor args.

### install.sh
- Host Kivy for the crash check is now pinned to 2.3.1 (matches what the check runs against),
  with clearer messaging that points at in-app "Enable crash check" if it's skipped.

### GUI
- Green Android "Dawg" identity throughout; real dog icon in the header + a proper wordmark.
- Crash-check status banner, "Enable crash check" action in the doctor, clearer build/fix
  toasts that tell you where a fix came from (real test run vs static analysis).

## Improve-with-AI (this build)
- You can now iterate on a forged app conversationally, from the workspace. Before, the only
  way to change an app after the first forge was to scroll back up, wipe the "Describe the
  app" box and re-forge -- which felt like starting over, so it looked like you couldn't talk
  to the AI at all. There's now an **Improve with AI** box right above the build actions:
  type what to change ("add a reset button", "save history between launches") and it edits the
  CURRENT app in place (the model gets the existing code as context), plus quick chips.
- Follow-up failures are now shown persistently (bad key, provider down, or the model
  replying in prose instead of code) instead of a toast that flashes and vanishes -- and your
  current app is left untouched so nothing is lost.

## Isolated build Python -- the real APK-build fix (this build)
- The whole Android build (buildozer -> python-for-android) used to run under the system
  Python. On Arch/CachyOS that's 3.14, and p4a's build venv crashed with
  "cannot import name BuildDependencyInstallError from pip._internal" -- and p4a was even
  downloading CPython 3.14 to build FOR Android (unsupported). Builds now run through an
  isolated CPython 3.12 with PATH prefixed so every bare python/pip/cython p4a shells out to
  is 3.12 too. Your system Python is never touched.
- That interpreter is the standalone CPython's OWN prefix (~/.androdawg/python), NOT a venv:
  buildozer hardcodes `pip install --user` for p4a's deps, and pip refuses `--user` inside a
  venv ("User site-packages are not visible in this virtualenv"). A standalone prefix has a
  normal user site, so --user works. (It's the same interpreter the crash check already
  fetched, so there's usually nothing new to download.)
- If a project was previously built under a different interpreter, the per-arch build dir is
  cleared once for a clean rebuild (the multi-GB SDK/NDK caches in ~/.buildozer are kept).
- Verified: buildozer 1.6.0 runs on the isolated 3.12, cython is on its bin dir, and both
  the earlier venv-pip crash AND buildozer's `pip install --user <p4a deps>` step now succeed.

## Self-sufficient install / auto Python (this build)
- The installer now sets up the crash-check environment itself, and if the host Python is too
  new for Kivy (Arch/CachyOS ship 3.14, which Kivy 2.3.1 has no wheels for), it **downloads a
  self-contained CPython 3.12** from python-build-standalone -- no root, no AUR, no compiler --
  and builds the isolated Kivy env from it. So Preview + the pre-build crash check work
  straight after `install.sh` with zero manual Python juggling.
- Same fallback drives the in-app "Enable crash check" button and `apkforge.py
  --provision-testenv` (which install.sh calls). Verified end-to-end on a simulated 3.14-only
  host: detects no compatible Python -> downloads 3.12.8 -> venv -> Kivy 2.3.1 -> ready.
- Fixed the earlier bad advice: `sudo pacman -S python312` doesn't exist in Arch's official
  repos; the messaging now points at the auto-download and, as a manual fallback,
  `sudo pacman -S uv && uv python install 3.12`.

## Verified in this build
- selftest.py: 338/338 PASS · selftest_modules.py: 54/54 PASS
- Preview fix verified: correct message on Python 3.14 hosts, no-run apps, and hard-exit;
  a good app still renders in the phone frame and exits 0
- Managed crash-check env builds against a Kivy-compatible Python (proven end-to-end)
- Every kit component (incl. new Scaffold/Row/Chip/Meter) instantiates headless under Kivy 2.3.1
- GUI: JS parses (node), HTML tags + CSS braces balanced, all handlers and DOM refs resolve

## Preview / Python 3.14 fix (this build)
- The preview no longer blames your code when Kivy can't start. Kivy 2.3.1 ships no wheels
  for Python 3.14+, so on a bleeding-edge host (CachyOS/Arch default to the newest Python)
  its window provider fails during import and hard-exits -- the old preview caught that
  silently and printed "you never called run()". It now detects whether your source actually
  calls run() and, when Kivy failed to start, says so plainly with the real cause and fix.
- The crash-check environment now builds against a Kivy-compatible interpreter (CPython
  3.9-3.13), searching PATH for python3.13..3.10 when the host default is too new. So on a
  Python 3.14 box, "Enable crash check" sets up an isolated 3.12/3.13 env and Preview +
  self-test work without downgrading your system Python.
- test_python() no longer trusts a host interpreter on 3.14+ even if `kivy` imports there,
  because its providers crash at runtime -- it falls through so the UI offers the fix.
- GUI: JS parses (node), HTML tags + CSS braces balanced, all 25 handlers and 55 DOM refs resolve
- Live: page + icon + all core endpoints return 200
- Build gate live-tested: crasher refused, force overrides, clean app passes
- Managed-venv provisioning proven end-to-end on a simulated no-Kivy host

## NOT tested here (no toolchain/key in the build sandbox)
- A real `buildozer android debug` run (needs the Android SDK/NDK on your machine)
- Live SiliconFlow/Groq generation (needs your API key)
Everything around those two is verified; they run on your box as before.
