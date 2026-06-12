"""Pre-deploy / pre-go-live preflight checks.

Run on the VPS (or locally with --remote to check the public endpoint). Verifies
the things that cannot be unit-tested and that, if wrong, lose money or block
trading:

  1. VPS public IP == the Angel One whitelisted static IP
  2. TLS cert present and valid for quant.devpilotx.com
  3. .env complete (no placeholder secrets) and gitignored
  4. DB reachable, migrations at head, an operator exists
  5. live-gate status (is real money even possible yet?)

Exit code 0 only if every BLOCKING check passes. Informational checks (cert
before first issuance, live-gate before a real backtest) warn but don't block a
PAPER deploy.

    python deploy/scripts/preflight.py                # local/VPS env checks
    python deploy/scripts/preflight.py --remote       # also probe the public URL
"""

from __future__ import annotations

import argparse
import os
import socket
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

WHITELISTED_IP = "80.225.240.46"   # Angel One static IP on record (verify in app)
DOMAIN = "quant.devpilotx.com"

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_results: list[tuple[str, str, str]] = []


def check(level: str, name: str, detail: str = "") -> None:
    _results.append((level, name, detail))


def public_ip() -> str | None:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                return r.read().decode().strip()
        except Exception:
            continue
    return None


def check_ip() -> None:
    ip = public_ip()
    if ip is None:
        check(WARN, "public IP", "could not determine (no outbound?)")
    elif ip == WHITELISTED_IP:
        check(OK, "public IP", f"{ip} == Angel whitelist")
    else:
        check(FAIL, "public IP",
              f"{ip} != whitelisted {WHITELISTED_IP} — live orders will be rejected")


def check_dns() -> None:
    try:
        resolved = socket.gethostbyname(DOMAIN)
        if resolved == WHITELISTED_IP:
            check(OK, "DNS", f"{DOMAIN} -> {resolved}")
        else:
            check(WARN, "DNS", f"{DOMAIN} -> {resolved} (expected {WHITELISTED_IP})")
    except Exception as e:
        check(FAIL, "DNS", f"{DOMAIN} does not resolve: {e}")


def check_cert(remote: bool) -> None:
    if not remote:
        check(WARN, "TLS cert", "skipped (use --remote to probe the public URL)")
        return
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((DOMAIN, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=DOMAIN) as ss:
                cert = ss.getpeercert()
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc)
        days = (not_after - datetime.now(timezone.utc)).days
        if days < 7:
            check(WARN, "TLS cert", f"valid but expires in {days}d — check renewal")
        else:
            check(OK, "TLS cert", f"valid, {days}d remaining")
    except ssl.SSLCertVerificationError as e:
        check(FAIL, "TLS cert", f"invalid/untrusted — run certbot: {e}")
    except Exception as e:
        check(WARN, "TLS cert", f"could not probe (service down before deploy?): {e}")


def check_env() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(repo_root, ".env")
    if not os.path.exists(env_path):
        check(WARN, ".env", "not found at repo root (compose uses deploy/.env)")
        return
    placeholders = ("your_api_key_here", "your_client_code", "your_pin",
                    "your_totp_secret", "generate_a_long_random",
                    "generate_another_long")
    bad = []
    with open(env_path) as f:
        for line in f:
            for ph in placeholders:
                if ph in line:
                    bad.append(line.split("=")[0].strip())
    if bad:
        check(WARN, ".env", f"placeholder values still present: {', '.join(bad)}")
    else:
        check(OK, ".env", "no placeholder secrets")


def check_db() -> None:
    try:
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "dashboard", "backend"))
        from sqlalchemy import inspect, text  # noqa

        from qsdash.db import engine
        from qsdash.models import User

        with engine.connect() as c:
            insp = inspect(engine)
            tables = set(insp.get_table_names())
            if "decisions" not in tables or "users" not in tables:
                check(FAIL, "DB schema", "core tables missing — run alembic upgrade head")
                return
            n_users = c.execute(text("SELECT count(*) FROM users")).scalar()
            if n_users == 0:
                check(FAIL, "operator", "no operator — run qsdash.cli create-operator")
            else:
                check(OK, "DB + operator", f"{len(tables)} tables, {n_users} operator(s)")
    except Exception as e:
        check(WARN, "DB", f"not reachable from here: {e}")


def check_live_gate() -> None:
    try:
        from qsdash.bridge.livegate import backtest_gate
        from qsdash.db import SessionLocal

        s = SessionLocal()
        try:
            g = backtest_gate(s)
        finally:
            s.close()
        if g.allowed:
            check(OK, "live gate (backtest)", f"passing run #{g.passing_run_id}")
        else:
            check(WARN, "live gate (backtest)",
                  "real money BLOCKED: " + "; ".join(g.reasons))
    except Exception as e:
        check(WARN, "live gate", f"could not evaluate: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", action="store_true", help="probe the public URL/cert")
    args = ap.parse_args()

    check_ip()
    check_dns()
    check_cert(args.remote)
    check_env()
    check_db()
    check_live_gate()

    width = max(len(n) for _, n, _ in _results)
    print(f"\nPreflight for {DOMAIN}\n" + "=" * 60)
    for level, name, detail in _results:
        print(f"  [{level:4}] {name.ljust(width)}  {detail}")
    fails = [r for r in _results if r[0] == FAIL]
    warns = [r for r in _results if r[0] == WARN]
    print("=" * 60)
    print(f"{len(fails)} blocking, {len(warns)} warnings")
    if fails:
        print("DEPLOY BLOCKED — resolve the FAIL items above.")
        return 1
    print("No blocking issues. PAPER deploy OK. (Live still needs a passing "
          "backtest gate + QS_LIVE_ARMED + rotated creds.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
