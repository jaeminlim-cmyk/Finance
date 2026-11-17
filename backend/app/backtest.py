"""백테스트·데모 시뮬레이션. 운용과 똑같은 에이전트 파이프라인을 과거 날짜에 대해 하루씩 재생한다.

데이터 계층이 시점을 강제하므로(MarketData) 에이전트는 해당 날짜 이후의 데이터를 볼 수 없다.
다만 실제 LLM 은 학습 과정에서 과거 사건을 이미 알고 있을 수 있어, 학습 시점 이전 구간의 백테스트 성과는
낙관적으로 나올 수 있다. 이 한계는 코드로 막을 수 없다.
"""

from __future__ import annotations

import json
import time as _time
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Callable

from .clock import SimClock, at, trading_days
from .config import ROOT, Config
from .fund import Fund, build_fund
from .performance import metrics
from .store import Store

BACKTEST_DIR = ROOT / "data" / "backtests"


def simulate(fund: Fund, start: date, end: date, progress: Callable[[date, int, int], None] | None = None) -> dict:
    days = trading_days(start, end)
    for i, d in enumerate(days, 1):
        fund.simulate_day(d)
        if progress:
            progress(d, i, len(days))
    return metrics(fund.store, fund.portfolio.initial_capital)


def run_backtest(cfg: Config, start: date, end: date, *, name: str | None = None, llm_budget_usd: float | None = None,
                 progress: Callable[[date, int, int], None] | None = None) -> dict:
    """별도 DB 파일에 백테스트를 돌리고 요약을 돌려준다. 운용 DB 는 건드리지 않는다."""
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    run_id = name or datetime.now(timezone.utc).strftime("bt_%Y%m%d_%H%M%S")
    db_path = BACKTEST_DIR / f"{run_id}.db"
    if db_path.exists():
        db_path.unlink()
    store = Store(db_path)
    fund = build_fund(cfg, store=store, clock=SimClock(at(start, time(9, 0))), historical=True,
                      llm_budget_usd=llm_budget_usd)
    t0 = _time.time()
    result = simulate(fund, start, end, progress)
    summary = {"id": run_id, "start": start.isoformat(), "end": end.isoformat(), "data_mode": cfg.data_mode,
               "llm_mode": cfg.llm_mode, "seconds": round(_time.time() - t0, 1), "metrics": result}
    (BACKTEST_DIR / f"{run_id}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    store.close()
    return summary


def list_backtests() -> list[dict]:
    if not BACKTEST_DIR.exists():
        return []
    out = []
    for p in sorted(BACKTEST_DIR.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def backtest_db(run_id: str) -> Path | None:
    p = BACKTEST_DIR / f"{Path(run_id).name}.db"
    return p if p.exists() else None
