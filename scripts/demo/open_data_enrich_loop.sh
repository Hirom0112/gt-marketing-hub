#!/usr/bin/env bash
# open_data_enrich_loop.sh — turnkey E1 demo: the live tryopendata.ai query that
# CHANGES a decision. Starts the backend in OPEN_DATA_MODE=live against the LOCAL
# Supabase stack, mints a leader demo token, POSTs /open-data/enrich for a Texas
# district, and prints the data_source badge ("live") + the recommendation move.
#
# Prereq you do yourself: paste your od_live_ key into the repo .env as
# OPEN_DATA_API_KEY (replace the <od_live_REPLACE_ME> placeholder). Everything else
# is automatic. The live edge runs behind the INV-8 cap + kill switch.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
PORT="${PORT:-8000}"
DISTRICT="${DISTRICT:-031903}"   # a Texas district id the seeded set also knows
LOG_DIR="$(mktemp -d)"
BACKEND_LOG="$LOG_DIR/backend.log"
BACKEND_PID=""

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
cleanup() { [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true; say "backend log: $BACKEND_LOG"; }
trap cleanup EXIT

# --- Preflight ---------------------------------------------------------------
[ -f .env ] || die "no repo .env — add OPEN_DATA_API_KEY there first."
[ -f backend/.env.local-supabase ] || die "missing backend/.env.local-supabase — provision the local Supabase stack first."

# Root .env supplies the real OPEN_DATA_API_KEY; the local stack overrides the
# Supabase/DB vars so the decision-queue write lands on the migrated-to-0031 local
# DB, not a possibly-stale cloud project.
set -a; . .env; . backend/.env.local-supabase; set +a
export OPEN_DATA_MODE=live AUTH_MODE=demo COCKPIT_REPO=auto GT_PROGRAM_ID=fall_enrollment

case "${OPEN_DATA_API_KEY:-}" in
  ""|*REPLACE_ME*|"<"*) die "OPEN_DATA_API_KEY is still the placeholder — paste your od_live_ key into .env." ;;
  od_live_*) : ;;
  *) printf 'warning: OPEN_DATA_API_KEY does not start with od_live_ — continuing anyway\n' ;;
esac
psql "$DATABASE_URL" -tAc "select 1" >/dev/null 2>&1 || die "local Supabase DB unreachable — is 'supabase start' up?"

# --- Start the backend (live Open Data) --------------------------------------
say "starting backend on :$PORT (OPEN_DATA_MODE=live, key od_live_…${OPEN_DATA_API_KEY: -6})"
( cd backend && uv run uvicorn app.main:app --port "$PORT" >"$BACKEND_LOG" 2>&1 ) &
BACKEND_PID=$!
up=""; for _ in $(seq 1 60); do code="$(curl -s -o /dev/null -w "%{http_code}" "localhost:${PORT}/families" 2>/dev/null || echo 000)"; [ "$code" != "000" ] && { up=1; break; }; sleep 0.5; done
[ -n "$up" ] || { tail -20 "$BACKEND_LOG"; die "backend did not start (no HTTP response on :$PORT)"; }

# --- Mint a leader demo token (AUTH_MODE=demo) -------------------------------
say "minting a leader demo token"
TOKEN="$(curl -fsS -X POST "localhost:${PORT}/auth/demo-token" \
  -H 'Content-Type: application/json' -d '{"role":"leader"}' | sed -E 's/.*"access_token":"([^"]+)".*/\1/')"
[ -n "$TOKEN" ] || die "could not mint a demo token (is AUTH_MODE=demo + SUPABASE_JWT_SECRET set?)"

# --- The headline: live enrichment that changes a decision -------------------
say "POST /open-data/enrich  district=$DISTRICT  (live tryopendata.ai query)"
RESP="$(curl -fsS -X POST "localhost:${PORT}/open-data/enrich" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"district_id\":\"$DISTRICT\"}")" || { tail -30 "$BACKEND_LOG"; die "enrich call failed (see log — a 502 here usually means a live-API/cap/key issue)"; }

echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
SRC="$(printf '%s' "$RESP" | sed -E 's/.*"data_source":"([^"]+)".*/\1/')"
CHANGED="$(printf '%s' "$RESP" | sed -E 's/.*"recommendation_changed":(true|false).*/\1/')"

echo
if [ "$SRC" = "live" ]; then
  echo "PROOF: data_source=\"live\" — the enrichment came from the real tryopendata.ai datasets."
  echo "recommendation_changed=$CHANGED  (true ⇒ a card was fed into the Decision Queue; E1 headline)."
else
  echo "NOTE: data_source=\"$SRC\" (not live) — the live edge degraded to seeded."
  echo "Likely the kill switch is on, the cap is exhausted, or the key/endpoint rejected the query."
  echo "Check $BACKEND_LOG."
fi
