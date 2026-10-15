#!/usr/bin/env bash
# Start the BidBrief web app.
#   On SCC OnDemand (code-server): bash scripts/run_app.sh, then open port 8501 from the PORTS tab
#   On a plain compute node:       qrsh -l h_rt=3:00:00 -pe omp 4 ; bash scripts/run_app.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck disable=SC1091
[ -f scripts/env.sh ] && source scripts/env.sh
PORT="${1:-8501}"
NODE="$(hostname -s)"
echo "OnDemand / VS Code: open port ${PORT} from the PORTS tab."
echo "From your laptop:   ssh -N -L ${PORT}:${NODE}:${PORT} ${USER}@scc1.bu.edu   then open http://localhost:${PORT}"
exec python -m bidbrief serve --port "$PORT"
