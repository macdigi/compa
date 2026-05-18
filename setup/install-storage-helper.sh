#!/usr/bin/env bash
# Install the narrow Compa USB storage mount helper without touching the repo.
#
# Use this on an existing Pi install when P-6/SP-404 storage appears in lsblk
# but Compa cannot mount it from the service.

set -euo pipefail

COMPA_USER="${COMPA_USER:-pi}"
COMPA_DIR="${COMPA_DIR:-/home/${COMPA_USER}/compa}"
HELPER_SRC="${COMPA_DIR}/setup/compa-storage-mount"
UNIT_SRC="${COMPA_DIR}/setup/compa-storage-mount@.service"
HELPER_DST="/usr/local/sbin/compa-storage-mount"
SUDOERS_FILE="/etc/sudoers.d/020_compa_storage_mount"

[[ "$(id -u)" -eq 0 ]] || { echo "Run with sudo: sudo bash setup/install-storage-helper.sh" >&2; exit 1; }
[[ -f "$HELPER_SRC" ]] || { echo "Missing helper source: $HELPER_SRC" >&2; exit 1; }
[[ -f "$UNIT_SRC" ]] || { echo "Missing systemd unit source: $UNIT_SRC" >&2; exit 1; }
id -u "$COMPA_USER" >/dev/null 2>&1 || { echo "Unknown user: $COMPA_USER" >&2; exit 1; }

install -D -o root -g root -m 0755 "$HELPER_SRC" "$HELPER_DST"
install -D -o root -g root -m 0644 "$UNIT_SRC" \
    /etc/systemd/system/compa-storage-mount@.service
mkdir -p /mnt/compa
chown root:root /mnt/compa
chmod 0755 /mnt/compa

cat > "$SUDOERS_FILE" <<EOF
${COMPA_USER} ALL=(root) NOPASSWD: ${HELPER_DST} *
EOF
chmod 0440 "$SUDOERS_FILE"
if ! visudo -cf "$SUDOERS_FILE" >/dev/null; then
    rm -f "$SUDOERS_FILE"
    echo "Invalid sudoers entry; removed $SUDOERS_FILE" >&2
    exit 1
fi

rm -f /etc/udev/rules.d/99-p6-automount.rules
cat > /etc/udev/rules.d/99-compa-roland-storage.rules <<'EOF'
# Mark Roland USB storage for Compa. Mounting is handled by
# /usr/local/sbin/compa-storage-mount. The systemd unit handles auto-mount
# on connect; the app can still call the helper for manual/debug mounting.
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]*", ATTRS{idVendor}=="0582", TAG+="systemd", ENV{ID_COMPA_STORAGE}="1", ENV{SYSTEMD_WANTS}+="compa-storage-mount@%k.service"
EOF

systemctl daemon-reload || true
udevadm control --reload-rules || true
udevadm trigger || true

for dev in /dev/sd* /dev/mmcblk[1-9]*; do
    [[ -b "$dev" ]] || continue
    if udevadm info -q property -n "$dev" 2>/dev/null | grep -q '^ID_VENDOR_ID=0582$'; then
        "$HELPER_DST" mount "$dev" "$(basename "$dev")" || true
    fi
done

echo "Compa storage helper installed for ${COMPA_USER}."
echo "Restart compa.service after pulling code changes."
