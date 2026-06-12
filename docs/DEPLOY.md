# Deploying to the VPS (quant.devpilotx.com)

Prereqs: Ubuntu VPS with Docker + compose plugin, DNS A record
`quant.devpilotx.com → VPS IP`, ports 80/443 open. The VPS IP must match the
Angel One whitelisted static IP (verify in SmartAPI app settings).

## 1. First deployment

```bash
git clone https://github.com/devpilotX/Quant.git && cd Quant/deploy
cp .env.example .env && nano .env          # set POSTGRES_PASSWORD + secrets

# TLS first issuance (nginx serves the challenge on :80)
docker compose up -d nginx               # will 502 on /, fine
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
  -d quant.devpilotx.com --email you@example.com --agree-tos --no-eff-email
docker compose restart nginx

# database + operator
docker compose up -d postgres redis
docker compose run --rm api python -m qsdash.cli init-db
docker compose run --rm api python -m qsdash.cli create-operator --username <you>
#   ^ scan the printed otpauth:// URI into your authenticator NOW — shown once

docker compose up -d                     # everything else
```

Login at https://quant.devpilotx.com. Default mode is PAPER.

## 2. Operations

| task | command |
|---|---|
| logs | `docker compose logs -f engine api` |
| restart engine only | `docker compose restart engine` |
| backups (auto, 6h, keep 14) | `ls deploy/backups/` |
| manual backup | `docker compose exec postgres pg_dump -U quantsys -Fc quantsys > backups/manual.dump` |
| restore | `./scripts/restore.sh backups/<file>.dump` (prints the swap step) |
| update code | `git pull && docker compose build && docker compose up -d` |
| reset operator password | `docker compose run --rm api python -m qsdash.cli reset-password --username <you>` |

Off-VPS backup copy: add a cron on another machine:
`rsync -az vps:/path/Quant/deploy/backups/ ./quantsys-backups/` — do not skip
this; a VPS disk is not a backup.

## 3. Security checklist before going live (real money)

- [ ] `ANGEL_WEBHOOK_SECRET` set and the postback relay signs requests
- [ ] `/dbadmin` IP allowlist enabled in `nginx/quant.conf` (it ships
      commented out — pgweb has NO auth of its own)
- [ ] Optional `IP_ALLOWLIST` for the whole dashboard in `.env`
- [ ] Rotate any credential that ever touched a repo or chat
- [ ] `docker compose exec api python -c "from qsdash.config import settings; assert settings.cookie_secure"`
- [ ] SEBI algo registration via broker done (legal prerequisite)
- [ ] Restore drill performed once (`restore.sh` on a fresh dump)

## 4. Local development (Windows box)

```powershell
# API  (uses repo .env: local Postgres, COOKIE_SECURE=false, no Redis)
C:\Users\Dipan\.venvs\quant\Scripts\python.exe -m uvicorn qsdash.main:app --port 8000
# engine (synthetic paper session)
C:\Users\Dipan\.venvs\quant\Scripts\python.exe -m qsdash.bridge.runner --synthetic --bars 4000
# frontend
cd dashboard\frontend; npm run dev      # http://localhost:3000 (proxies /api,/ws)
# tests
cd dashboard\backend; ...python.exe -m pytest tests -q
```
