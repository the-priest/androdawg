#!/usr/bin/env bash
# THE DAWG // APK FORGE v3.1 - one-shot installer
# Wipes any old install, pulls everything, installs all deps + a Gradle-compatible JDK 17.
#
# v3.1 - CACHYOS / ARCH FIRST:
#   - auto-detects the package manager: pacman (CachyOS/Arch/Manjaro/EndeavourOS) is the
#     primary path; apt (Debian/Kali/Ubuntu) is kept as a fallback so this still runs there.
#   - on Arch it installs jdk17-openjdk straight from the official repos (no Temurin repo
#     dance) and xorg-server-xvfb for the headless self-test.
#   - because Arch's xvfb package ships Xvfb but NOT the xvfb-run wrapper, apkforge.py now
#     launches Xvfb itself, so the self-test works on CachyOS (incl. Wayland/KDE) regardless.
# v3 added: generated kit-API contract, zero-token local repair, kit-stripped AI calls +
#           token metering/budget, a self-test that taps every button, the forge-and-verify
#           agent loop, and a rebuilt UI.
# Run (from the repo folder):  bash install.sh
#  or:  curl -fsSL https://raw.githubusercontent.com/the-priest/androdawg/main/install.sh | bash
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
WMCLASS="AndroDawg"   # must match apkforge.py's --class so the panel shows OUR icon

SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  SRC_DIR="$(cd "$(dirname "$SELF")" && pwd)"
else
  SRC_DIR=""
fi

echo "[dawg] ===== The Dawg // APK Forge v3.1 installer ====="

# ---------------------------------------------------------------------------
# 0) detect the package manager. pacman (CachyOS/Arch) is preferred; apt is the
#    Debian/Kali fallback; dnf/zypper are best-effort. PM stays empty if none match.
# ---------------------------------------------------------------------------
PM=""
if   command -v pacman  >/dev/null 2>&1; then PM="pacman"
elif command -v apt-get >/dev/null 2>&1; then PM="apt"
elif command -v dnf     >/dev/null 2>&1; then PM="dnf"
elif command -v zypper  >/dev/null 2>&1; then PM="zypper"
fi
# only use sudo if we're not already root and sudo exists
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 && SUDO="sudo"
fi
echo "[dawg] package manager: ${PM:-none detected}   sudo: ${SUDO:-none}"

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

# ---------------------------------------------------------------------------
# 2) system build deps + JDK 17, per package manager.
#    installed one-by-one so a single bad/renamed package can't sink the batch.
# ---------------------------------------------------------------------------
jdk17_present() {
  for d in /usr/lib/jvm/java-17-openjdk* /usr/lib/jvm/temurin-17-jdk* \
           /usr/lib/jvm/*-17-* /usr/lib/jvm/*17*; do
    [ -x "$d/bin/java" ] && return 0
  done
  return 1
}

case "$PM" in
  # ------------------------------------------------ CachyOS / Arch / Manjaro / Endeavour
  pacman)
    # CachyOS ships pip as an externally-managed env, and Xvfb (not xvfb-run) via
    # xorg-server-xvfb. base-devel is the group that carries gcc/make/pkgconf/autoconf.
    PKGS="git zip unzip python python-pip base-devel autoconf libtool pkgconf \
zlib ncurses cmake libffi openssl ccache wget gnupg ca-certificates \
xorg-server-xvfb jdk17-openjdk"
    echo "[dawg] syncing pacman databases..."
    $SUDO pacman -Sy --noconfirm >/dev/null 2>&1 || true
    echo "[dawg] installing system deps via pacman..."
    for p in $PKGS; do
      $SUDO pacman -S --needed --noconfirm "$p" >/dev/null 2>&1 || echo "[dawg]   WARN: $p"
    done
    if ! jdk17_present; then
      echo "[dawg] jdk17-openjdk didn't land; retrying explicitly..."
      $SUDO pacman -S --needed --noconfirm jdk17-openjdk >/dev/null 2>&1 || true
    fi
    ;;

  # ------------------------------------------------ Debian / Kali / Ubuntu
  apt)
    SYS="git zip unzip python3 python3-pip python3-venv autoconf libtool pkg-config \
zlib1g-dev libncurses-dev cmake libffi-dev libssl-dev build-essential ccache \
wget gnupg ca-certificates apt-transport-https xvfb"
    echo "[dawg] installing system deps (apt)..."
    $SUDO apt-get update -y || true
    for p in $SYS; do $SUDO apt-get install -y "$p" >/dev/null 2>&1 || echo "[dawg]   WARN: $p"; done

    # JDK 17 - Kali ships no openjdk-17, and its default JDK (21/25) breaks buildozer's
    # bundled Gradle. Try Debian openjdk-17, then Temurin apt repo, then a tarball.
    $SUDO rm -f /etc/apt/sources.list.d/adoptium.list /etc/apt/keyrings/adoptium.gpg 2>/dev/null || true
    if ! jdk17_present; then
      echo "[dawg] trying openjdk-17..."
      $SUDO apt-get install -y openjdk-17-jdk >/dev/null 2>&1 || true
    fi
    if ! jdk17_present; then
      echo "[dawg] installing Temurin 17 via apt..."
      $SUDO install -d -m 0755 /etc/apt/keyrings 2>/dev/null || true
      wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public 2>/dev/null \
        | $SUDO gpg --dearmor -o /etc/apt/keyrings/adoptium.gpg 2>/dev/null || true
      echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb bookworm main" \
        | $SUDO tee /etc/apt/sources.list.d/adoptium.list >/dev/null 2>&1 || true
      $SUDO apt-get update -y >/dev/null 2>&1 || true
      $SUDO apt-get install -y temurin-17-jdk >/dev/null 2>&1 || true
    fi
    if ! jdk17_present; then
      echo "[dawg] apt route failed, fetching Temurin 17 tarball..."
      $SUDO rm -f /etc/apt/sources.list.d/adoptium.list 2>/dev/null || true
      $SUDO apt-get update -y >/dev/null 2>&1 || true
      $SUDO mkdir -p /usr/lib/jvm/temurin-17-jdk-amd64
      if wget -qO /tmp/dawg-jdk17.tgz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"; then
        $SUDO tar -xzf /tmp/dawg-jdk17.tgz -C /usr/lib/jvm/temurin-17-jdk-amd64 --strip-components=1 2>/dev/null || true
        rm -f /tmp/dawg-jdk17.tgz
      fi
    fi
    ;;

  # ------------------------------------------------ Fedora / RHEL
  dnf)
    PKGS="git zip unzip python3 python3-pip autoconf libtool pkgconf-pkg-config \
zlib-devel ncurses-devel cmake libffi-devel openssl-devel @development-tools ccache \
wget gnupg2 ca-certificates xorg-x11-server-Xvfb java-17-openjdk-devel"
    echo "[dawg] installing system deps (dnf)..."
    for p in $PKGS; do $SUDO dnf install -y "$p" >/dev/null 2>&1 || echo "[dawg]   WARN: $p"; done
    ;;

  # ------------------------------------------------ openSUSE
  zypper)
    PKGS="git zip unzip python3 python3-pip autoconf libtool pkg-config \
zlib-devel ncurses-devel cmake libffi-devel libopenssl-devel gcc gcc-c++ make ccache \
wget gpg2 ca-certificates xorg-x11-server-Xvfb java-17-openjdk-devel"
    echo "[dawg] installing system deps (zypper)..."
    for p in $PKGS; do $SUDO zypper --non-interactive install "$p" >/dev/null 2>&1 || echo "[dawg]   WARN: $p"; done
    ;;

  *)
    echo "[dawg] no supported package manager found - install these yourself:"
    echo "[dawg]   git zip unzip python3 python3-pip a C toolchain, cmake, libffi, openssl,"
    echo "[dawg]   ccache, Xvfb, and a JDK 17 (must be 17-24; NOT 25+)."
    ;;
esac

# locate a Gradle-compatible JDK (17 preferred) to pin JAVA_HOME in the launcher
JAVA17=""
for d in /usr/lib/jvm/java-17-openjdk* /usr/lib/jvm/temurin-17-jdk* \
         /usr/lib/jvm/*-17-* /usr/lib/jvm/*17*; do
  [ -x "$d/bin/java" ] && JAVA17="$d" && break
done

# 3) buildozer + cython into the USER site (NOT a venv: p4a does `pip install --user`)
echo "[dawg] installing buildozer + cython..."
PYBIN="$(command -v python3 || command -v python)"
"$PYBIN" -m pip install --user --break-system-packages --upgrade pip wheel >/dev/null 2>&1 || true
"$PYBIN" -m pip install --user --break-system-packages "cython==0.29.36" buildozer \
  || echo "[dawg]   ERROR: buildozer pip install failed"

# 3b) host Kivy for the headless self-test (OPTIONAL - never fails the install).
echo "[dawg] (optional) installing host Kivy for the self-test..."
"$PYBIN" -m pip install --user --break-system-packages "kivy" >/dev/null 2>&1 \
  && echo "[dawg]   host Kivy ready -> the self-test will catch launch crashes" \
  || echo "[dawg]   host Kivy not installed (self-test will be skipped; builds still work)"

# 4) fetch app + icon from the local checkout, or GitHub as a fallback
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
  echo "exec \"$PYBIN\" \"$APP_DIR/apkforge.py\" \"\$@\""
} > "$BIN/androdawg"
chmod +x "$BIN/androdawg"

# 6) icon + desktop entry
# StartupWMClass MUST equal the --class apkforge.py passes to the browser window so the
# panel shows THE DAWG's own icon/name instead of folding it into Brave/Chromium.
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
StartupWMClass=$WMCLASS
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
  echo "[dawg] !!! WARNING: no JDK 17 found. The APK build will reach the Gradle step and"
  echo "[dawg] !!! FAIL on a newer JDK. Install it:"
  case "$PM" in
    pacman) echo "[dawg] !!!   sudo pacman -S jdk17-openjdk" ;;
    apt)    echo "[dawg] !!!   sudo apt install -y openjdk-17-jdk" ;;
    dnf)    echo "[dawg] !!!   sudo dnf install -y java-17-openjdk-devel" ;;
    zypper) echo "[dawg] !!!   sudo zypper install java-17-openjdk-devel" ;;
    *)      echo "[dawg] !!!   install a JDK 17 (must be 17-24, not 25+)" ;;
  esac
fi
case ":$PATH:" in *":$BIN:"*) : ;; *) echo "[dawg] add to PATH:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;; esac
echo "[dawg] Launch 'The Dawg APK Forge' from your menu, or run:  androdawg"
echo "[dawg] First run: open Settings (gear), paste your SiliconFlow key."
echo "[dawg] Build the SMOKE-TEST APP first to verify the toolchain."
echo "[dawg] Tip: forge an app, hit TEST RUN to catch crashes, POLISH to glam it, then BUILD APK."
