# source scripts/env.sh   (activates bidbrf and sets the cache folders)
if command -v module >/dev/null 2>&1; then
  [ -n "python3/3.12.4" ] && module load "python3/3.12.4" >/dev/null 2>&1 || true
  module load tesseract >/dev/null 2>&1 || true
  module load ghostscript >/dev/null 2>&1 || true
fi
source "/projectnb/medaihack/team4/venvs/bidbrf/bin/activate"
export BIDBRIEF_MODEL_CACHE="/projectnb/medaihack/team4/models/bidbrief"
export HF_HOME="/projectnb/medaihack/team4/models/bidbrief/hf"
export PIP_CACHE_DIR="/projectnb/medaihack/team4/.pip_cache"
export PYTHONPATH="/projectnb/medaihack/team4/v3/bidbrief:${PYTHONPATH:-}"
# OCR tools installed by scripts/install_ocr_scc.sh (if any)
if [ -x "/projectnb/medaihack/team4/tools/ocr/bin/tesseract" ]; then
  export PATH="/projectnb/medaihack/team4/tools/ocr/bin:$PATH"
  export TESSDATA_PREFIX="/projectnb/medaihack/team4/tools/ocr/share/tessdata"
fi
cd "/projectnb/medaihack/team4/v3/bidbrief"
