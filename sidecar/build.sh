#!/usr/bin/env bash
# Build the Python sidecar into a single Mach-O binary suffixed with the host
# target triple — Tauri 2 resolves externalBin paths by appending the triple
# at runtime (see tauri.conf.json `bundle.externalBin`).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRIPLE="$(rustc --print host-tuple)"          # e.g. aarch64-apple-darwin
OUT_DIR="$ROOT/src-tauri/binaries"
WORK_DIR="$ROOT/sidecar/.build"
RUNTIME="$ROOT/.venv-stable"
if [[ ! -x "$RUNTIME/bin/python" ]]; then
    echo "Create the Python 3.11 runtime first: python3.11 -m venv .venv-stable" >&2
    exit 1
fi
"$RUNTIME/bin/python" -c 'import sys; assert sys.version_info >= (3, 10), "Python >=3.10 required"'

mkdir -p "$OUT_DIR" "$WORK_DIR"
export PYINSTALLER_CONFIG_DIR="$WORK_DIR/cache"

# --onefile is required: Tauri externalBin must be a single executable, not a
# .app/.onedir bundle. The first launch unpacks to a temp dir (~200-500ms),
# which is why the Rust shell spawns this once at startup and keeps it alive.
"$RUNTIME/bin/pyinstaller" \
    --onefile \
    --name bilio-sidecar \
    --distpath "$WORK_DIR/dist" \
    --workpath "$WORK_DIR/work" \
    --specpath "$WORK_DIR" \
    --clean \
    --noconfirm \
    "$ROOT/sidecar/main.py"

# Tauri looks for `<base>-<triple>` next to tauri.conf.json's externalBin entry.
DEST="$OUT_DIR/bilio-sidecar-$TRIPLE"
cp "$WORK_DIR/dist/bilio-sidecar" "$DEST"
chmod +x "$DEST"

echo "Built: $DEST"
"$DEST" </dev/null >/dev/null
