"""대시보드용 HTTP API 와 정적 파일 서빙.

인증이 없는 로컬 도구다. 킬 스위치·초기화 같은 제어 엔드포인트가 있으므로 인터넷에 그대로 노출하지 말 것.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date, time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import backtest as bt
from .agents.pipeline import MARKET, SCALP
from .agents.roster import AGENTS
from .clock import SimClock, add_trading_days, at, is_market_open, iso, last_completed_session
from .config import load_config
from .fund import Fund, build_fund
from .performance import agent_stats, daily_equity, metrics, monthly_returns
from .scheduler import Scheduler
from .store import SCHEMA

log = logging.getLogger("fund.api")
STATIC = Path(__file__).parent / "static"


class EventHub:
    """스레드에서 발생한 이벤트를 SSE 구독자에게 전달한다."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def publish(self, topic: str, data: dict) -> None:
        if self.loop and self.clients:
            self.loop.call_soon_threadsafe(self._fanout, {"topic": topic, **data})

    def _fanout(self, event: dict) -> None:
        for q in list(self.clients):
            if q.qsize() < 200:
                q.put_nowait(event)


class App:
    fund: Fund
    scheduler: Scheduler
    hub = EventHub()
    job: dict | None = None
    job_error: str | None = None


ctx = App()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config()
    ctx.fund = build_fund(cfg)
    ctx.hub.loop = asyncio.get_running_loop()
    ctx.fund.store.subscribe(ctx.hub.publish)
    ctx.scheduler = Scheduler(ctx.fund)
    if os.getenv("SCHEDULER", "on").lower() != "off":
        ctx.scheduler.start()
    log.info("시작: 매매=%s 데이터=%s LLM=%s DB=%s", cfg.trading_mode, cfg.data_mode, cfg.llm_mode, cfg.db_path)
    yield
    ctx.scheduler.stop()


app = FastAPI(title="AI 헤지펀드 (모의투자)", lifespan=lifespan)


def run_job(kind: str, fn, detail: str = "") -> dict:
    """회의·시뮬레이션 같은 긴 작업을 백그라운드 스레드에서 하나씩만 돌린다."""
    if not ctx.scheduler.busy.acquire(blocking=False):
        raise HTTPException(409, "다른 작업(회의·시뮬레이션·스케줄러 틱)이 진행 중입니다. 잠시 후 다시 시도하세요.")
    ctx.job, ctx.job_error = {"kind": kind, "detail": detail, "started": iso(ctx.fund.clock.now())}, None

    def work():
        try:
            fn()
        except Exception as e:
            log.exception("작업 실패: %s", kind)
            ctx.job_error = f"{kind}: {type(e).__name__}: {e}"
        finally:
            ctx.job = None
            ctx.scheduler.busy.release()
            ctx.hub.publish("job", {"kind": kind, "done": True})

    threading.Thread(target=work, name=f"job-{kind}", daemon=True).start()
    ctx.hub.publish("job", {"kind": kind, "done": False})
    return {"started": kind}


def _name(symbol: str) -> str:
    try:
        return ctx.fund.data.profile(symbol).name
    except Exception:
        return symbol


def _with_names(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["name"] = _name(r["symbol"])
    return rows


# ── 조회 ──────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/overview")
def overview():
    f = ctx.fund
    now = f.clock.now()
    snap = f.portfolio.snapshot()
    init = f.portfolio.initial_capital
    prev = f.store.one("SELECT equity FROM equity WHERE day<? ORDER BY ts DESC LIMIT 1", (now.date().isoformat(),))
    base = prev["equity"] if prev else init
    m = metrics(f.store, init)
    budget = f.llm.budget()
    last = f.store.one("SELECT id, ts, kind, status, stage, summary FROM meetings ORDER BY id DESC LIMIT 1")
    first_bench = f.store.scalar("SELECT benchmark FROM equity WHERE benchmark IS NOT NULL ORDER BY ts LIMIT 1")
    bench_now = f.data.quote(f.cfg.fund.benchmark)
    bench_ret = round(bench_now / first_bench - 1, 5) if first_bench and bench_now else None
    return {
        "now": iso(now), "market_open": is_market_open(now),
        "mode": {"trading": f.cfg.trading_mode, "data": f.cfg.data_mode, "llm": f.cfg.llm_mode},
        "models": {"research": f.cfg.llm.research_model, "senior": f.cfg.llm.senior_model},
        "equity": snap["equity"], "cash": snap["cash"], "cash_pct": snap["cash_pct"],
        "positions_value": snap["positions_value"], "positions_count": len(snap["positions"]),
        "initial_capital": init,
        "day_pnl": round(snap["equity"] - base, 2), "day_pnl_pct": round(snap["equity"] / base - 1, 5) if base else 0,
        "total_pnl": round(snap["equity"] - init, 2), "total_return": round(snap["equity"] / init - 1, 5),
        "benchmark": f.cfg.fund.benchmark, "benchmark_return": bench_ret,
        "sharpe": m.get("sharpe"), "max_drawdown": m.get("max_drawdown"),
        "status": {
            "kill_switch": f.risk.kill_switch, "halted": f.risk.halted, "halt_reason": f.store.get_state("halt_reason"),
            "daily_stop": f.risk.daily_stop_active(), "entries_blocked": f.risk.entries_blocked(),
            "scheduler_paused": ctx.scheduler.paused, "scheduler_error": ctx.scheduler.last_error,
            "job": ctx.job, "job_error": ctx.job_error, "last_sim_day": f.store.get_state("last_sim_day"),
        },
        "llm": {"spent": round(budget.spent, 4), "budget": budget.budget, "level": budget.level,
                "total_cost": m["llm_cost_usd"]},
        "last_meeting": last,
    }


@app.get("/api/equity")
def equity():
    f = ctx.fund
    rows = daily_equity(f.store)
    init = f.portfolio.initial_capital
    first = next((r["benchmark"] for r in rows if r["benchmark"]), None)
    return [{"day": r["day"], "equity": r["equity"], "cash": r["cash"],
             "benchmark": round(init * r["benchmark"] / first, 2) if first and r["benchmark"] else None} for r in rows]


@app.get("/api/positions")
def positions():
    snap = ctx.fund.portfolio.snapshot()
    return {"equity": snap["equity"], "cash": snap["cash"], "cash_pct": snap["cash_pct"],
            "positions": _with_names(snap["positions"]),
            "limits": {"max_sector_pct": ctx.fund.cfg.risk.max_sector_pct, "min_cash_pct": ctx.fund.cfg.risk.min_cash_pct},
            "sectors": [{"sector": k, "value": round(v, 2), "weight": round(v / snap["equity"], 4)}
                        for k, v in sorted(snap["sector_value"].items(), key=lambda x: -x[1])]}


@app.get("/api/orders")
def orders(limit: int = 200):
    return _with_names(ctx.fund.store.query("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (min(limit, 1000),)))


@app.get("/api/trades")
def trades(limit: int = 300):
    return _with_names(ctx.fund.store.query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (min(limit, 2000),)))


def _trail(meeting_id: int, symbol: str) -> dict:
    s = ctx.fund.store
    return {
        "meeting": s.one("SELECT id, ts, kind, status, summary FROM meetings WHERE id=?", (meeting_id,)),
        "macro": s.one("SELECT * FROM reports WHERE meeting_id=? AND symbol=?", (meeting_id, MARKET)),
        "reports": s.query("SELECT * FROM reports WHERE meeting_id=? AND symbol=? ORDER BY id", (meeting_id, symbol)),
        "debate": s.query("SELECT * FROM debate WHERE meeting_id=? AND symbol=? ORDER BY id", (meeting_id, symbol)),
    }


@app.get("/api/trades/{trade_id}")
def trade_detail(trade_id: int):
    s = ctx.fund.store
    t = s.one("SELECT * FROM trades WHERE id=?", (trade_id,))
    if not t:
        raise HTTPException(404, "거래를 찾을 수 없습니다.")
    t["name"] = _name(t["symbol"])
    d = s.one("SELECT * FROM decisions WHERE id=?", (t["decision_id"],)) if t["decision_id"] else None
    return {
        "trade": t, "decision": d,
        "trail": _trail(d["meeting_id"], t["symbol"]) if d else None,
        "orders": s.query("SELECT * FROM orders WHERE symbol=? AND desk=? AND ts BETWEEN ? AND ? ORDER BY id",
                          (t["symbol"], t["desk"], t["entry_ts"], t["exit_ts"])),
        "lesson": s.one("SELECT * FROM lessons WHERE trade_id=?", (trade_id,)),
    }


@app.get("/api/meetings")
def meetings(limit: int = 60):
    return ctx.fund.store.query(
        "SELECT m.*, (SELECT COUNT(*) FROM decisions d WHERE d.meeting_id=m.id AND d.action='buy') AS buys, "
        "(SELECT COUNT(*) FROM decisions d WHERE d.meeting_id=m.id AND d.action='sell') AS sells "
        "FROM meetings m ORDER BY m.id DESC LIMIT ?", (min(limit, 500),))


@app.get("/api/meetings/{meeting_id}")
def meeting_detail(meeting_id: int):
    s = ctx.fund.store
    m = s.one("SELECT * FROM meetings WHERE id=?", (meeting_id,))
    if not m:
        raise HTTPException(404, "회의를 찾을 수 없습니다.")
    reports = s.query("SELECT * FROM reports WHERE meeting_id=? ORDER BY id", (meeting_id,))
    debate = s.query("SELECT * FROM debate WHERE meeting_id=? ORDER BY id", (meeting_id,))
    decisions = {d["symbol"]: d for d in s.query("SELECT * FROM decisions WHERE meeting_id=? ORDER BY id", (meeting_id,))}
    by_symbol: dict[str, dict] = {}
    for row in m["symbols"] or []:
        by_symbol[row["symbol"]] = {**row, "reports": [], "debate": [], "decision": decisions.get(row["symbol"])}
    for r in reports:
        if r["symbol"] in (MARKET, SCALP):
            continue
        by_symbol.setdefault(r["symbol"], {"symbol": r["symbol"], "name": _name(r["symbol"]), "role": "candidate",
                                           "consensus": None, "outcome": "리서치 중", "reports": [], "debate": [],
                                           "decision": decisions.get(r["symbol"])})["reports"].append(r)
    for t in debate:
        if t["symbol"] in by_symbol:
            by_symbol[t["symbol"]]["debate"].append(t)
    order = {"보유 재검토": 0, "토론 진행": 1, "리서치 중": 2, "선별 탈락": 3}
    return {
        "meeting": m,
        "macro": next((r for r in reports if r["symbol"] == MARKET), None),
        "scalper": next((r for r in reports if r["symbol"] == SCALP), None),
        "symbols": sorted(by_symbol.values(), key=lambda x: (order.get(x["outcome"], 9), -(x["consensus"] or 0))),
    }


@app.get("/api/agents")
def agents():
    return agent_stats(ctx.fund.store)


@app.get("/api/agents/{agent_id}")
def agent_detail(agent_id: str):
    if agent_id not in AGENTS:
        raise HTTPException(404, "직원을 찾을 수 없습니다.")
    s, a = ctx.fund.store, AGENTS[agent_id]
    recent = _with_names(s.query(
        "SELECT id, meeting_id, ts, symbol, signal, confidence, summary, fwd_return, hit FROM reports "
        "WHERE agent=? AND symbol NOT LIKE '\\_%' ESCAPE '\\' ORDER BY id DESC LIMIT 40", (agent_id,)))
    return {"id": a.id, "title": a.title, "division": a.division, "tier": a.tier, "duty": a.duty,
            "system_prompt": a.system, "recent": recent}


@app.get("/api/risk")
def risk():
    f = ctx.fund
    return {
        "exposure": f.risk.exposure(f.portfolio.snapshot()),
        "events": f.store.query("SELECT * FROM risk_events WHERE kind!='order_blocked' ORDER BY id DESC LIMIT 100"),
        "blocked": _with_names(f.store.query("SELECT * FROM orders WHERE status='rejected' ORDER BY id DESC LIMIT 100")),
    }


@app.get("/api/performance")
def performance():
    f = ctx.fund
    init = f.portfolio.initial_capital
    rows = daily_equity(f.store)
    peak, dd = init, []
    for r in rows:
        peak = max(peak, r["equity"])
        dd.append({"day": r["day"], "drawdown": round(r["equity"] / peak - 1, 5)})
    return {
        "metrics": metrics(f.store, init),
        "monthly": monthly_returns(f.store, init),
        "drawdown": dd,
        "lessons": f.store.query("SELECT * FROM lessons ORDER BY id DESC LIMIT 50"),
        "llm_daily": f.store.query("SELECT substr(ts,1,10) AS day, COUNT(*) AS calls, SUM(cost_usd) AS cost, "
                                   "SUM(cached) AS cached FROM llm_usage GROUP BY 1 ORDER BY 1"),
        "llm_by_model": f.store.query("SELECT model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
                                      "SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost FROM llm_usage GROUP BY 1"),
    }


@app.get("/api/symbols/{symbol}")
def symbol_detail(symbol: str, days: int = 180):
    f, symbol = ctx.fund, symbol.upper()
    bars = f.data.daily_bars(symbol, min(days, 750))
    if not bars:
        raise HTTPException(404, f"{symbol} 시세가 없습니다.")
    p = f.data.profile(symbol)
    s = f.store
    return {
        "symbol": symbol, "name": p.name, "sector": p.sector, "price": f.data.quote(symbol),
        "bars": [{"time": b.ts.date().isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                  "volume": b.volume} for b in bars],
        "positions": [x for x in f.portfolio.positions() if x["symbol"] == symbol],
        "orders": s.query("SELECT * FROM orders WHERE symbol=? AND status IN ('filled','partial') ORDER BY id", (symbol,)),
        "trades": s.query("SELECT * FROM trades WHERE symbol=? ORDER BY id DESC", (symbol,)),
        "decisions": s.query("SELECT id, meeting_id, ts, action, conviction, consensus, rationale, dissent, status, "
                             "status_detail, qty, stop_price, target_price FROM decisions WHERE symbol=? ORDER BY id DESC LIMIT 40", (symbol,)),
        "reports": s.query("SELECT id, meeting_id, ts, agent, signal, confidence, summary, fwd_return, hit FROM reports "
                           "WHERE symbol=? ORDER BY id DESC LIMIT 140", (symbol,)),
    }


@app.get("/api/audit")
def audit(limit: int = 200):
    return ctx.fund.store.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(limit, 2000),))


# ── 제어 ──────────────────────────────────────────────────
class Toggle(BaseModel):
    on: bool


class SimulateBody(BaseModel):
    days: int = 60


class BacktestBody(BaseModel):
    start: date
    end: date
    max_cost_usd: float | None = None


class ResetBody(BaseModel):
    confirm: str


@app.post("/api/control/kill")
def kill(body: Toggle):
    ctx.fund.risk.set_kill_switch(body.on)
    if body.on:
        ctx.fund.scalper.flatten("kill_switch") if is_market_open(ctx.fund.clock.now()) else None
    return {"kill_switch": ctx.fund.risk.kill_switch}


@app.post("/api/control/resume")
def resume():
    ctx.fund.risk.resume()
    return {"halted": ctx.fund.risk.halted}


@app.post("/api/control/scheduler")
def scheduler_toggle(body: Toggle):
    ctx.scheduler.set_paused(not body.on)
    return {"paused": ctx.scheduler.paused}


@app.post("/api/control/meeting")
def start_meeting():
    return run_job("meeting", lambda: ctx.fund.run_meeting("manual"), "수동 회의")


@app.post("/api/control/simulate")
def start_simulation(body: SimulateBody):
    f = ctx.fund
    end = last_completed_session(f.clock.now())
    latest = f.store.scalar("SELECT MAX(day) FROM equity")
    start = add_trading_days(date.fromisoformat(latest), 1) if latest else add_trading_days(end, -(max(1, min(body.days, 500)) - 1))
    if start > end:
        raise HTTPException(409, "이미 최근 거래일까지 기록이 있습니다.")
    if f.store.scalar("SELECT COUNT(*) FROM positions WHERE desk='scalp'", default=0):
        raise HTTPException(409, "스캘핑 포지션이 열려 있는 동안에는 시뮬레이션을 돌릴 수 없습니다.")

    def work():
        sim = Fund(f.cfg, f.store, f.data.provider, f.llm.client, SimClock(at(start, time(9, 0))), historical=True)
        bt.simulate(sim, start, end)

    return run_job("simulate", work, f"{start} ~ {end}")


@app.post("/api/control/reset")
def reset(body: ResetBody):
    if body.confirm != "RESET":
        raise HTTPException(400, "confirm 에 RESET 을 보내야 합니다.")
    if not ctx.scheduler.busy.acquire(blocking=False):
        raise HTTPException(409, "작업이 진행 중이라 초기화할 수 없습니다.")
    try:
        s = ctx.fund.store
        for t in s.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            s.execute(f"DROP TABLE {t['name']}")
        s._conn.executescript(SCHEMA)
        ctx.fund = build_fund(ctx.fund.cfg, store=s)
        ctx.scheduler.f = ctx.fund
    finally:
        ctx.scheduler.busy.release()
    return {"reset": True}


@app.get("/api/backtests")
def backtests():
    return bt.list_backtests()


@app.post("/api/backtests")
def start_backtest(body: BacktestBody):
    if body.end < body.start:
        raise HTTPException(400, "종료일이 시작일보다 빠릅니다.")
    cfg = ctx.fund.cfg
    return run_job("backtest", lambda: bt.run_backtest(cfg, body.start, body.end, llm_budget_usd=body.max_cost_usd),
                   f"{body.start} ~ {body.end}")


# ── 실시간 이벤트 (SSE) ───────────────────────────────────
@app.get("/api/events")
async def events(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    ctx.hub.clients.add(q)

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            ctx.hub.clients.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 대시보드 정적 파일 ────────────────────────────────────
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = STATIC / path
        return FileResponse(file if path and file.is_file() and STATIC in file.resolve().parents else STATIC / "index.html")
