"""GET /api/downshock — the read-only forward-tracker monitor endpoint."""
from __future__ import annotations

import json


def test_downshock_unavailable_when_no_state(authed, tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNSHOCK_DIR", str(tmp_path / "absent"))
    r = authed.get("/api/downshock")
    assert r.status_code == 200
    assert r.json() == {"available": False}


def test_downshock_returns_state_and_history(authed, tmp_path, monkeypatch):
    d = tmp_path / "ds"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({
        "tracking_start": "2026-06-25", "forward_events": 3,
        "forward_cum_return": 0.05, "forward_sharpe_ann": 1.2,
    }))
    (d / "state.log.csv").write_text(
        "run_date,tracking_start,forward_events,forward_days,forward_cum_return,forward_sharpe_ann\n"
        "2026-06-25,2026-06-25,0,0,0.000000,None\n"
        "2026-06-25,2026-06-25,0,0,0.000000,None\n"      # same-day dupe -> collapsed
        "2026-06-26,2026-06-25,3,30,0.050000,1.2\n"
    )
    monkeypatch.setenv("DOWNSHOCK_DIR", str(d))
    j = authed.get("/api/downshock").json()
    assert j["available"] is True
    assert j["state"]["forward_events"] == 3
    assert [h["date"] for h in j["history"]] == ["2026-06-25", "2026-06-26"]
    assert j["history"][-1]["cum_return"] == 0.05
    assert j["history"][-1]["sharpe"] == 1.2
    assert j["history"][0]["sharpe"] is None


def test_downshock_requires_auth(client):
    assert client.get("/api/downshock").status_code in (401, 403)
