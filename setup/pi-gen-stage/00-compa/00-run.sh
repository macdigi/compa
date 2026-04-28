#!/bin/bash -e
# pi-gen stage step — install Compa into the image rootfs by running
# the shared install.sh inside the chroot. Same script that producers
# can `curl | sudo bash` on a normal Pi OS install — there's only one
# install path.
#
# Two env vars steer it for the image-build context:
#   COMPA_IN_CHROOT=1   skip live systemd / udev operations
#   COMPA_BRANCH        which git branch to clone (set by the workflow)

# Stage the latest install.sh + udev rule into the rootfs so the
# chrooted run has access to them at predictable paths.
install -m 0755 -o root -g root \
    "${BASE_DIR}/setup/install.sh" "${ROOTFS_DIR}/tmp/install.sh"
mkdir -p "${ROOTFS_DIR}/tmp/compa-setup"
install -m 0644 -o root -g root \
    "${BASE_DIR}/setup/50-ableton-push-2.rules" \
    "${ROOTFS_DIR}/tmp/compa-setup/50-ableton-push-2.rules" || true

on_chroot << EOF
set -e
export COMPA_IN_CHROOT=1
export COMPA_BRANCH=${COMPA_BRANCH:-main}
bash /tmp/install.sh
rm -f /tmp/install.sh
rm -rf /tmp/compa-setup
EOF
