# quantsys — Operations Runbook (security & resilience)

Procedural only — **no secret values here**. Actual secrets live in the gitignored
`VPS_DEPLOYMENT_REPORT.md` (repo root) and `/opt/quant/deploy/.env` (VPS, mode 600).
Status as of 2026-06-17 in **[brackets]** per item.

---

## 1. Angel One credential rotation  **[REQUIRED before any live money — leaked 2026-06-11]**

The repo is **confirmed secret-free** (API key / client code are not in the working
tree or git history; only `deploy/.env.example`, a placeholder template, is tracked —
verified 2026-06-17 via `git grep` + `git log -S`). So **no git-history scrub is needed** —
only the live credentials must be rotated, because they were briefly public.

Human steps (in the Angel One SmartAPI / portal — the agent cannot do these):
1. **API key:** SmartAPI dashboard → regenerate the app's API key (invalidates the old one).
2. **MPIN/PIN:** Angel One app/web → change the login PIN.
3. **TOTP:** disable + re-enrol the authenticator → you get a **new TOTP secret** (and a new
   otpauth URI). Save it.
4. **VPS:** edit `/opt/quant/deploy/.env` → update `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`
   (unchanged), `ANGEL_PASSWORD` (new MPIN), `ANGEL_TOTP_SECRET` (new). Keep mode 600.
5. Restart: `cd /opt/quant/deploy && sudo docker compose --profile paper up -d engine-paper`.
6. Verify: engine log shows `Angel One connected; … instruments`. Update
   `VPS_DEPLOYMENT_REPORT.md` §7 with the new values.
7. **Whitelist:** confirm the SmartAPI app's allowed IP still equals the VPS IP
   (80.225.240.46).

---

## 2. Telegram alerts  **[DEFERRED — blocked by the regional India Telegram ban (upstream network), 2026-06-18]**

Token + chat-id ARE now set in the VPS `.env` and the wiring is verified correct
(`channels_configured() == ['telegram']`), but **delivery fails from the VPS**:
`api.telegram.org` is network-unreachable (TCP timeout to `149.154.x` / `91.108.x`, IPv4 &
IPv6) while general egress works (`google.com` → 200). No host firewall rule blocks it — the
block is **upstream** (the India Telegram ban). `notify.py` still POSTs to
`api.telegram.org/bot<token>/sendMessage`; alerts persist & show in the dashboard regardless.
**Re-test after the 22nd; if still blocked, switch the alert channel to email/webhook**
(`notify.py` is channel-pluggable). Setup steps (already done) for reference:

1. In Telegram, message **@BotFather** → `/newbot` → get the **bot token**.
2. Start a chat with your new bot (send it any message), then get your **chat id**
   (e.g. message **@userinfobot**, or `https://api.telegram.org/bot<token>/getUpdates`).
3. VPS `/opt/quant/deploy/.env`: set `TELEGRAM_BOT_TOKEN=…`, `TELEGRAM_CHAT_ID=…`.
4. `sudo docker compose --profile paper up -d engine-paper api`.
5. Verify delivery: trigger any alert (e.g. restart the engine → an `engine_start` alert
   fires) and confirm the Telegram message arrives. (Agent can wire+test this step once
   you provide a token.)

---

## 3. Dashboard operator TOTP  **[configured ✓; login e2e-verified at deploy]**

`users.totp_secret` is NOT NULL (every operator has TOTP). The 2026-06-12 deploy ran a
26/26 e2e incl. a real password+TOTP login over public TLS.
- Re-enrol authenticator: scan/enter the otpauth URI in `VPS_DEPLOYMENT_REPORT.md` §7.
- Reset password if needed: `cd /opt/quant/deploy && sudo docker compose run --rm api
  python -m qsdash.cli reset-password --username dipanshu`.
- **Lockout note:** consecutive failed logins lock the account for a cooldown; a password
  reset clears it.

---

## 4. Backups  **[on-VPS dumps ✓ (6-hourly, 14 kept); restore drill ✓; off-VPS = 1 user step]**

- **Restore drill — PASSED 2026-06-17:** the newest dump restored cleanly into a temp DB
  (21 tables, decisions present), temp DB dropped. The dumps are valid & restorable. To
  repeat:
  ```bash
  cd /opt/quant/deploy; D=$(ls -t backups/*.dump | head -1)
  sudo docker compose exec -T postgres psql -U quantsys -d postgres -c "CREATE DATABASE qs_restore_test;"
  sudo docker compose exec -T postgres pg_restore -U quantsys -d qs_restore_test --no-owner < "$D"
  sudo docker compose exec -T postgres psql -U quantsys -d qs_restore_test -c "SELECT count(*) FROM decisions;"
  sudo docker compose exec -T postgres psql -U quantsys -d postgres -c "DROP DATABASE qs_restore_test;"
  ```
  Production restore (overwrites live): `deploy/scripts/restore.sh backups/<file>.dump`.
- **Off-VPS copy — still the one pending item** (the VPS cannot push to your PC; run this
  *from your PC*, where the deploy key / SSH to the VPS lives):
  ```bash
  rsync -az mcpagent@80.225.240.46:/opt/quant/deploy/backups/ ~/quantsys-backups/
  ```
  Schedule it (Windows Task Scheduler / cron) daily. A VPS disk is **not** a backup.

---

## Quick status table

| Item | Status |
|---|---|
| Repo secret-free (tree + history) | ✅ verified |
| Angel cred rotation | ⛔ **pending** (human portal steps above) — blocking for live |
| Telegram alerts | 🟡 **deferred** — India ban (upstream network block); creds set + wiring verified; re-test after the 22nd or switch to email/webhook |
| Dashboard TOTP | ✅ configured + e2e-verified |
| On-VPS backups (6-hourly) | ✅ working |
| Backup restore drill | ✅ passed |
| Off-VPS backup copy | 🟡 1 user step (rsync from PC) |
