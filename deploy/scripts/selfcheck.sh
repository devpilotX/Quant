#!/usr/bin/env bash
# quantsys daily ops self-check — one GREEN/RED line per run appended to
# deploy/_audit/selfcheck.log. A RED run exits non-zero so the systemd unit
# shows as failed (`systemctl list-units --failed`) — no silent decay.
# RED (and WARN) results are also pushed to the operator Telegram chat.
#
# Checks: public-plane health endpoint, engine container state, engine
# heartbeat freshness (engine_status), container restart count (context, not
# a gate), root-disk headroom — plus the Forward Study 2 integrity guards:
#   * yesterday's decision cadence (trading weekdays from 2026-07-03): exactly
#     25 decisions, one per 15-min bar, close bar 15:15 decided. A weekday with
#     ZERO decisions is a WARN, not RED (an NSE holiday is indistinguishable
#     from an outage here; the operator disambiguates).
#   * today's post-recycle "paper broker cash restored" engine log line
#     (weekdays after ~09:05 IST) — guards the durable-cash fix.
#   * off-box backup freshness: an OK in _audit/offbox.log within 26 h
#     (see scripts/offbox_backup.sh).
set -u
cd "$(dirname "$0")/.."          # -> deploy/
LOG=_audit/selfcheck.log
mkdir -p _audit
fail=""
warn=""

# Forward Study 2 first live session — the cadence contract applies from here.
STUDY2_START=2026-07-03

tg_notify() {  # $1 = text; best-effort, must never fail the check itself
  local tok chat
  tok=$(grep -E '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2-)
  chat=$(grep -E '^TELEGRAM_CHAT_ID=' .env 2>/dev/null | head -1 | cut -d= -f2-)
  { [ -n "$tok" ] && [ -n "$chat" ]; } || return 0
  curl -s --max-time 10 -o /dev/null "https://api.telegram.org/bot${tok}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" || true
}

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

# --- Study-2 cadence guard: yesterday = one decision per bar, 25 bars, last 15:15.
# decisions.ts is naive IST bar time; mode filter keeps any replay/backtest rows out.
yday=$(date -d yesterday +%F)    # host clock is IST
ydow=$(date -d yesterday +%u)
if [ "$ydow" -le 5 ] && [ ! "$yday" \< "$STUDY2_START" ]; then
  row=$(docker compose exec -T postgres psql -U quantsys -d quantsys -tAc \
    "SELECT COALESCE(sum(c),0)||'|'||count(*)||'|'||COALESCE(max(c),0)||'|'||COALESCE(to_char(max(b),'HH24:MI'),'-')
       FROM (SELECT date_trunc('minute',ts) b, count(*) c
               FROM decisions
              WHERE mode='paper' AND ts >= DATE '$yday' AND ts < DATE '$yday' + 1
              GROUP BY 1) x" 2>/dev/null | tr -d '[:space:]')
  if [ -z "$row" ]; then
    fail="$fail study_cadence=unreadable"
  else
    IFS='|' read -r n_dec n_buck max_per last_bar <<<"$row"
    if [ "${n_dec:-0}" = "0" ]; then
      warn="$warn zero_decisions_${yday}(holiday?)"
    elif [ "$n_dec" != "25" ] || [ "$n_buck" != "25" ] || [ "$max_per" != "1" ] || [ "$last_bar" != "15:15" ]; then
      fail="$fail study_cadence=${n_dec}dec/${n_buck}bars/max${max_per}/last${last_bar}(${yday})"
    fi
  fi
fi

# --- Study-2 durable-cash guard: the 08:50 weekday recycle must RESTORE paper
# cash (never re-apply the float over an open book). Enforced from 09:05 IST.
if [ "$(date +%u)" -le 5 ]; then
  recycle_epoch=$(date -d 08:45 +%s)
  now_epoch=$(date +%s)
  if [ "$now_epoch" -gt $((recycle_epoch + 1200)) ]; then
    mins=$(( (now_epoch - recycle_epoch) / 60 + 1 ))
    if ! docker logs --since "${mins}m" quantsys-engine-paper-1 2>&1 | grep -q 'paper broker cash restored'; then
      fail="$fail cash_restore=missing_since_0845"
    fi
  fi
fi

# --- Off-box backup freshness (quant-offbox.timer, daily 20:00 IST).
if [ -f _audit/offbox.log ]; then
  last_ok=$(grep ' OK ' _audit/offbox.log | tail -1 | awk '{print $1}')
  if [ -z "$last_ok" ]; then
    fail="$fail offbox=never_succeeded"
  else
    ok_epoch=$(date -d "$last_ok" +%s 2>/dev/null || echo 0)
    age_h=$(( ($(date +%s) - ok_epoch) / 3600 ))
    [ "$age_h" -le 26 ] || fail="$fail offbox_age=${age_h}h"
  fi
else
  warn="$warn offbox_log_missing"
fi

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ -z "$fail" ]; then
  echo "$ts GREEN restarts=$restarts hb_age=${hb_age:-?}s disk=${disk:-?}%${warn:+ warn=$warn}" >> "$LOG"
  if [ -n "$warn" ]; then
    tg_notify "quantsys selfcheck WARN:$warn (otherwise GREEN)"
  fi
  exit 0
else
  echo "$ts RED$fail restarts=$restarts${warn:+ warn=$warn}" >> "$LOG"
  tg_notify "quantsys selfcheck RED:$fail"
  exit 1
fi
