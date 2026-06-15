#!/usr/bin/env bash
# THE DAWG // APK FORGE - one-shot installer
# Wipes any old install, pulls everything from GitHub, installs all deps + JDK 17.
# Run:  curl -fsSL https://raw.githubusercontent.com/the-priest/androdawg/main/install.sh | bash
set -u

# ---- change this if your repo name differs --------------------------------
REPO="the-priest/androdawg"
BRANCH="main"
# ---------------------------------------------------------------------------
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
APP_DIR="$HOME/.androdawg"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
HIC="$HOME/.local/share/icons/hicolor/256x256/apps"

SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  SRC_DIR="$(cd "$(dirname "$SELF")" && pwd)"
else
  SRC_DIR=""
fi

echo "[dawg] ===== The Dawg // APK Forge installer ====="

# 1) clean old install (keep saved API keys), then recreate
echo "[dawg] removing old install (keeping your saved settings)..."
[ -f "$APP_DIR/config.json" ] && cp "$APP_DIR/config.json" "/tmp/androdawg_config.bak" 2>/dev/null || true
case "$APP_DIR" in
  */.androdawg) rm -rf "$APP_DIR" ;;
  *) echo "[dawg] refusing to delete '$APP_DIR'" ;;
esac
rm -f "$BIN/androdawg" "$APPS/androdawg.desktop" "$HIC/androdawg.png" 2>/dev/null || true
mkdir -p "$APP_DIR" "$BIN" "$APPS" "$HIC"
if [ -f "/tmp/androdawg_config.bak" ]; then
  mv "/tmp/androdawg_config.bak" "$APP_DIR/config.json"
  chmod 600 "$APP_DIR/config.json" 2>/dev/null || true
  echo "[dawg] restored your saved keys/settings"
fi

# 2) system build deps (per-package so one bad name can't sink the batch)
SYS="git zip unzip python3 python3-pip python3-venv autoconf libtool pkg-config \
zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev build-essential ccache \
wget gnupg ca-certificates apt-transport-https"
if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
  echo "[dawg] installing system deps (sudo)..."
  sudo apt-get update -y || true
  for p in $SYS; do sudo apt-get install -y "$p" >/dev/null 2>&1 || echo "[dawg]   WARN: $p"; done

  # 2b) JDK 17 — REQUIRED. Kali ships no openjdk-17, and its default JDK (21/25) breaks
  # buildozer's bundled Gradle ("Unsupported class file major version"). Get Temurin 17
  # by apt, else by tarball. Always lands in /usr/lib/jvm so the app auto-detects it.
  jdk17_present() { ls -d /usr/lib/jvm/temurin-17-jdk* /usr/lib/jvm/java-17-openjdk* >/dev/null 2>&1; }
  # heal any broken adoptium repo a previous run may have written (wrong suite -> 404)
  sudo rm -f /etc/apt/sources.list.d/adoptium.list /etc/apt/keyrings/adoptium.gpg 2>/dev/null || true
  if jdk17_present; then
    echo "[dawg] JDK 17 already installed"
  else
    echo "[dawg] trying Debian openjdk-17 (usually absent on Kali)..."
    sudo apt-get install -y openjdk-17-jdk >/dev/null 2>&1 || true
  fi
  if ! jdk17_present; then
    echo "[dawg] installing Temurin 17 via apt..."
    sudo install -d -m 0755 /etc/apt/keyrings 2>/dev/null || true
    wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public 2>/dev/null \
      | sudo gpg --dearmor -o /etc/apt/keyrings/adoptium.gpg 2>/dev/null || true
    # Adoptium publishes no 'kali-rolling' suite; bookworm debs are self-contained and work on Kali.
    echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb bookworm main" \
      | sudo tee /etc/apt/sources.list.d/adoptium.list >/dev/null 2>&1 || true
    sudo apt-get update -y >/dev/null 2>&1 || true
    sudo apt-get install -y temurin-17-jdk >/dev/null 2>&1 || true
  fi
  if ! jdk17_present; then
    echo "[dawg] apt route failed, fetching Temurin 17 tarball (no repo needed)..."
    sudo rm -f /etc/apt/sources.list.d/adoptium.list 2>/dev/null || true  # don't leave a broken repo
    sudo apt-get update -y >/dev/null 2>&1 || true
    sudo mkdir -p /usr/lib/jvm/temurin-17-jdk-amd64
    if wget -qO /tmp/dawg-jdk17.tgz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"; then
      sudo tar -xzf /tmp/dawg-jdk17.tgz -C /usr/lib/jvm/temurin-17-jdk-amd64 --strip-components=1 2>/dev/null || true
      rm -f /tmp/dawg-jdk17.tgz
    fi
  fi
  if ! jdk17_present; then
    echo "[dawg]   WARN: could not install JDK 17 automatically."
  fi
else
  echo "[dawg] no apt/sudo - install build deps + a JDK 17 yourself"
fi

# locate JDK 17 (used to pin JAVA_HOME in the launcher)
JAVA17=""
for d in /usr/lib/jvm/temurin-17-jdk* /usr/lib/jvm/java-17-openjdk*; do
  [ -d "$d" ] && JAVA17="$d" && break
done

# 3) buildozer + cython into the USER site (NOT a venv: p4a does `pip install --user`)
echo "[dawg] installing buildozer + cython..."
python3 -m pip install --user --break-system-packages --upgrade pip wheel >/dev/null 2>&1 || true
python3 -m pip install --user --break-system-packages "cython==0.29.36" buildozer \
  || echo "[dawg]   ERROR: buildozer pip install failed"

# 4) fetch app + icon from GitHub (or local checkout)
echo "[dawg] fetching app + icon..."
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/apkforge.py" ]; then
  cp "$SRC_DIR/apkforge.py" "$APP_DIR/apkforge.py"
else
  curl -fsSL "$RAW/apkforge.py" -o "$APP_DIR/apkforge.py" || { echo "[dawg] ERROR: could not download apkforge.py"; exit 1; }
fi
if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/icon.png" ]; then
  cp "$SRC_DIR/icon.png" "$APP_DIR/icon.png"
else
  curl -fsSL "$RAW/icon.png" -o "$APP_DIR/icon.png" || echo "[dawg]   WARN: could not download icon.png"
fi

# 5) launcher (pins JDK 17 if found, user-site on PATH, PEP 668 bypass)
{
  echo '#!/usr/bin/env bash'
  if [ -n "$JAVA17" ]; then
    echo "export JAVA_HOME=\"$JAVA17\""
    echo 'export PATH="$JAVA_HOME/bin:$PATH"'
  fi
  echo 'export PATH="$HOME/.local/bin:$PATH"'
  echo 'export PIP_BREAK_SYSTEM_PACKAGES=1'
  echo "exec python3 \"$APP_DIR/apkforge.py\" \"\$@\""
} > "$BIN/androdawg"
chmod +x "$BIN/androdawg"

# 6) icon + desktop entry
[ -f "$APP_DIR/icon.png" ] && cp "$APP_DIR/icon.png" "$HIC/androdawg.png" 2>/dev/null || true
cat > "$APPS/androdawg.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=The Dawg APK Forge
GenericName=Android App Forge
Comment=Forge Android apps and build APKs with AI
Exec=$BIN/androdawg
Icon=$APP_DIR/icon.png
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
echo "[dawg] ===== done ====="
if [ -n "$JAVA17" ]; then
  echo "[dawg] JDK 17 -> $JAVA17"
  "$JAVA17/bin/java" -version 2>&1 | head -n1 | sed 's/^/[dawg]   /'
else
  echo "[dawg] !!! WARNING: no JDK 17 found. The APK build will reach the Gradle step"
  echo "[dawg] !!! and FAIL on a newer JDK. Install it:  sudo apt install -y openjdk-17-jdk"
fi
case ":$PATH:" in *":$BIN:"*) : ;; *) echo "[dawg] add to PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;; esac
echo "[dawg] Launch 'The Dawg APK Forge' from your menu, or run:  androdawg"
echo "[dawg] First run: open Settings (gear), paste your SiliconFlow key."
echo "[dawg] Build the SMOKE-TEST APP first to verify the toolchain."
