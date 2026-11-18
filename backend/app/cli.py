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


def _check(cfg) -> bool:
    """실데이터와 Claude 연결을 한 번씩 실제로 호출해 본다. 같은 설정으로 이미 통과했으면 다시 호출하지 않는다."""
    from .agents.roster import AGENTS
    from .data.base import MarketData
    from .fund import make_llm, make_provider
    from .llm.base import LLMCall, LLMError, LLMGateway
    from .store import Store

    print(f"모드: 데이터={cfg.data_mode}  LLM={cfg.llm_mode}  기록={cfg.db_path}")
    if cfg.data_mode == "mock" and cfg.llm_mode == "mock":
        return True
    store = Store(cfg.db_path)
    signature = {"data": cfg.data_mode, "llm": cfg.llm_mode, "research": cfg.llm.research_model, "senior": cfg.llm.senior_model}
    if store.get_state("connection_check") == signature:
        print("연결 확인: 이전에 같은 설정으로 통과했습니다.")
        return True
    ok, macro = True, None
    if cfg.data_mode == "live":
        try:
            data = MarketData(make_provider(cfg), RealClock())
            price, news, macro = data.quote("SPY"), data.news("AAPL", 3), data.macro()
            if not price:
                raise RuntimeError("SPY 현재가를 받지 못했습니다")
            print(f"[확인] 시세: SPY {price:.2f}")
            print(f"[확인] 뉴스: AAPL 최근 {len(news)}건" + (f" — {news[0].headline[:60]}" if news else " (0건이면 뉴스 분석가는 '데이터 없음'으로 보고합니다)"))
        except Exception as e:
            ok = False
            print(f"[실패] 실데이터: {type(e).__name__}: {e}")
    if cfg.llm_mode == "anthropic":
        try:
            gateway = LLMGateway(store, make_llm(cfg), cfg)
            for tier, model in (("research", cfg.llm.research_model), ("senior", cfg.llm.senior_model)):
                before = gateway.spent()
                a = AGENTS["macro"]
                rep = gateway.run(LLMCall(agent="connection_check", tier=tier, system=a.system, schema=a.schema,
                                          task="연결 확인용 호출입니다. 주어진 데이터로 시장 국면을 간단히 판정하세요.",
                                          context={"check": tier, "macro": macro}))
                print(f"[확인] Claude {model}: 응답 받음 (국면 {rep.regime}, 비용 약 ${gateway.spent() - before:.4f})")
        except LLMError as e:
            ok = False
            print(f"[실패] Claude: {e}")
        except Exception as e:
            ok = False
            print(f"[실패] Claude: {type(e).__name__}: {e}")
    if ok:
        store.set_state("connection_check", signature)
    else:
        print("연결에 실패한 항목이 있습니다. .env 의 설정과 키를 확인하세요. 대시보드는 열리지만 회의가 실패할 수 있습니다.")
    return ok


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
    sub.add_parser("demo", help="샘플 모드이고 기록이 없을 때만 60거래일 재생 (start.bat 용)")
    sub.add_parser("check", help="실데이터·Claude 연결 확인")
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
    if args.cmd == "check":
        sys.exit(0 if _check(cfg) else 1)
    if args.cmd == "demo":
        # 실데이터나 Claude 모드에서 60거래일을 자동 재생하면 시간이 오래 걸리고 실제 비용이 나가므로 샘플 모드에서만 한다.
        if cfg.data_mode != "mock" or cfg.llm_mode != "mock":
            return
        if build_fund(cfg).store.scalar("SELECT COUNT(*) FROM equity", default=0):
            return
        print("샘플 데이터로 최근 60거래일을 재생합니다…", file=sys.stderr)
        args.cmd, args.days = "simulate", 60
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
