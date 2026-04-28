#!/bin/bash -e
# pi-gen stage step — install Compa into the image rootfs by running
# the shared install.sh inside the chroot. Same script that producers
# can `curl | sudo bash` on a normal Pi OS install — there's only one
# install path.
#
# Two env vars steer it for the image-build context:
#   COMPA_IN_CHROOT=1   skip live systemd / udev operations
#   COMPA_BRANCH        which git branch to clone (set by the workflow)

set -x

echo "=== stage-compa/00-run.sh: env ==="
echo "BASE_DIR=${BASE_DIR}"
echo "ROOTFS_DIR=${ROOTFS_DIR}"
echo "STAGE=${STAGE:-<unset>}"
echo "STAGE_DIR=${STAGE_DIR:-<unset>}"
echo "SUB_STAGE_DIR=${SUB_STAGE_DIR:-<unset>}"
echo "WORK_DIR=${WORK_DIR:-<unset>}"

# Stage install.sh + udev rule into a durable rootfs path. /tmp can
# get reset between substages by some pi-gen flows; /opt is left
# alone for the lifetime of the chroot run.
install -d "${ROOTFS_DIR}/opt/compa-installer"
install -m 0755 -o root -g root \
    "${BASE_DIR}/setup/install.sh" \
    "${ROOTFS_DIR}/opt/compa-installer/install.sh"
install -m 0644 -o root -g root \
    "${BASE_DIR}/setup/50-ableton-push-2.rules" \
    "${ROOTFS_DIR}/opt/compa-installer/50-ableton-push-2.rules"
install -d "${ROOTFS_DIR}/opt/compa-installer/source"
cp -a "${BASE_DIR}/setup/compa-src/." \
    "${ROOTFS_DIR}/opt/compa-installer/source/"

echo "=== rootfs /opt/compa-installer after staging ==="
ls -la "${ROOTFS_DIR}/opt/compa-installer/"

on_chroot << EOF
set -ex
echo "=== inside chroot: /opt/compa-installer ==="
ls -la /opt/compa-installer/

export COMPA_IN_CHROOT=1
export COMPA_BRANCH=${COMPA_BRANCH:-main}
export COMPA_SOURCE_DIR=/opt/compa-installer/source
bash /opt/compa-installer/install.sh
rm -rf /opt/compa-installer
EOF
