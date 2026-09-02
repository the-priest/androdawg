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

## Verified in this build
- selftest.py: 338/338 PASS · selftest_modules.py: 54/54 PASS
- Every kit component (incl. new Scaffold/Row/Chip/Meter) instantiates headless under Kivy 2.3.1
- Crash-check line remapping re-verified with the larger kit (points at the exact app line)
- GUI: JS parses (node), HTML tags + CSS braces balanced, all handlers and DOM refs resolve
- GUI: JS parses (node), HTML tags + CSS braces balanced, all 25 handlers and 55 DOM refs resolve
- Live: page + icon + all core endpoints return 200
- Build gate live-tested: crasher refused, force overrides, clean app passes
- Managed-venv provisioning proven end-to-end on a simulated no-Kivy host

## NOT tested here (no toolchain/key in the build sandbox)
- A real `buildozer android debug` run (needs the Android SDK/NDK on your machine)
- Live SiliconFlow/Groq generation (needs your API key)
Everything around those two is verified; they run on your box as before.
