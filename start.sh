#!/usr/bin/env bash
# Mirror — one-command setup wrapper.
#
# Verifies the prerequisites a `docker compose up --build` silently assumes
# (Docker daemon running, host ports free), starts the stack, waits for the
# api and frontend to actually be responding (not just "up" per compose),
# then opens the browser. First run takes ~3–5 min for image pulls + build.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# --- Prerequisite: Docker daemon -----------------------------------------------

if ! command -v docker > /dev/null 2>&1; then
  echo "[ERROR] docker not found on PATH. Install Docker Desktop (or dockerd) first."
  exit 1
fi

if ! docker info > /dev/null 2>&1; then
  echo "[ERROR] Docker daemon is not running. Start Docker Desktop (or 'sudo systemctl start docker') and try again."
  exit 1
fi

if ! docker compose version > /dev/null 2>&1; then
  echo "[ERROR] 'docker compose' not available. You need Docker Compose v2 (ships with Docker Desktop >=3.4)."
  exit 1
fi

# --- Prerequisite: host ports -------------------------------------------------

PORTS=(3050 8085 5433 6379 8888)
PORT_LABELS=("frontend" "api" "postgres" "redis" "searxng")

OCCUPIED=()
for i in "${!PORTS[@]}"; do
  port="${PORTS[$i]}"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN > /dev/null 2>&1; then
    OCCUPIED+=("$port (${PORT_LABELS[$i]})")
  fi
done

if [ ${#OCCUPIED[@]} -gt 0 ]; then
  echo "[ERROR] These host ports are already in use:"
  for entry in "${OCCUPIED[@]}"; do
    echo "  - $entry"
  done
  echo ""
  echo "Free them and try again, or override the host-side port mappings in"
  echo "docker-compose.yml (the container-side ports don't need to change)."
  exit 1
fi

# --- Start the stack ----------------------------------------------------------

echo "[INFO] Starting Mirror — first run takes ~3-5 min for image pulls + build."
echo ""
docker compose up --build -d

# --- Wait for api -------------------------------------------------------------

# The api runs alembic migrations on startup, so 'up -d' returning isn't the
# same as 'ready to serve'. Poll /health up to 5 min.
echo -n "[INFO] Waiting for api on http://localhost:8085 "
api_ready=0
for _ in $(seq 1 150); do
  if curl -sf http://localhost:8085/health > /dev/null 2>&1; then
    api_ready=1
    break
  fi
  echo -n "."
  sleep 2
done
echo ""
if [ "$api_ready" -eq 0 ]; then
  echo "[ERROR] api never responded on http://localhost:8085/health after 5 min."
  echo "        Check 'docker compose logs api'."
  exit 1
fi
echo "[OK] api ready."

# --- Wait for frontend --------------------------------------------------------

echo -n "[INFO] Waiting for frontend on http://localhost:3050 "
fe_ready=0
for _ in $(seq 1 60); do
  if curl -sf http://localhost:3050 > /dev/null 2>&1; then
    fe_ready=1
    break
  fi
  echo -n "."
  sleep 2
done
echo ""
if [ "$fe_ready" -eq 0 ]; then
  echo "[ERROR] frontend never responded on http://localhost:3050 after 2 min."
  echo "        Check 'docker compose logs frontend'."
  exit 1
fi
echo "[OK] frontend ready."

# --- Open browser -------------------------------------------------------------

# Route to the page the user actually needs first so we skip an unnecessary
# redirect. /setup is the gate everyone hits before anything else works;
# /onboarding follows once API keys are saved.
LANDING="/"
if curl -sf http://localhost:8085/api/setup/status 2>/dev/null | grep -q '"needs_setup":true'; then
  LANDING="/setup"
elif curl -sf http://localhost:8085/api/onboarding/status 2>/dev/null | grep -q '"needs_onboarding":true'; then
  LANDING="/onboarding"
fi

URL="http://localhost:3050${LANDING}"
echo ""
echo "[DONE] Mirror is ready — opening $URL"
echo ""
case "$LANDING" in
  /setup)
    echo "First-time setup: pick an LLM provider, paste your API key, hit Save."
    echo "After saving you'll be routed to /onboarding to import your resume."
    ;;
  /onboarding)
    echo "Onboarding: upload your resume (PDF or DOCX); optionally paste LinkedIn /"
    echo "Google Scholar / GitHub / personal-site URLs to enrich the profile."
    ;;
  *)
    echo "You're already set up — you'll land on the jobs board."
    ;;
esac
echo ""

if command -v open > /dev/null 2>&1; then
  open "$URL"
elif command -v xdg-open > /dev/null 2>&1; then
  xdg-open "$URL"
else
  echo "Open $URL in your browser."
fi
