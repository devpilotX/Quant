"""Generate the investor report (.docx) — quantsys, Forward Study 2.

The rendered file is intentionally gitignored (*.docx policy); this generator
is the tracked source of truth. Honesty contract: every performance-related
claim in the document is either a verified platform fact, a frozen research
record with its multiple-testing verdict attached, or explicitly labeled an
outcome to be measured. No profitability is promised anywhere.

Run:  python scripts/_make_investor_report.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / f"quantsys-investor-report-{date.today().isoformat()}.docx"

ACCENT = RGBColor(0x1F, 0x3B, 0x5C)


def _style(doc: Document) -> None:
    n = doc.styles["Normal"]
    n.font.name = "Calibri"
    n.font.size = Pt(10.5)
    for lvl, sz in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        s = doc.styles[lvl]
        s.font.name = "Calibri"
        s.font.size = Pt(sz)
        s.font.color.rgb = ACCENT


def h(doc, text, lvl=1):
    doc.add_heading(text, level=lvl)


def p(doc, text, bold=False, italic=False, size=None):
    par = doc.add_paragraph()
    r = par.add_run(text)
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    return par


def bullets(doc, items):
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, htxt in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = htxt
        for par in cell.paragraphs:
            for r in par.runs:
                r.bold = True
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = str(v)
    if widths:
        for i, w in enumerate(widths):
            for row_ in t.rows:
                row_.cells[i].width = Inches(w)
    doc.add_paragraph()
    return t


def build() -> None:
    doc = Document()
    _style(doc)

    # ------------------------------------------------------------- title
    tp = doc.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = tp.add_run("\nquantsys\n")
    r.bold = True
    r.font.size = Pt(30)
    r.font.color.rgb = ACCENT
    for line, sz, it in (
        ("Auto-adaptive systematic trading platform — Indian markets (NSE/BSE cash + F&O)", 13, False),
        ("Investor & Stakeholder Report — complete system description and current status", 12, False),
        (f"As of {date.today().strftime('%d %B %Y')} · Forward Study 2 (paper) · repo main @ d2ce86a", 10, True),
        ("CONFIDENTIAL — contains operational detail; distribute only under NDA", 9, True),
    ):
        q = doc.add_paragraph()
        q.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rr = q.add_run(line)
        rr.font.size = Pt(sz)
        rr.italic = it
    doc.add_page_break()

    # -------------------------------------------------------- exec summary
    h(doc, "1. Executive summary")
    p(doc, "quantsys is a complete, tested, deployed systematic trading platform: a "
           "deterministic decision engine, an event-driven backtester that drives the "
           "identical code, a broker execution layer on Angel One SmartAPI, a "
           "full-audit dashboard control plane, and a standalone research kit with "
           "survivorship-bias-free NSE data. It runs today in PAPER mode on a VPS "
           "against live market data at a ₹15 crore paper float, executing six "
           "strategy sleeves under a pre-registered forward study.")
    p(doc, "The honest headline first: the 2017–2026 historical alpha search is CLOSED "
           "with a negative verdict — no strategy cleared our five-criterion "
           "statistical deployment gate net of realistic Indian costs. What runs now "
           "is the scientifically sanctioned continuation: a forward-only, "
           "pre-registered paper study collecting evidence that cannot be overfit "
           "because it does not exist yet. Paper results, good or bad, do not arm "
           "real-money trading by themselves; a hard two-lock go-live gate (including "
           "credential rotation and SEBI algo registration) stands between this system "
           "and any real order.", bold=True)
    p(doc, "This document describes the platform end-to-end, the day-1 operational "
           "review of 2 July 2026 (four implementation defects found and fixed the "
           "same day, verified by 251 automated tests), the resulting Forward Study 2 "
           "registration, and every material risk. Nothing in it should be read as a "
           "promise of profitability.")

    # ------------------------------------------------------- architecture
    h(doc, "2. System architecture")
    p(doc, "One deterministic pass per decision bar (15-minute bars in the current "
           "study). The same decide() function is driven by the backtester, the paper "
           "engine, and (when ever armed) live execution — there is no separate "
           "'backtest logic' to drift from reality. Every stage appends to a "
           "machine-readable audit trail persisted per decision.")
    table(doc,
          ["Stage", "Function", "Module"],
          [("1 Risk pre-pass", "kill switches, drawdown throttle", "risk/engine.py"),
           ("2 Capital tier", "T1 (₹1L) → T6 (₹20cr) gates + interpolated params, hysteresis", "portfolio/tiers.py"),
           ("3 Regime", "Gaussian HMM → P(calm_trend / calm_range / turbulent), fallback ladder", "regime/"),
           ("4 Gating", "universe size and strategy slots by tier", "engine/decision.py"),
           ("5 Signals", "six-sleeve ensemble (registry drop-ins)", "strategies/"),
           ("6 Stop vetoes", "hard/trailing stops, re-entry cooldowns", "risk/engine.py"),
           ("7 Allocation", "fractional Kelly × regime weights × learned regime tilt", "portfolio/allocation.py"),
           ("8 Sizing", "risk-fraction qty → vol target → exposure caps → lots → cost gate", "portfolio/, risk/rules.py"),
           ("9 Order diff", "anti-churn bands, equity-scaled dust floor, urgency", "engine/orders.py")],
          widths=[1.3, 3.6, 1.6])
    p(doc, "Persistence: every stateful component serialises to JSON and round-trips "
           "bit-identically (proven by test). Postgres holds every decision, order, "
           "fill, position, equity tick and audit event; the dashboard renders "
           "per-trade explainability straight from that record.")

    # ------------------------------------------------------------ sleeves
    h(doc, "3. Strategy sleeves")
    p(doc, "Strategies are independent, individually toggleable registry plug-ins. "
           "Each emits declarative signals (ceasing to emit IS the exit), carries its "
           "own stop distance, and is allocated capital only by the central Kelly "
           "allocator — no sleeve sizes itself. Historical verdicts below are the "
           "frozen research record, stated without spin.")
    table(doc,
          ["Sleeve", "Type / cadence", "Historical prior (frozen)", "Study-2 role"],
          [("trend", "TSMOM + breakout, hysteresis, 90-min timeframe",
            "no edge (deflated Sharpe ≤ 0.11)", "plumbing + forward log"),
           ("meanrev", "Engle-Granger/OU cointegrated pairs, episode-frozen",
            "dead (re-test failed all five gate criteria)", "plumbing + forward log"),
           ("expiry", "monthly F&O expiry-week fade",
            "borderline-luck (deflated 0.33)", "forward log"),
           ("factor", "12L/12S market-neutral momentum + low-vol, monthly, self-seeded daily panel",
            "OOS-survivor, sub-gate (deflated 0.074→0.583 in combo)", "PRIMARY candidate"),
           ("downshock", "event: z ≤ −3.5 daily shock on ≥2× volume → short next session, hold 10, ≤5 concurrent",
            "project-best lead: hold-out Sharpe 1.75, MC 0.002, PBO 0.31 — but deflated 0.46 → FAILED the gate",
            "promoted to paper sleeve; zero-risk tracker continues as control"),
           ("tom", "turn-of-month long index futures (−2/+3 weekdays)",
            "suggestive, NOT significant on our universe (+12.2 vs +4.1 bps/day, t = 1.53)",
            "new hypothesis, trivial cost"),
           ("reversal (DARK)", "5-day cross-sectional reversal, 8L/8S weekly",
            "REJECTED pre-deployment: whole 3×3 grid net-negative 2017–26 "
            "(Sharpe −0.58…−0.94, PBO 0.84) — costs eat it",
            "implemented + tested, ships disabled; documented rejected arm"),
           ("voloptions", "IV-vs-RV defined-risk vertical spreads",
            "unvalidated", "disabled until a live option-chain feed exists")],
          widths=[1.0, 1.9, 2.2, 1.4])
    p(doc, "The rejected reversal arm is deliberately listed: refusing to deploy a "
           "known cost-sink — and accounting for its 9 evaluated configurations in "
           "the multiple-testing ledger (cumulative n_trials = 30) — is what "
           "separates a research process from curve-fitting.", italic=True)

    # -------------------------------------------------- allocation/adaptive
    h(doc, "4. Capital allocation and market-condition adaptation")
    p(doc, "Three adaptive layers stack multiplicatively, all bounded and audited:")
    bullets(doc, [
        "Fractional Kelly per sleeve: f = clip(0.30 · µ/σ², 0, 0.35) from online EWMA "
        "edge statistics measured on virtual unit books (f=1 shadow positions), with "
        "a zero-mean prior that shrinks young or lucky track records; incubation "
        "floor for new sleeves, withdrawn early on clearly negative evidence; gross "
        "Kelly cap 0.80. Capital migrates to what is actually earning — walk-forward "
        "by construction.",
        "Regime overlay: an in-repo diagonal-Gaussian HMM classifies "
        "calm-trend / calm-range / turbulent with probability blending (no cliff "
        "edges) and a deterministic volatility-percentile fallback; each regime "
        "carries a risk scaler (turbulent = 0.35×) and per-sleeve prior weights.",
        "NEW — learned regime tilt: the engine additionally keeps per-(sleeve × "
        "regime) edge statistics, soft-assigned by the regime probabilities that "
        "ruled when each position was decided, and tilts each sleeve's allocation by "
        "clip(1 + 0.4·Σ p·tanh(t/2), 0.5×, 1.5×). The ensemble literally learns "
        "which weather each sleeve earns in, within hard bounds, with every tilt "
        "logged to the audit trail.",
        "Paper-exploration floor (paper mode ONLY): each sleeve receives a minimum "
        "5% Kelly allocation so the study exercises the full order→fill→P&L path "
        "even under the honest no-edge prior. Live and backtest modes are never "
        "affected — their allocations stay purely evidence-based.",
    ])

    # ---------------------------------------------------------------- risk
    h(doc, "5. Risk management stack")
    p(doc, "Every control below is enforced in code and covered by dedicated tests "
           "(caps shrink positions monotonically and group-jointly — no cap can "
           "orphan one leg of a hedge).")
    table(doc,
          ["Control", "Setting (Study 2)"],
          [("Per-trade risk", "0.6% of equity at the stop distance"),
           ("Portfolio vol target", "13% annualised (EWMA covariance, shrinkage, clipped scaler)"),
           ("Per-instrument / sector / correlation-cluster caps", "25% / 50% / 40% of E (cluster at |ρ|>0.70)"),
           ("Net exposure / gross leverage", "1.5× / tier-laddered up to 2.5× at T6"),
           ("ADV participation", "≤5% of daily volume at T6"),
           ("Hard stops", "ATR-based, trailing for trend; re-entry cooldown 12 bars"),
           ("Daily loss kill", "−2.5% of session-open equity → flat, auto re-arms next session"),
           ("Drawdown throttle", "risk fraction scales linearly to 0 at 18% drawdown"),
           ("Hard kill", "20% drawdown → flatten, manual re-arm only"),
           ("Reconciliation halt", "broker-vs-internal mismatch → freeze (no orders at all)"),
           ("Equity wipeout", "E ≤ 0 → immediate hard kill"),
           ("Study-level stop", "paper drawdown > 12% → study ends early (pre-registered)")],
          widths=[2.6, 3.9])

    # ---------------------------------------------------------------- costs
    h(doc, "6. Transaction-cost realism")
    p(doc, "Every paper fill is priced by the full June-2026 Indian fee schedule "
           "(re-verified quarterly): brokerage (₹20/0.25% per order; delivery ₹0), "
           "STT (Budget 2026 rates: futures sell 0.05%, delivery 0.1% both sides), "
           "exchange transaction charges, SEBI fee, stamp duty, 18% GST, plus a "
           "slippage haircut and a square-root market-impact term. The research "
           "convention charges equities at delivery rates (worst case). A tier cost "
           "gate blocks any new live/backtest trade whose expected edge does not "
           "clear a multiple of its round-trip cost — the ₹1-lakh account cost trap "
           "is enforced, not wished away.")

    # ---------------------------------------------------------- data/exec
    h(doc, "7. Market data and execution infrastructure")
    bullets(doc, [
        "Angel One SmartAPI: TOTP session management, instrument master refreshed "
        "daily (lot sizes, tokens, tick sizes — config values are only warm-start "
        "fallbacks), continuous near-month index-future aliases that re-roll "
        "automatically (NIFTY-FUT / BANKNIFTY-FUT).",
        "Self-healing websocket feed: supervisor + watchdog threads rebuild silent "
        "or dropped sockets with capped backoff and rate-limited re-auth; the feed "
        "PARKS outside session hours (the June overnight reconnect-churn incident "
        "is engineered out) — pinned smartapi-python 1.5.5 / websocket-client 0.59.0.",
        "Tick→bar aggregation with time-based completion: every 15-min bar "
        "completes within seconds of its boundary even for thin names, and the "
        "session close bar always completes (fixed 2 July 2026).",
        "Exactly ONE decision per bar on the full cross-section, stamped at bar "
        "time — live and backtest share the same clock semantics (fixed 2 July 2026).",
        "Paper broker fills at bar close with the full cost model; positions, cash "
        "and realized P&L persist transactionally with the fills, so the equity "
        "series is continuous across the daily engine recycle (fixed 2 July 2026).",
        "Idempotent, rate-limited OMS for the (gated) live path; broker is always "
        "ground truth; reconciliation mismatch freezes trading.",
    ])

    # ------------------------------------------------------------ dashboard
    h(doc, "8. Control plane and observability")
    bullets(doc, [
        "FastAPI backend with argon2id + mandatory TOTP auth, lockout, CSRF, "
        "re-auth window; Next.js dark terminal UI with 12 live views.",
        "Per-trade explainability: signal, regime probabilities, Kelly f, vol "
        "scaler, every cap/veto that touched the order — from the decision audit "
        "trail, not reconstructed.",
        "Operator command queue: pause (no flatten), flatten, kill re-arm, "
        "strategy toggles, config overrides, durable paper capital — all engine-"
        "acknowledged with results.",
        "Ops automation (systemd): 08:50 IST pre-open engine recycle (cash-safe), "
        "09:55 self-check (GREEN/RED to an audit log; RED fails the unit loudly), "
        "20:30 down-shock tracker, Saturday 11:00 weekly history refresh + "
        "walk-forward backtest.",
        "Daily paper-trading telemetry: decisions, orders, fills, per-strategy "
        "P&L attribution by regime, equity ticks — queryable and exported to the "
        "dashboard.",
    ])

    # ---------------------------------------------------------- methodology
    h(doc, "9. Research methodology — and the honest verdict")
    p(doc, "The platform's evaluation machinery is deliberately hostile to self-"
           "deception: walk-forward IS/OOS splits, deflated Sharpe (Bailey & López "
           "de Prado — corrects for how many configurations were tried), Probability "
           "of Backtest Overfitting via CSCV, purged & embargoed cross-validation, "
           "and Monte-Carlo resampling of P(SR<0). The deployment gate requires ALL "
           "of: OOS Sharpe ≥ 0.80, deflated ≥ 0.95, P(SR<0) ≤ 0.10, PBO ≤ 0.50, and "
           "positive net CAGR.")
    p(doc, "Verdict of the 2017–2026 search (docs/RESEARCH_CLOSEOUT.md): nothing "
           "cleared that gate. The best lead ever found — the down-shock "
           "underreaction — passed 4 of 5 criteria and failed deflation (0.46 < "
           "0.95). We reported it as DEAD and stopped, because the alternative "
           "(retuning until the number crosses) manufactures false edges (measured "
           "PBO 0.84 on that path). The one legitimate continuation the closeout "
           "names is a forward-only, pre-registered study on data that did not "
           "exist yet. That is what is running.", bold=True)

    # ------------------------------------------------------------- study 2
    h(doc, "10. Forward Study 2 (pre-registered, running)")
    p(doc, "Registered 2 July 2026 after market close, superseding Study 1 the same "
           "day. Study 1 ran exactly one session; its day-1 operational review found "
           "four implementation defects — decisive among them a paper-cash "
           "persistence flaw that would have made the registered equity metrics "
           "unmeasurable from day 2. Zero evaluation reads had occurred, so the "
           "supersession has the same evidentiary status as registering correctly "
           "one day earlier. Study 1's registration carries its appended final "
           "verdict; the equity series continues unbroken.")
    table(doc,
          ["Parameter", "Frozen value"],
          [("Venue", "paper engine on live Angel One data, full cost model on every fill"),
           ("Float / tier", "₹15 crore → T6: 40-instrument view, 7 strategy slots, 2.5× gross cap"),
           ("Universe", "48 liquid large-caps + NIFTY/BANKNIFTY near-month futures + NIFTY index (regime source)"),
           ("Clock", "15-minute bars, IST; one decision per bar incl. the close bar"),
           ("Sleeves", "trend, meanrev, expiry, factor (primary), downshock, tom — reversal dark (rejected arm)"),
           ("Adaptation under test", "Kelly + regime priors + learned regime tilt (β=0.4, clip 0.5–1.5)"),
           ("First read", "5 January 2027 (≥6 months), then quarterly; observational only"),
           ("Change policy", "ANY parameter change ends the study and requires fresh registration"),
           ("Study stop", "paper drawdown > 12% → early end, verdict 'risk stack insufficient'"),
           ("Gate to DISCUSS live", "forward net Sharpe ≥ 0.80, deflated ≥ 0.95, P(SR<0) ≤ 0.10, net CAGR > 0, "
            "PLUS rotated credentials and SEBI algo registration; QS_LIVE_ARMED=0 throughout")],
          widths=[1.7, 4.8])

    # ------------------------------------------------------------ day1 work
    h(doc, "11. The 2 July 2026 engineering review (this release)")
    p(doc, "Market-hours review of the study's first live session, root-caused from "
           "the recorded decision/order stream; all fixes deployed the same evening "
           "and verified.")
    table(doc,
          ["Defect found (live evidence)", "Fix (verified by test)"],
          [("3–5 decisions per 15-min bar, each on a partial universe cross-section "
            "(decision ids 1s apart; 48 decisions in 15 bars)",
            "time-based bar completion + accumulate-and-decide-once per bucket at "
            "bar timestamp; stragglers absorbed without re-deciding"),
           ("No decision ever ran on the 15:15 close bar (nothing crosses 15:30)",
            "aggregator time-flush completes the close bar; close-bar decide covered by test"),
           ("1-share churn (MARUTI buy/sell/buy on consecutive bars) through a flat "
            "₹5k dust floor meaningless at ₹15cr",
            "equity-scaled dust floor: max(₹5k, 1bp of equity) — ₹15k at the study float"),
           ("Paper cash/realized rebuilt from bootstrap at every restart; the ₹15cr "
            "float re-applied over the carried book → phantom equity at each 08:50 "
            "recycle, compounding from day 2",
            "cash ledger persists transactionally with fills; float applies only on "
            "operator change and never over an open book; VPS data migrated in place"),
           ("Weekly backtest unit hung (3h wall, <1s CPU) in the broker's Sunday "
            "maintenance window — SDK has no HTTP timeouts",
            "socket default timeout (60s) + timer moved to Saturday 11:00 IST")],
          widths=[3.3, 3.2])
    p(doc, "Also answered: 'why are index futures not trading?' — they are. Both "
           "index futures bought their risk-capped 1-lot minimum at 09:30:00 on "
           "2 July and held it all session (65 = one NIFTY lot, 30 = one BANKNIFTY "
           "lot); no further orders because the trend targets were unchanged — "
           "exactly the designed min-lot promotion behaviour. The turn-of-month "
           "sleeve now gives the futures a second, independent engine.")
    h(doc, "Pre-deployment sanity checks (pre-declared, report-only)", 2)
    p(doc, "Run on the cached point-in-time NSE daily panel (2017–2026, 48 config "
           "equities, 28 bps round-trip delivery costs) before enabling the new "
           "sleeves — configs were frozen BEFORE the numbers were seen, and the one "
           "that failed was rejected rather than tuned:")
    bullets(doc, [
        "reversal (5d/8, weekly): REJECTED — the entire 3×3 neighborhood is net-"
        "negative (deployed cell Sharpe −0.78, −8.7% CAGR; grid PBO 0.84). Ships "
        "dark.",
        "turn-of-month (−2/+3): +12.2 bps/day in-window vs +4.1 outside, t = 1.53 — "
        "suggestive, not significant; deployed at exploration size where cost is "
        "trivial. Stated as such in the registration.",
        "downshock: NOT re-run (binding stop-rule) — the frozen research record "
        "stands: hold-out Sharpe 1.75, deflated 0.46 (failed gate). Forward "
        "evidence is the only open question.",
    ])

    # ------------------------------------------------------------- status
    h(doc, "12. Current status snapshot (2 July 2026, post-deploy)")
    table(doc,
          ["Item", "State"],
          [("Paper equity", "₹15,00,09,192 (day 1: realized −₹12,112 — fee-dominated; unrealized +₹14,164)"),
           ("Open positions", "29 (incl. NIFTY-FUT 1 lot, BANKNIFTY-FUT 1 lot); gross ₹56.3L"),
           ("Engine", "quantsys-engine-paper healthy; cash ledger restored across restart "
            "(verified in logs: 'paper broker cash restored: cash=146336152.14 realized=-12111.65')"),
           ("Feed", "parked (market closed); self-healing supervisor armed for 09:05 pre-open"),
           ("Automated tests", "191 engine + 60 dashboard backend, ALL GREEN; ruff clean"),
           ("Ops timers", "recycle 08:50 / selfcheck 09:55 / downshock 20:30 daily; backtest Sat 11:00 — all active"),
           ("Self-check", "GREEN (restarts=0, heartbeat 0s, disk 13%)"),
           ("Resource envelope", "engine 365 MiB of 2 GiB cgroup; VPS disk 13% used"),
           ("Repository", "main @ d2ce86a pushed to GitHub == deployed on VPS"),
           ("Live trading", "OFF (QS_LIVE_ARMED=0); blocked by design on credential rotation + SEBI registration")],
          widths=[1.8, 4.7])

    # ---------------------------------------------------------- governance
    h(doc, "13. Governance: the two-lock go-live gate")
    p(doc, "Real-money trading requires BOTH locks, in addition to the study gate "
           "above: (1) the dashboard operator chain — fresh re-auth, typed "
           "confirmation phrase, deployable-capital cap, clean book; (2) the engine "
           "livegate — a real (non-synthetic) passing backtest on record, a "
           "connected execution adapter, and the out-of-band QS_LIVE_ARMED "
           "environment switch. Two standing preconditions are unmet by policy: the "
           "Angel One credentials exposed in the 11 June 2026 leak incident must be "
           "rotated (paper operation is unaffected; the exposure is documented and "
           "contained), and SEBI algo registration via the broker must be completed. "
           "The engine additionally rejects live mode outright until the execution "
           "adapter has been proven against the live broker.")

    # ------------------------------------------------------------- risks
    h(doc, "14. Risk factors (read this section)")
    bullets(doc, [
        "No demonstrated edge: the historical research verdict is negative. The "
        "forward study measures whether any sleeve earns net of costs in real "
        "time; the honest prior is that most will not.",
        "Paper ≠ live: paper fills at bar close with modeled slippage/impact "
        "cannot capture queue position, partial fills, or stressed liquidity. A "
        "positive paper result would still need live-execution validation at "
        "small size.",
        "Statistical fragility: six months of forward data is a small sample; "
        "the pre-registered gate (deflated Sharpe, Monte-Carlo) exists precisely "
        "because single-period Sharpe ratios mislead.",
        "Regime dependence: trend/factor/event sleeves have documented bad "
        "weather (e.g. short squeezes in V-shaped recoveries for downshock). The "
        "risk stack bounds, but does not eliminate, drawdowns; the 12% study "
        "stop is the backstop.",
        "Operational: broker API changes, feed outages, exchange rule changes "
        "(lot sizes, STT) — mitigated by daily instrument-master refresh, "
        "self-healing feed, pinned dependencies, quarterly fee re-verification, "
        "and loud self-checks, but not eliminated.",
        "Security: the June 2026 credential exposure is contained and rotation "
        "is a hard precondition for live; secrets live outside the repository "
        "and rendered reports are excluded from version control by policy.",
        "Key-person and single-broker concentration: one operator, one broker "
        "API, one VPS — acceptable for a paper study, on the roadmap before any "
        "live capital.",
        "Nothing in this document is investment advice or an offer of "
        "securities; past (simulated) performance does not indicate future "
        "results; capital deployed against this system can be lost in full.",
    ])

    # ------------------------------------------------------------- roadmap
    h(doc, "15. Roadmap")
    bullets(doc, [
        "Rotate Angel One credentials + fill Telegram alert credentials (hard "
        "precondition, operator action).",
        "Forward Study 2 first read: 5 January 2027; quarterly thereafter; "
        "verdicts published against the pre-registered gate.",
        "Verify the single-decide cadence on the first full live session "
        "(3 July): expect exactly 25 decisions and one 15:15 close-bar decision.",
        "Off-VPS backup replication + restore drill; SEBI algo registration "
        "before any real order; option-chain feed before the voloptions sleeve "
        "is considered.",
        "If (and only if) a sleeve clears the full gate on forward data: "
        "staged live pilot at minimum size behind the two-lock gate.",
    ])

    p(doc, "")
    p(doc, "Prepared from the repository state, live database, and deployment logs "
           "of 2 July 2026. Every number is reproducible from the audit trail; the "
           "pre-registered documents (docs/FORWARD_STUDY.md, docs/FORWARD_STUDY_2.md, "
           "docs/RESEARCH_CLOSEOUT.md, docs/PILLAR4_EVENT_DRIVEN.md) are the binding "
           "record.", italic=True, size=9)

    OUT.parent.mkdir(exist_ok=True)
    doc.save(OUT)
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    build()
