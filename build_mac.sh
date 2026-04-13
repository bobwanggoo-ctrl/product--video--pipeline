#!/usr/bin/env bash
# Build ProductVideoPipeline.app for macOS
set -euo pipefail
cd "$(dirname "$0")"

echo "=== Product Video Pipeline — macOS Build ==="

# 1. Install / upgrade deps
echo "[1/3] Installing dependencies..."
pip3 install -r requirements.txt -q

# 2. Build
echo "[2/3] Running PyInstaller..."
python3 -m PyInstaller product_video_pipeline.spec --noconfirm --clean

# 3. Post-process: copy runtime assets next to .app
APP_DIR="dist/ProductVideoPipeline.app/Contents/MacOS"
echo "[3/3] Copying runtime assets..."
mkdir -p "$APP_DIR/input/fonts"
mkdir -p "$APP_DIR/input/music"
mkdir -p "$APP_DIR/output"

# Copy fonts and music samples if present
[ -d "input/fonts"  ] && cp -r "input/fonts/"  "$APP_DIR/input/fonts/"
[ -d "input/music"  ] && cp -r "input/music/"  "$APP_DIR/input/music/"
[ -f ".env.example" ] && cp ".env.example" "$APP_DIR/.env.example"

echo ""
echo "✓ Build complete: dist/ProductVideoPipeline.app"
echo ""
echo "  To distribute: zip -r ProductVideoPipeline-mac.zip dist/ProductVideoPipeline.app"
