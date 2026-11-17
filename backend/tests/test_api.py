"""API: 조회·제어 엔드포인트가 시뮬레이션 데이터 위에서 동작하는지 확인한다."""
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    os.environ.update(DB_PATH=str(tmp_path_factory.mktemp("db") / "fund.db"), SCHEDULER="off",
                      DATA_MODE="mock", LLM_MODE="mock", TRADING_MODE="paper")
    from app import api
    from app.backtest import simulate
    from app.clock import SimClock, at
    from app.fund import Fund
    with TestClient(api.app) as c:
        f = api.ctx.fund
        sim = Fund(f.cfg, f.store, f.data.provider, f.llm.client, SimClock(at(date(2026, 10, 1), __import__("datetime").time(9))), historical=True)
        simulate(sim, date(2026, 10, 1), date(2026, 10, 30))
        yield c


def wait_idle(c):
    for _ in range(600):
        if c.get("/api/overview").json()["status"]["job"] is None:
            return
        time.sleep(0.1)
    raise AssertionError("작업이 끝나지 않음")


def test_read_endpoints(client):
    o = client.get("/api/overview").json()
    assert o["mode"] == {"trading": "paper", "data": "mock", "llm": "mock"}
    assert o["equity"] == pytest.approx(o["cash"] + o["positions_value"], abs=0.01)
    eq = client.get("/api/equity").json()
    assert len(eq) == 22 and eq[0]["benchmark"] == 100_000
    pos = client.get("/api/positions").json()
    assert all("name" in p and "weight" in p for p in pos["positions"])
    trades = client.get("/api/trades").json()
    assert trades
    swing = next(t for t in trades if t["desk"] == "swing")
    detail = client.get(f"/api/trades/{swing['id']}").json()
    assert detail["decision"]["rationale"] and len(detail["trail"]["reports"]) == 7 and detail["trail"]["macro"]
    assert detail["lesson"]["lesson"] and len(detail["orders"]) >= 2
    ms = client.get("/api/meetings").json()
    m = client.get(f"/api/meetings/{ms[0]['id']}").json()
    assert m["macro"]["payload"]["regime"] and m["symbols"]
    debated = [s for s in client.get(f"/api/meetings/{ms[-1]['id']}").json()["symbols"] if s["outcome"] == "토론 진행"]
    assert debated and len(debated[0]["debate"]) == 4 and debated[0]["decision"]
    staff = client.get("/api/agents").json()
    assert {a["id"] for a in staff} >= {"chart", "head", "risk_engine", "compliance"}
    assert any(a["hit_rate"] is not None for a in staff)
    assert "수석 트레이더" in client.get("/api/agents/head").json()["system_prompt"]
    r = client.get("/api/risk").json()
    assert r["exposure"]["limits"]["max_position_pct"] == 0.10 and r["exposure"]["largest_position_pct"] <= 0.115
    p = client.get("/api/performance").json()
    assert p["metrics"]["days"] == 22 and p["monthly"][0]["month"] == "2026-10" and p["lessons"]
    sym = client.get(f"/api/symbols/{swing['symbol']}").json()
    assert len(sym["bars"]) == 180 and sym["orders"] and sym["reports"]
    assert client.get("/api/symbols/NOPE").status_code in (200, 404)
    assert client.get("/api/trades/999999").status_code == 404


def test_controls(client):
    assert client.post("/api/control/kill", json={"on": True}).json() == {"kill_switch": True}
    assert "킬 스위치" in client.get("/api/overview").json()["status"]["entries_blocked"]
    assert client.post("/api/control/kill", json={"on": False}).json() == {"kill_switch": False}
    before = len(client.get("/api/meetings").json())
    assert client.post("/api/control/meeting").status_code == 200
    wait_idle(client)
    assert len(client.get("/api/meetings").json()) == before + 1
    assert client.get("/api/overview").json()["status"]["job_error"] is None
    assert client.post("/api/control/reset", json={"confirm": "no"}).status_code == 400
    assert client.post("/api/control/reset", json={"confirm": "RESET"}).json() == {"reset": True}
    o = client.get("/api/overview").json()
    assert o["equity"] == 100_000 and client.get("/api/trades").json() == []
