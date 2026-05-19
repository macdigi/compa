#!/usr/bin/env bash
# Build and launch the Roland SP-404MKII app under the serial interposer.
#
# Run on the Mac mini from a checkout of this repo:
#   bash tools/mac_sp404_capture_interpose.sh

set -euo pipefail

APP="${SP404_APP:-/Applications/Roland/SP-404MKII.app}"
OUT="${SP404_CAPTURE_DIR:-$HOME/Desktop/sp404-capture}"
SRC="$(cd "$(dirname "$0")" && pwd)/mac_sp404_serial_trace.c"
LIB="$OUT/libsp404_serial_trace.dylib"
LOG="$OUT/sp404_interpose_$(date -u +%Y%m%dT%H%M%SZ).jsonl"

mkdir -p "$OUT"

if [[ ! -d "$APP" ]]; then
  echo "SP-404 app not found: $APP" >&2
  exit 1
fi

EXE_NAME=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")
EXE="$APP/Contents/MacOS/$EXE_NAME"

if [[ ! -x "$EXE" ]]; then
  echo "SP-404 executable not found: $EXE" >&2
  exit 1
fi

echo "Building interposer:"
echo "  $LIB"
ARCH_FLAGS=()
if command -v lipo >/dev/null 2>&1; then
  for arch in $(lipo -archs "$EXE" 2>/dev/null || true); do
    case "$arch" in
      arm64|x86_64) ARCH_FLAGS+=("-arch" "$arch") ;;
    esac
  done
fi
clang -dynamiclib -O2 -Wall "${ARCH_FLAGS[@]}" -o "$LIB" "$SRC"

echo "Capture log:"
echo "  $LOG"
echo
echo "Launching:"
echo "  $EXE"
echo
echo "Use the app read-only: detect SP, list projects/pads, click/view one pad."
echo "Then quit the Roland app. If it refuses to launch or the log only contains"
echo "trace_start/trace_stop, run:"
echo "  codesign -dv --verbose=4 \"$APP\""
echo "and report the output."
echo

SP404_TRACE_LOG="$LOG" DYLD_INSERT_LIBRARIES="$LIB" "$EXE"

echo
echo "Capture complete:"
echo "  $LOG"
wc -l "$LOG" || true
