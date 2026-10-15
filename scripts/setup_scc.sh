#!/usr/bin/env bash
# One-time setup of BidBrief on BU SCC (or any Linux box).
#
#   cd bidbrief
#   BIG_DIR=/projectnb/<your-project>/<you> bash scripts/setup_scc.sh
#
# BIG_DIR is the folder with plenty of space (NOT your small home quota). The
# virtual environment "bidbrf", the pip cache and the search models go there.
# Without BIG_DIR everything goes inside this project folder.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIG_DIR="${BIG_DIR:-$PROJECT_DIR}"
VENV_DIR="${VENV_DIR:-$BIG_DIR/venvs/bidbrf}"
MODEL_DIR="${MODEL_DIR:-$BIG_DIR/models/bidbrief}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$BIG_DIR/.pip_cache}"

echo "== BidBrief setup"
echo "   project : $PROJECT_DIR"
echo "   venv    : $VENV_DIR"
echo "   models  : $MODEL_DIR"
echo "   pip cache: $PIP_CACHE_DIR"
mkdir -p "$(dirname "$VENV_DIR")" "$MODEL_DIR" "$PIP_CACHE_DIR"

# --- Python 3.10+ (SCC uses environment modules)
PY_MODULE=""
if command -v module >/dev/null 2>&1 && [ -z "${PYTHON:-}" ]; then
  set +u  # module scripts may read unset variables
  for m in python3/3.12.4 python3/3.11.9 python3/3.11.4 python3/3.10.12 python3; do
    if module load "$m" >/dev/null 2>&1; then PY_MODULE="$m"; echo "   loaded module $m"; break; fi
  done
  # OCR tools, if SCC provides them as modules (optional)
  module load tesseract >/dev/null 2>&1 && echo "   loaded module tesseract" || true
  module load ghostscript >/dev/null 2>&1 && echo "   loaded module ghostscript" || true
  set -u
fi
PY="${PYTHON:-python3}"
"$PY" - <<'PYCHK'
import sys
v = sys.version_info
print(f"   python  : {sys.version.split()[0]}")
if v < (3, 10):
    sys.exit("Python 3.10+ is needed. Run 'module avail python3' and set PYTHON=/path/to/python3.11")
PYCHK

# --- venv named bidbrf
if [ ! -d "$VENV_DIR" ]; then
  "$PY" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip wheel setuptools -q
python -m pip install -r "$PROJECT_DIR/requirements.txt"
python -m pip install -e "$PROJECT_DIR" --no-deps -q

# --- env file that every later session sources
cat > "$PROJECT_DIR/scripts/env.sh" <<ENVSH
# source scripts/env.sh   (activates bidbrf and sets the cache folders)
if command -v module >/dev/null 2>&1; then
  [ -n "$PY_MODULE" ] && module load "$PY_MODULE" >/dev/null 2>&1 || true
  module load tesseract >/dev/null 2>&1 || true
  module load ghostscript >/dev/null 2>&1 || true
fi
source "$VENV_DIR/bin/activate"
export BIDBRIEF_MODEL_CACHE="$MODEL_DIR"
export HF_HOME="$MODEL_DIR/hf"
export PIP_CACHE_DIR="$PIP_CACHE_DIR"
export PYTHONPATH="$PROJECT_DIR:\${PYTHONPATH:-}"
# OCR tools installed by scripts/install_ocr_scc.sh (if any)
if [ -x "$BIG_DIR/tools/ocr/bin/tesseract" ]; then
  export PATH="$BIG_DIR/tools/ocr/bin:\$PATH"
  export TESSDATA_PREFIX="$BIG_DIR/tools/ocr/share/tessdata"
fi
cd "$PROJECT_DIR"
ENVSH
echo "   wrote scripts/env.sh"

if [ ! -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "   created .env (put your GEMINI_API_KEY in it)"
fi

# --- download the small search models now (login nodes have internet)
export BIDBRIEF_MODEL_CACHE="$MODEL_DIR" HF_HOME="$MODEL_DIR/hf"
cd "$PROJECT_DIR"
python -m bidbrief doctor --download || true

echo
echo "== Done. Next:"
echo "   source scripts/env.sh"
echo "   nano .env                      # paste GEMINI_API_KEY"
echo "   python -m pytest               # all tests should pass"
echo "   python -m bidbrief sample && python -m bidbrief process data/packages/sample-mapleford"
