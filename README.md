<p align="center">
  <img src="icon.png" alt="AndroDawg" width="132" height="132">
</p>

<h1 align="center">AndroDawg</h1>

<p align="center">
  <b>Describe an Android app in plain English. Watch it get forged, crash-tested, and fixed — then built into a real APK.</b>
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/Linux-CachyOS%20%C2%B7%20Arch%20%C2%B7%20Debian%20%C2%B7%20Fedora%20%C2%B7%20SUSE-1793d1?style=flat-square&logo=linux&logoColor=white">
  <img alt="python" src="https://img.shields.io/badge/Python-3.10%2B%20(host)-e8a33d?style=flat-square&logo=python&logoColor=white">
  <img alt="ui" src="https://img.shields.io/badge/Builds-Kivy%20%2B%20Buildozer-9fe04a?style=flat-square">
  <img alt="output" src="https://img.shields.io/badge/Output-.apk-46c7d4?style=flat-square&logo=android&logoColor=white">
  <img alt="license" src="https://img.shields.io/badge/License-MIT-c79be0?style=flat-square">
</p>

<p align="center">
  <i>AndroDawg is TheDawg's Android sibling: it forges phone apps and builds installable APKs, all locally.</i>
</p>

---

AndroDawg turns a one-line description into a working **Android app**, then does the part
that usually wastes your afternoon for you: it **actually launches the app on a hidden
display, taps every button, rotates it and lets it soak** — catching the crashes that only
show up at runtime — and only then builds the `.apk`. When the model makes a mistake, it
feeds the real traceback back and fixes it, before you ever spend 40 minutes on a build.

Everything runs **locally**. Your API key never leaves your machine.

**The Station** (v4.0) is the front door: one command box at the top of the window. Type
what you want in plain English — *"build a habit tracker with a weekly grid"* on an empty
editor, or *"add a dark mode toggle" / "make the buttons bigger" / "fix the crash on
rotate"* with an app already open — hit **Go**, and it builds or edits, then runs the whole
forge → repair → self-test → fix loop on its own. No mode-juggling.

Powered by **GLM-5.3-Flash**. It's an always-reasoning model, so AndroDawg asks for minimal
reasoning (to spend budget on code, not planning) and strips the chain-of-thought before it
can ever leak into your app — you can dial the effort up in Settings for tricky builds.

<br>

<table>
<tr>
<td width="50%" valign="top">

**🔨 You describe it**
> *"A price-compare app for record shops in Limerick — search a title, list shops with prices, save favourites."*

**👁 You watch it forge**
> Static-checked → launched on a hidden display → every button tapped → *caught 2 issues* → fixed → ✓ passed.

</td>
<td width="50%" valign="top">

**▶ You preview it**
> One click opens the real app in a phone-sized window on your desktop. No build, no phone needed.

**◆ You build it**
> A real Android `.apk`, built through an isolated toolchain — refused up front if the app would crash on the phone.

</td>
</tr>
</table>

---

## Install

From a clone (recommended while iterating):

```
curl -fsSL https://raw.githubusercontent.com/the-priest/androdawg/main/install.sh | bash
```

The installer needs **no root** — everything lands under `$HOME`. It:

- installs the system build tools (via pacman / apt / dnf / zypper) — `base-devel`, autoconf,
  libtool, pkg-config, cmake, git, zip/unzip, and a **JDK 17** for the Android build;
- sets up an **isolated Python 3.12 with Kivy** for the crash check + preview — so you never
  see "no module named kivy" again. If your system Python is too new for Kivy (CachyOS/Arch
  ship 3.14, which Kivy has no wheels for), it **downloads a self-contained CPython 3.12
  automatically** — no root, no AUR, no compiler;
- sets up an **isolated build environment** (Python 3.12 + buildozer + cython) so the APK
  build never touches — or is broken by — your system Python;
- drops an `androdawg` launcher into `~/.local/bin` and an app-menu entry.

Then just run:

```
androdawg
```

Set your API key in the app (the gear, top-right). Re-running `./install.sh` updates in place.

---

## What it does, step by step

1. **Forge** — you describe the app; the model writes a single-file Kivy app on top of a
   built-in UI kit (cards, buttons, chips, a scaffold, crash-proof storage) so it looks like
   a real app, not default-Kivy grey.
2. **Static check (instant)** — catches the whole class of Kivy mistakes before running
   anything: undefined names, wrong `Theme` attributes, `id=` passed to a widget, wrong
   `kivy.*` import paths, direct `JsonStore` misuse, unguarded android-only imports.
3. **Crash check (seconds)** — actually runs the app on a hidden display: compiles, imports,
   builds the UI, taps every button, rotates, and soaks for a moment. A real traceback here
   is a bug that *would* have crashed on your phone.
4. **Auto-fix** — sends the exact, app-relative traceback back to the model (or repairs common
   issues locally for free) and re-checks, up to a few rounds, until it passes.
5. **Preview** — opens the real app in a phone-sized window so you can try it yourself.
6. **Build** — refuses to start a ~40-minute build if the app still crashes or a build tool is
   missing (it tells you the exact packages to install). Otherwise it builds the `.apk`
   through the isolated Python 3.12 toolchain and hands you the file.

You can keep talking to the AI to change an app after it's forged — the **Improve with AI**
box edits the current app in place, it doesn't start over.

---

## Requirements

- **Linux** (first-class: CachyOS / Arch; also Debian/Ubuntu/Kali, Fedora, openSUSE).
- **Python 3.10+** to run AndroDawg itself. The Android build and crash check run on their own
  isolated Python 3.12 that the installer sets up — your system Python is never used or altered.
- An API key for a supported provider (set inside the app). Nothing is sent anywhere else.
- Disk: the **first** APK build downloads the Android SDK/NDK (~2–3 GB) and compiles the
  native libraries — budget ~5–6 GB free and 20–40 minutes. Later builds take minutes.

---

## Honest notes

- **Python 3.14 host?** Kivy 2.3.1 has no 3.14 wheels, so AndroDawg quietly runs the crash
  check, preview, and APK build on its own bundled Python 3.12. You don't have to downgrade
  anything.
- **The crash check is not a guarantee of a perfect app** — but if it passes, the app really
  did launch and every button fired without an exception. It exists so a broken app is caught
  in seconds here, not after a 40-minute build and an install on your phone.
- **What runs where:** forging and fixing use your chosen AI provider; everything else — the
  static check, crash check, preview, and build — is local.

---

## License

MIT. See [LICENSE](LICENSE).
