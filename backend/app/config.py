"""운용 설정. 한도·모델·일정은 config/fund.toml, 모드와 비밀 키는 환경 변수(.env)에서 읽는다."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


class FundCfg(BaseModel):
    initial_capital: float = 100_000.0
    benchmark: str = "SPY"


class UniverseCfg(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    max_research: int = 10
    max_debate: int = 4


class RiskCfg(BaseModel):
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    min_cash_pct: float = 0.10
    max_risk_per_trade_pct: float = 0.01
    daily_loss_limit_pct: float = 0.03
    max_drawdown_pct: float = 0.15
    earnings_blackout_days: int = 2
    min_price: float = 5.0
    min_avg_dollar_volume: float = 20_000_000
    stop_atr_multiple: float = 2.0
    target_r_multiple: float = 2.0


class ScalperCfg(BaseModel):
    enabled: bool = True
    capital_pct: float = 0.10
    max_candidates: int = 3
    risk_per_trade_pct: float = 0.0025
    bar_minutes: int = 5


class BrokerCfg(BaseModel):
    slippage_bps: float = 5.0
    commission_per_share: float = 0.005
    min_commission: float = 1.0
    max_participation: float = 0.05


class LLMCfg(BaseModel):
    monthly_budget_usd: float = 100.0
    research_model: str = "claude-haiku-5-5"
    senior_model: str = "claude-opus-5-5"
    debate_rounds: int = 2


class ScheduleCfg(BaseModel):
    premarket: str = "09:00"
    intraday_review_minutes: int = 60


class Config(BaseModel):
    fund: FundCfg = Field(default_factory=FundCfg)
    universe: UniverseCfg = Field(default_factory=UniverseCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    scalper: ScalperCfg = Field(default_factory=ScalperCfg)
    broker: BrokerCfg = Field(default_factory=BrokerCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    schedule: ScheduleCfg = Field(default_factory=ScheduleCfg)

    # 환경 변수에서 오는 값
    trading_mode: str = "paper"
    data_mode: str = "mock"
    llm_mode: str = "mock"
    db_path: str = "data/fund.db"


class LiveTradingDisabled(RuntimeError):
    """1단계에서는 실거래 경로가 존재하지 않는다."""


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_config(path: str | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = _resolve(path or os.getenv("FUND_CONFIG", "config/fund.toml"))
    raw: dict = {}
    if cfg_path.exists():
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    cfg = Config(**raw)
    cfg.trading_mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    cfg.data_mode = os.getenv("DATA_MODE", "mock").strip().lower()
    cfg.llm_mode = os.getenv("LLM_MODE", "mock").strip().lower()
    cfg.db_path = str(_resolve(os.getenv("DB_PATH", "data/fund.db")))
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.trading_mode != "paper":
        raise LiveTradingDisabled(
            f"TRADING_MODE={cfg.trading_mode!r} 는 지원하지 않습니다. 1단계는 paper(모의투자) 전용입니다."
        )
    if cfg.data_mode not in ("mock", "live"):
        raise ValueError("DATA_MODE 는 mock 또는 live 여야 합니다.")
    if cfg.llm_mode not in ("mock", "anthropic"):
        raise ValueError("LLM_MODE 는 mock 또는 anthropic 이어야 합니다.")
