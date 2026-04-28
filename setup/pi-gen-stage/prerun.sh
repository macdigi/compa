#!/bin/bash -e
# pi-gen prerun.sh — copy the previous stage's filesystem so we
# can layer Compa on top of Pi OS Lite without rebuilding from scratch.
if [ ! -d "${ROOTFS_DIR}" ]; then
    copy_previous
fi
