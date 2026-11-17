"""명령줄 도구.

    python -m app.cli simulate --days 60      # 운용 DB 에 최근 60거래일을 빠르게 재생 (데모 데이터 채우기)
    python -m app.cli backtest 2024-01-02 2024-03-28
    python -m app.cli meeting                 # 지금 회의 1회 실행
    python -m app.cli status
    python -m app.cli reset                   # 운용 DB 삭제
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, time
from pathlib import Path

from .backtest import run_backtest, simulate
from .clock import RealClock, SimClock, add_trading_days, at, last_completed_session
from .config import load_config
from .fund import build_fund
from .performance import metrics


def _progress(d: date, i: int, n: int) -> None:
    print(f"\r  {d} ({i}/{n})", end="", file=sys.stderr, flush=True)
    if i == n:
        print(file=sys.stderr)


def _warn_cost(cfg, what: str) -> None:
    if cfg.llm_mode == "anthropic":
        print(f"※ LLM_MODE=anthropic 입니다. {what} 동안 실제 API 비용이 발생합니다 "
              f"(예산 상한 ${cfg.llm.monthly_budget_usd:.0f}).", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="app.cli", description="AI 헤지펀드 모의 운용 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate", help="운용 DB 에 과거 N 거래일을 재생")
    s.add_argument("--days", type=int, default=60)
    b = sub.add_parser("backtest", help="별도 DB 로 백테스트")
    b.add_argument("start")
    b.add_argument("end")
    b.add_argument("--name")
    b.add_argument("--max-cost", type=float, default=None, help="이 백테스트에 쓸 LLM 비용 상한(USD)")
    sub.add_parser("meeting", help="지금 회의 1회 실행")
    sub.add_parser("status", help="현재 성과 요약")
    sub.add_parser("reset", help="운용 DB 삭제")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.cmd == "reset":
        for suffix in ("", "-wal", "-shm"):
            Path(cfg.db_path + suffix).unlink(missing_ok=True)
        print(f"삭제: {cfg.db_path}")
        return
    if args.cmd == "backtest":
        _warn_cost(cfg, "백테스트")
        out = run_backtest(cfg, date.fromisoformat(args.start), date.fromisoformat(args.end), name=args.name,
                           llm_budget_usd=args.max_cost, progress=_progress)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if args.cmd == "simulate":
        _warn_cost(cfg, "시뮬레이션")
        end = last_completed_session(RealClock().now())
        fund = build_fund(cfg, clock=SimClock(at(end, time(9, 0))), historical=True)
        last = fund.store.get_state("last_sim_day")
        start = add_trading_days(date.fromisoformat(last), 1) if last else add_trading_days(end, -(args.days - 1))
        if start > end:
            print("이미 최근 거래일까지 재생되어 있습니다.")
            return
        out = simulate(fund, start, end, _progress)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    fund = build_fund(cfg)
    if args.cmd == "meeting":
        _warn_cost(cfg, "회의")
        mid = fund.run_meeting("manual")
        print(json.dumps(fund.store.one("SELECT id, status, summary, cost_usd, error FROM meetings WHERE id=?", (mid,)),
                         ensure_ascii=False, indent=1))
    elif args.cmd == "status":
        print(json.dumps(metrics(fund.store, fund.portfolio.initial_capital), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
