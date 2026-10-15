#!/usr/bin/env bash
# Install the OCR tools (tesseract + ghostscript) without root, into your big folder,
# so scanned pages and scanned addenda can be read. Run once on a login node:
#   BIG_DIR=/projectnb/<project>/<you> bash scripts/install_ocr_scc.sh
# then:  source scripts/env.sh   (it picks the tools up automatically)
set -eo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIG_DIR="${BIG_DIR:-$PROJECT_DIR}"
OCR_DIR="$BIG_DIR/tools/ocr"

if command -v tesseract >/dev/null 2>&1; then
  echo "tesseract is already available: $(command -v tesseract)"; exit 0
fi
if command -v module >/dev/null 2>&1; then
  if module load tesseract >/dev/null 2>&1 && command -v tesseract >/dev/null 2>&1; then
    echo "SCC has a tesseract module. scripts/env.sh loads it; nothing to install."; exit 0
  fi
  module load miniconda >/dev/null 2>&1 || true
fi

mkdir -p "$BIG_DIR/tools"
if command -v conda >/dev/null 2>&1; then
  echo "Installing tesseract + ghostscript with conda into $OCR_DIR (a few minutes)..."
  CONDA_PKGS_DIRS="$BIG_DIR/tools/.conda_pkgs" conda create -y -p "$OCR_DIR" -c conda-forge tesseract ghostscript
else
  echo "conda not found; using micromamba (downloaded once into $BIG_DIR/tools)..."
  if [ ! -x "$BIG_DIR/tools/bin/micromamba" ]; then
    (cd "$BIG_DIR/tools" && curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj bin/micromamba)
  fi
  MAMBA_ROOT_PREFIX="$BIG_DIR/tools/mamba" "$BIG_DIR/tools/bin/micromamba" create -y -p "$OCR_DIR" -c conda-forge tesseract ghostscript
fi
"$OCR_DIR/bin/tesseract" --version | head -1
ENV_SH="$PROJECT_DIR/scripts/env.sh"
if [ -f "$ENV_SH" ] && ! grep -q "tools/ocr/bin" "$ENV_SH"; then
  cat >> "$ENV_SH" <<ENVSH
# OCR tools (added by install_ocr_scc.sh)
export PATH="$OCR_DIR/bin:\$PATH"
export TESSDATA_PREFIX="$OCR_DIR/share/tessdata"
ENVSH
  echo "Added the OCR tools to scripts/env.sh"
fi
echo "Done. Run: source scripts/env.sh   then re-process your packages."
