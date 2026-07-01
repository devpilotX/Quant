#!/usr/bin/env bash
# quantsys daily ops self-check — one GREEN/RED line per run appended to
# deploy/_audit/selfcheck.log. A RED run exits non-zero so the systemd unit
# shows as failed (`systemctl list-units --failed`) — no silent decay.
#
# Checks: public-plane health endpoint, engine container state, engine
# heartbeat freshness (engine_status), container restart count (context, not
# a gate), root-disk headroom.
set -u
cd "$(dirname "$0")/.."          # -> deploy/
LOG=_audit/selfcheck.log
mkdir -p _audit
fail=""

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8010/api/health || true)
[ "$code" = "200" ] || fail="$fail api_health=$code"

state=$(docker inspect quantsys-engine-paper-1 --format '{{.State.Status}}' 2>/dev/null || echo missing)
[ "$state" = "running" ] || fail="$fail engine=$state"

restarts=$(docker inspect quantsys-engine-paper-1 --format '{{.RestartCount}}' 2>/dev/null || echo '?')

disk=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "${disk:-100}" -lt 85 ] || fail="$fail disk=${disk}%"

# engine_status.last_heartbeat is naive IST (engine clock); compare in-zone.
hb_age=$(docker compose exec -T postgres psql -U quantsys -d quantsys -tAc \
  "SELECT COALESCE(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'Asia/Kolkata') - max(last_heartbeat)))::int, 999999) FROM engine_status" \
  2>/dev/null | tr -d '[:space:]')
if [ -z "${hb_age:-}" ]; then
  fail="$fail heartbeat=unreadable"
elif [ "$hb_age" -gt 300 ]; then
  fail="$fail heartbeat_age=${hb_age}s"
fi

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ -z "$fail" ]; then
  echo "$ts GREEN restarts=$restarts hb_age=${hb_age:-?}s disk=${disk:-?}%" >> "$LOG"
else
  echo "$ts RED$fail restarts=$restarts" >> "$LOG"
  exit 1
fi
