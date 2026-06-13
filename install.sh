#!/usr/bin/env bash
# THE DAWG // APK FORGE - installer
# Sole supported install: curl -fsSL <raw>/install.sh | bash
set -u

# ---- change this if your repo name differs --------------------------------
REPO="the-priest/androdawg"
BRANCH="main"
# ---------------------------------------------------------------------------

RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
APP_DIR="$HOME/.androdawg"
BIN="$HOME/.local/bin"
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  SRC_DIR="$(cd "$(dirname "$SELF")" && pwd)"
else
  SRC_DIR=""   # piped via curl|bash -> no local checkout, fetch from GitHub
fi

echo "[dawg] installing The Dawg // APK Forge"
mkdir -p "$APP_DIR" "$BIN"

# 1) system build deps (best-effort; needs sudo)
# non-java deps install individually so one missing/renamed package can't sink the rest
SYS_COMMON="git zip unzip python3 python3-pip python3-venv autoconf libtool pkg-config zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev build-essential ccache"
# JDK name differs across distros/Kali rolling -- first one that installs wins
JAVA_PREF="openjdk-17-jdk openjdk-21-jdk default-jdk openjdk-jdk"
if command -v apt-get >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    echo "[dawg] installing system deps via apt (sudo)..."
    sudo apt-get update -y || true
    for p in $SYS_COMMON; do
      sudo apt-get install -y "$p" >/dev/null 2>&1 || echo "[dawg] WARN: could not install $p"
    done
    jdk_ok=0
    for j in $JAVA_PREF; do
      if sudo apt-get install -y "$j" >/dev/null 2>&1; then
        echo "[dawg] JDK installed: $j"; jdk_ok=1; break
      fi
    done
    [ "$jdk_ok" = "1" ] || echo "[dawg] WARN: no JDK installed -- install one of: $JAVA_PREF"
  else
    echo "[dawg] no sudo - install yourself: $SYS_COMMON + a JDK ($JAVA_PREF)"
  fi
else
  echo "[dawg] non-apt distro - ensure equivalents are installed: $SYS_COMMON + a JDK"
fi

# 2) buildozer + cython into the USER site (NOT a venv).
# python-for-android runs `pip install --user` for its host build tools, which
# fails inside any virtualenv ("user site-packages are not visible"). So we install
# to ~/.local and let pip write to the externally-managed (Kali/PEP 668) env.
echo "[dawg] installing buildozer + cython (pinned) into ~/.local ..."
python3 -m pip install --user --break-system-packages --upgrade pip wheel >/dev/null 2>&1 || true
python3 -m pip install --user --break-system-packages "cython==0.29.36" buildozer

# 3) fetch the forge (local copy if running from the cloned repo, else download)
if [ -f "$SRC_DIR/apkforge.py" ]; then
  echo "[dawg] using local apkforge.py"
  cp "$SRC_DIR/apkforge.py" "$APP_DIR/apkforge.py"
else
  echo "[dawg] downloading apkforge.py ..."
  curl -fsSL "$RAW/apkforge.py" -o "$APP_DIR/apkforge.py"
fi

# 4) launcher: user-site bin on PATH so 'buildozer' resolves under system python
# (user site visible), and let p4a's pip installs write to the Kali/PEP 668 env.
cat > "$BIN/androdawg" <<EOF
#!/usr/bin/env bash
export PATH="\$HOME/.local/bin:\$PATH"
export PIP_BREAK_SYSTEM_PACKAGES=1
exec python3 "$APP_DIR/apkforge.py" "\$@"
EOF
chmod +x "$BIN/androdawg"

# 5) icon + desktop entry (clickable launcher in the app menu)
echo "[dawg] installing icon + desktop entry ..."
ICON_DST="$APP_DIR/icon.png"
if [ -f "$SRC_DIR/icon.png" ]; then
  cp "$SRC_DIR/icon.png" "$ICON_DST"
else
  curl -fsSL "$RAW/icon.png" -o "$ICON_DST" || echo "[dawg] WARN: could not fetch icon.png"
fi
HIC="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$HIC" && cp "$ICON_DST" "$HIC/androdawg.png" 2>/dev/null || true

APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/androdawg.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=The Dawg APK Forge
GenericName=Android App Forge
Comment=Forge Android apps and build APKs with AI
Exec=$BIN/androdawg
Icon=$ICON_DST
Terminal=false
Categories=Development;Building;Utility;
Keywords=android;apk;kivy;buildozer;ai;forge;
StartupNotify=true
EOF
chmod +x "$APPS/androdawg.desktop"
update-desktop-database "$APPS" >/dev/null 2>&1 || true
gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
kbuildsycoca6 >/dev/null 2>&1 || kbuildsycoca5 >/dev/null 2>&1 || true

echo
echo "[dawg] installed."
echo
case ":$PATH:" in
  *":$BIN:"*) : ;;
  *) echo "  NOTE: add ~/.local/bin to PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo "  Launch it from your app menu: 'The Dawg APK Forge'  (or run: androdawg)"
echo "  Set your SiliconFlow key in the in-app Settings (gear) the first time."
echo "  Projects + .apk land in ~/AndroDawg/projects"
