#!/usr/bin/env bash
# Run the hot-search eval inside the api container, then regenerate the
# markdown report at the repo root.
#
# Usage (from repo root):
#   ./backend/scripts/eval/run.sh                # all personas, default settings
#   ./backend/scripts/eval/run.sh --max-hits 4   # quick run
#   ./backend/scripts/eval/run.sh --personas ml_engineer_sf,fintech_ds
#
# Notes:
# - Requires Docker Compose stack running. Will rebuild api if needed.
# - Eval uses real OpenAI API; budget ~$1-2 per full run (7 personas).
# - Report is written to ./EVAL.md (committed to repo, linked from README).

set -euo pipefail

# Find repo root (script lives at backend/scripts/eval/run.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# Make sure the api container is running
if ! docker compose ps --status running api 2>/dev/null | grep -q api; then
  echo "Starting API container..." >&2
  docker compose up -d api
  sleep 5
fi

echo "Running eval (this takes 5–15 minutes; ~\$1-2 in OpenAI tokens)..." >&2
docker compose exec -T api python -m scripts.eval.eval_hot_search "$@"

echo "" >&2
echo "Generating EVAL.md from latest results..." >&2
# The container writes results to /app/tests/eval/external/results/.
# Have it generate the markdown directly to the repo root EVAL.md.
docker compose exec -T api python -m scripts.eval.eval_hot_search_report \
    --out /app/EVAL.md.tmp

# Copy out of container to repo root
docker cp jobboard-api-1:/app/EVAL.md.tmp "$REPO_ROOT/EVAL.md"
docker compose exec -T api rm -f /app/EVAL.md.tmp

echo "" >&2
echo "✅ Eval complete. See EVAL.md at repo root." >&2
