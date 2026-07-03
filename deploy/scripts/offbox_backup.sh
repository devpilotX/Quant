#!/usr/bin/env bash
# quantsys off-box backup — takes a FRESH pg_dump of the live DB, encrypts it
# symmetrically (aes-256-cbc, pbkdf2 200k iterations; passphrase is
# BACKUP_PASSPHRASE in deploy/.env — gitignored, NEVER in the repo), and ships
# it to the operator's Telegram chat via the bot API. Telegram's cloud is the
# off-box copy, and the daily arrival doubles as a delivery heartbeat.
# Appends one OK/FAIL line to deploy/_audit/offbox.log, which selfcheck.sh
# watches with a 26 h freshness gate. Keeps the newest 3 encrypted dumps
# on-box in _audit/offbox/ for convenience.
#
# Decrypt:  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
#             -in quantsys-<stamp>.dump.enc -out quantsys.dump \
#             -pass pass:'<BACKUP_PASSPHRASE>'
# Restore:  pg_restore -U quantsys -d quantsys --clean --if-exists quantsys.dump
set -u
cd "$(dirname "$0")/.."          # -> deploy/
LOG=_audit/offbox.log
OUTDIR=_audit/offbox
mkdir -p "$OUTDIR"

TOK=$(grep -E '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2-)
CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' .env 2>/dev/null | head -1 | cut -d= -f2-)

note() {  # best-effort operator ping; never the reason this script fails
  { [ -n "$TOK" ] && [ -n "$CHAT" ]; } || return 0
  curl -s --max-time 10 -o /dev/null "https://api.telegram.org/bot${TOK}/sendMessage" \
    --data-urlencode "chat_id=${CHAT}" --data-urlencode "text=$1" || true
}
fail() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL $1" >> "$LOG"
  note "quantsys off-box backup FAILED: $1"
  exit 1
}

BACKUP_PASSPHRASE=$(grep -E '^BACKUP_PASSPHRASE=' .env 2>/dev/null | head -1 | cut -d= -f2-)
export BACKUP_PASSPHRASE
[ -n "$BACKUP_PASSPHRASE" ] || fail "BACKUP_PASSPHRASE missing from deploy/.env"
{ [ -n "$TOK" ] && [ -n "$CHAT" ]; } || fail "telegram creds missing from deploy/.env"

stamp=$(date +%Y%m%d-%H%M)       # host clock is IST
raw="$OUTDIR/quantsys-$stamp.dump"
enc="$raw.enc"

docker compose exec -T postgres pg_dump -U quantsys -d quantsys -Fc > "$raw" \
  || { rm -f "$raw"; fail "pg_dump"; }
[ -s "$raw" ] || { rm -f "$raw"; fail "pg_dump produced an empty file"; }

openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -in "$raw" -out "$enc" -pass env:BACKUP_PASSPHRASE \
  || { rm -f "$raw" "$enc"; fail "encrypt"; }
rm -f "$raw"

size=$(stat -c%s "$enc")
[ "$size" -le 47000000 ] || fail "encrypted dump ${size}B is near the 50MB bot-API cap — needs a bigger destination"

resp=$(curl -s --max-time 180 "https://api.telegram.org/bot${TOK}/sendDocument" \
  -F "chat_id=${CHAT}" -F "document=@${enc}" \
  -F "caption=quantsys DB off-box backup ${stamp} IST (${size} B, aes-256-cbc+pbkdf2)")
echo "$resp" | grep -q '"ok":true' || fail "telegram sendDocument rejected"

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) OK sent ${enc##*/} ${size}B" >> "$LOG"
ls -1t "$OUTDIR"/*.enc 2>/dev/null | tail -n +4 | xargs -r rm -f
exit 0
