#!/usr/bin/env bash
# Compa one-command installer.
#
# Turns a fresh Raspberry Pi OS Lite (64-bit) into a Compa appliance:
#   - Installs all system + Python dependencies
#   - Clones the repo to /home/pi/compa (or pulls latest if already there)
#   - Sets up the Python venv + pip packages
#   - Installs fonts, udev rules, systemd autostart service
#   - Configures GPU memory split, screen blanking, audio settings
#
# Idempotent — re-running the script updates the existing install
# in place. Used the same way by Path A (manual install on top of
# Pi OS) and Path B (the Compa OS image's pi-gen build stage).
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/macdigi/compa/main/setup/install.sh | sudo bash
#
# Or after manually cloning the repo:
#   sudo bash setup/install.sh

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────
COMPA_REPO="${COMPA_REPO:-https://github.com/macdigi/compa.git}"
COMPA_BRANCH="${COMPA_BRANCH:-main}"
COMPA_USER="${COMPA_USER:-pi}"
COMPA_DIR="${COMPA_DIR:-/home/${COMPA_USER}/compa}"
COMPA_SOURCE_DIR="${COMPA_SOURCE_DIR:-}"

# Pinned upstream rtpmidid release — powers the Network MIDI toggle.
# Not in apt, so we fetch the prebuilt .deb from davidmoreno/rtpmidid.
# Bump together with checking dependency compatibility against the
# Pi OS Lite base (currently Debian trixie / arm64).
RTPMIDID_VERSION="${RTPMIDID_VERSION:-26.01}"

# Chroot mode — when set to 1 we skip every "live" systemd action
# (start/restart, udevadm trigger, systemctl is-active checks).
# Used by pi-gen during the Compa OS image build, where there's no
# running init to talk to. Service is still enabled so it starts on
# first real boot. Detected automatically if /proc/1/comm isn't
# systemd, but can also be forced via env var.
COMPA_IN_CHROOT="${COMPA_IN_CHROOT:-}"
if [[ -z "$COMPA_IN_CHROOT" ]]; then
    if [[ ! -d /run/systemd/system ]] || \
       ! grep -qs '^systemd' /proc/1/comm 2>/dev/null; then
        COMPA_IN_CHROOT=1
    else
        COMPA_IN_CHROOT=0
    fi
fi

# ── Helpers ─────────────────────────────────────────────────────────
log()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── Pre-flight ──────────────────────────────────────────────────────
[[ "$(id -u)" -eq 0 ]] || die "Must run as root. Try: sudo bash $0"

if ! id -u "$COMPA_USER" >/dev/null 2>&1; then
    die "User '${COMPA_USER}' does not exist. Create it first or set COMPA_USER=<name>."
fi

if [[ ! -e /proc/device-tree/model ]] || \
   ! grep -qi 'raspberry pi' /proc/device-tree/model 2>/dev/null; then
    warn "This doesn't look like a Raspberry Pi. Continuing anyway."
fi

# ── Phase 1: System packages ────────────────────────────────────────
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    python3-numpy \
    python3-pygame \
    python3-rtmidi \
    python3-evdev \
    libsdl2-2.0-0 \
    libsdl2-mixer-2.0-0 \
    libsdl2-image-2.0-0 \
    libsdl2-ttf-2.0-0 \
    libegl1 \
    libgles2 \
    libegl-mesa0 \
    libportaudio2 \
    libsndfile1 \
    libusb-1.0-0 \
    libasound2-dev \
    pkg-config \
    ffmpeg \
    exfatprogs \
    libts-bin \
    sshfs \
    samba \
    avahi-daemon \
    git \
    fonts-dejavu-core \
    >/dev/null
ok "System packages installed"

# ── Phase 1b: rtpmidid (Network MIDI daemon) ────────────────────────
# Backs the Network MIDI toggle in Compa Settings — shares every USB
# MIDI controller plugged into Compa as a Bonjour-advertised RTP-MIDI
# session, so a Mac on the same LAN sees them in Audio MIDI Setup →
# Network. Not in Debian's apt repo, so we install the upstream .deb
# from davidmoreno/rtpmidid pinned to ${RTPMIDID_VERSION}. The .deb's
# postinst enables rtpmidid.service automatically (symlink for chroot
# builds, normal systemctl enable on live installs).
log "Installing rtpmidid (Network MIDI daemon)"
if dpkg -s rtpmidid >/dev/null 2>&1; then
    ok "rtpmidid already installed"
else
    RTPMIDID_DEB_URL="https://github.com/davidmoreno/rtpmidid/releases/download/v${RTPMIDID_VERSION}/rtpmidid-debian-trixie-arm64-${RTPMIDID_VERSION}.deb"
    RTPMIDID_DEB_PATH="/tmp/rtpmidid-${RTPMIDID_VERSION}.deb"
    # apt-get install of a local .deb resolves dependencies the same
    # way as a repo package — much safer than raw `dpkg -i`, which
    # leaves the system half-installed if a dep is missing.
    if curl -fsSL --max-time 60 -o "$RTPMIDID_DEB_PATH" "$RTPMIDID_DEB_URL"; then
        if apt-get install -y --no-install-recommends "$RTPMIDID_DEB_PATH" >/dev/null 2>&1; then
            ok "rtpmidid v${RTPMIDID_VERSION} installed (Network MIDI ready)"
        else
            warn "rtpmidid .deb downloaded but install failed — Network MIDI toggle will stay disabled until you re-run install.sh"
        fi
        rm -f "$RTPMIDID_DEB_PATH"
    else
        warn "Could not fetch rtpmidid v${RTPMIDID_VERSION} — Network MIDI toggle will stay disabled until you re-run install.sh"
    fi
fi

# ── Phase 2: Repo clone or pull ─────────────────────────────────────
log "Setting up repository at ${COMPA_DIR}"
if [[ -n "${COMPA_SOURCE_DIR}" && -d "${COMPA_SOURCE_DIR}" ]]; then
    rm -rf "${COMPA_DIR}"
    mkdir -p "${COMPA_DIR}"
    cp -a "${COMPA_SOURCE_DIR}/." "${COMPA_DIR}/"
    rm -rf "${COMPA_DIR}/venv"
    ok "Copied staged source tree → ${COMPA_DIR}"

    # Ensure /home/pi/compa is a real git checkout so Settings → Updates
    # works after first boot. If the staged source already included .git
    # we keep it; otherwise we bootstrap one pointing at origin.
    if [[ ! -d "${COMPA_DIR}/.git" ]]; then
        log "Bootstrapping git checkout for in-app updates"
        sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" init -q -b "${COMPA_BRANCH}"
        sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" remote add origin "${COMPA_REPO}"
        if sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" fetch --quiet --depth 50 origin "${COMPA_BRANCH}" 2>/dev/null; then
            sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" reset --hard "FETCH_HEAD" >/dev/null
            sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" branch --set-upstream-to="origin/${COMPA_BRANCH}" "${COMPA_BRANCH}" 2>/dev/null || true
            ok "git checkout aligned with origin/${COMPA_BRANCH}"
        else
            warn "Could not fetch ${COMPA_REPO} during install — Settings → Updates may need a manual 'git fetch' on first run"
        fi
    fi
elif [[ -d "${COMPA_DIR}/.git" ]]; then
    sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" fetch --quiet origin
    sudo -u "${COMPA_USER}" git -C "${COMPA_DIR}" reset --hard "origin/${COMPA_BRANCH}" >/dev/null
    ok "Updated existing checkout to origin/${COMPA_BRANCH}"
else
    rm -rf "${COMPA_DIR}"
    sudo -u "${COMPA_USER}" git clone --branch "${COMPA_BRANCH}" --depth 1 \
        "${COMPA_REPO}" "${COMPA_DIR}" >/dev/null
    ok "Cloned ${COMPA_REPO} → ${COMPA_DIR}"
fi
chown -R "${COMPA_USER}:${COMPA_USER}" "${COMPA_DIR}"

# ── Phase 3: Python venv + pip packages ─────────────────────────────
log "Creating Python virtual environment"
if [[ ! -x "${COMPA_DIR}/venv/bin/python" ]]; then
    sudo -u "${COMPA_USER}" python3 -m venv \
        "${COMPA_DIR}/venv" --system-site-packages
    ok "Created venv at ${COMPA_DIR}/venv"
else
    ok "Existing venv kept"
fi

log "Installing Python packages (this can take a few minutes)"
sudo -u "${COMPA_USER}" "${COMPA_DIR}/venv/bin/pip" install \
    --quiet --disable-pip-version-check --upgrade pip
sudo -u "${COMPA_USER}" "${COMPA_DIR}/venv/bin/pip" install \
    --quiet --disable-pip-version-check \
    -r "${COMPA_DIR}/requirements.txt"
ok "Python packages installed"

# ── Phase 4: Fonts ──────────────────────────────────────────────────
log "Installing bundled fonts"
mkdir -p /usr/local/share/fonts/compa
if compgen -G "${COMPA_DIR}/docs/fonts/*.ttf" > /dev/null; then
    cp "${COMPA_DIR}"/docs/fonts/*.ttf /usr/local/share/fonts/compa/
    fc-cache -f >/dev/null
    ok "Fonts installed and cache rebuilt"
else
    warn "No bundled fonts found in docs/fonts/ — using system fonts only"
fi

# ── Phase 5: Pi config (GPU split, no blanking, audio) ──────────────
log "Configuring Pi boot/runtime settings"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f "$CONFIG_TXT" ]] || CONFIG_TXT=/boot/config.txt
CMDLINE_TXT=/boot/firmware/cmdline.txt
[[ -f "$CMDLINE_TXT" ]] || CMDLINE_TXT=/boot/cmdline.txt

# GPU memory split — 64 MB is plenty for the touchscreen UI
if ! grep -q "^gpu_mem=" "$CONFIG_TXT"; then
    echo "gpu_mem=64" >> "$CONFIG_TXT"
    ok "GPU memory split set to 64 MB"
fi

# Disable onboard audio to free a card slot for the USB devices
if grep -q "^dtparam=audio=on" "$CONFIG_TXT"; then
    sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$CONFIG_TXT"
    ok "Onboard audio disabled (USB devices take priority)"
fi

# Disable console screen blanking so the touchscreen never goes dark
if [[ -f "$CMDLINE_TXT" ]] && ! grep -q "consoleblank=0" "$CMDLINE_TXT"; then
    sed -i 's/$/ consoleblank=0/' "$CMDLINE_TXT"
    ok "Screen blanking disabled"
fi

# ── Phase 6: Audio realtime priority ────────────────────────────────
log "Configuring audio realtime priority"
cat > /etc/security/limits.d/audio.conf <<'EOF'
@audio   -  rtprio     95
@audio   -  memlock    unlimited
@audio   -  nice       -19
EOF
usermod -a -G audio,video,input,render "${COMPA_USER}"
ok "Audio limits + user groups set"

# ── Phase 7: udev rules + USB storage helper ────────────────────────
log "Installing udev rules and USB storage helper"
if [[ -f "${COMPA_DIR}/setup/50-ableton-push-2.rules" ]]; then
    cp "${COMPA_DIR}/setup/50-ableton-push-2.rules" \
        /etc/udev/rules.d/50-ableton-push-2.rules
    ok "Push 2 USB rule installed"
fi
rm -f /etc/udev/rules.d/99-p6-automount.rules
cat > /etc/udev/rules.d/99-compa-roland-storage.rules <<'EOF'
# Mark Roland USB storage for Compa. Mounting is handled by
# /usr/local/sbin/compa-storage-mount. The systemd unit handles auto-mount
# on connect; the app can still call the helper for manual/debug mounting.
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]*", ATTRS{idVendor}=="0582", TAG+="systemd", ENV{ID_COMPA_STORAGE}="1", ENV{SYSTEMD_WANTS}+="compa-storage-mount@%k.service"
EOF

if [[ -f "${COMPA_DIR}/setup/compa-storage-mount" && \
      -f "${COMPA_DIR}/setup/compa-storage-mount@.service" ]]; then
    install -D -o root -g root -m 0755 \
        "${COMPA_DIR}/setup/compa-storage-mount" \
        /usr/local/sbin/compa-storage-mount
    install -D -o root -g root -m 0644 \
        "${COMPA_DIR}/setup/compa-storage-mount@.service" \
        /etc/systemd/system/compa-storage-mount@.service
    mkdir -p /mnt/compa
    chown root:root /mnt/compa
    chmod 0755 /mnt/compa
    cat > /etc/sudoers.d/020_compa_storage_mount <<EOF
${COMPA_USER} ALL=(root) NOPASSWD: /usr/local/sbin/compa-storage-mount *
EOF
    chmod 0440 /etc/sudoers.d/020_compa_storage_mount
    if visudo -cf /etc/sudoers.d/020_compa_storage_mount >/dev/null; then
        ok "Compa USB storage mount helper installed"
    else
        rm -f /etc/sudoers.d/020_compa_storage_mount
        die "Invalid sudoers entry for Compa USB storage helper"
    fi
else
    warn "USB storage helper/unit missing from repo; P-6/SP-404 mounting may require manual sudo"
fi

if [[ "$COMPA_IN_CHROOT" != "1" ]]; then
    systemctl daemon-reload
    udevadm control --reload-rules
    udevadm trigger
    for dev in /dev/sd* /dev/mmcblk[1-9]*; do
        [[ -b "$dev" ]] || continue
        if udevadm info -q property -n "$dev" 2>/dev/null | grep -q '^ID_VENDOR_ID=0582$'; then
            /usr/local/sbin/compa-storage-mount mount "$dev" "$(basename "$dev")" || true
        fi
    done
    ok "udev rules reloaded"
else
    ok "udev rules installed (will load on first boot)"
fi

# ── Phase 8: systemd service ────────────────────────────────────────
log "Installing systemd service"
cat > /etc/systemd/system/compa.service <<EOF
[Unit]
Description=Compa — touchscreen companion for music hardware
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${COMPA_USER}
Group=${COMPA_USER}
WorkingDirectory=${COMPA_DIR}
ExecStart=${COMPA_DIR}/venv/bin/python ${COMPA_DIR}/ui/p6_app.py
Restart=always
RestartSec=3

# Audio realtime priority
Nice=-10
LimitRTPRIO=95
LimitMEMLOCK=infinity

# Display: KMSDRM for direct framebuffer rendering
Environment=SDL_VIDEODRIVER=kmsdrm
Environment=SDL_FBDEV=/dev/fb0
Environment=SDL_MOUSE_RELATIVE=0
Environment=SDL_INPUT_LINUX_KEEP_KBD=1
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/home/${COMPA_USER}

# Audio + USB device access
SupplementaryGroups=audio video input render

[Install]
WantedBy=multi-user.target
EOF

if [[ "$COMPA_IN_CHROOT" == "1" ]]; then
    # Inside pi-gen build chroot — no live systemd. Just create the
    # symlink so the service starts on first boot of the image.
    ln -sf /etc/systemd/system/compa.service \
        /etc/systemd/system/multi-user.target.wants/compa.service
    ok "compa.service enabled (will start on first boot)"
else
    systemctl daemon-reload
    systemctl enable compa.service >/dev/null 2>&1
    ok "compa.service enabled"
    if systemctl is-active --quiet compa.service; then
        systemctl restart compa.service
        ok "compa.service restarted"
    else
        systemctl start compa.service \
            || warn "compa.service start deferred — reboot to pick up GPU/audio config changes"
    fi
fi

# ── Done ────────────────────────────────────────────────────────────
log "Compa install complete"
cat <<EOF

  Repo:    ${COMPA_DIR}
  User:    ${COMPA_USER}
  Service: compa.service

  Manual controls:
    sudo systemctl status compa
    journalctl -u compa -f
    sudo systemctl restart compa

  If you just installed for the first time, REBOOT now so the
  GPU memory split + onboard audio + screen blanking changes
  take effect:

    sudo reboot

EOF
