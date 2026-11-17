"""포트폴리오 조회와 평가."""

from __future__ import annotations

from .clock import iso
from .config import Config
from .data.base import MarketData
from .store import Store


class Portfolio:
    def __init__(self, store: Store, data: MarketData, cfg: Config):
        self.store, self.data, self.cfg = store, data, cfg
        if store.get_state("cash") is None:
            store.set_state("cash", cfg.fund.initial_capital)
            store.set_state("initial_capital", cfg.fund.initial_capital)

    @property
    def cash(self) -> float:
        return float(self.store.get_state("cash", 0.0))

    @property
    def initial_capital(self) -> float:
        return float(self.store.get_state("initial_capital", self.cfg.fund.initial_capital))

    def positions(self) -> list[dict]:
        rows = self.store.query("SELECT * FROM positions ORDER BY desk, symbol")
        for p in rows:
            price = self.data.quote(p["symbol"]) or p["avg_price"]
            p["price"] = price
            p["market_value"] = round(price * p["qty"], 2)
            p["unrealized_pnl"] = round((price - p["avg_price"]) * p["qty"], 2)
            p["unrealized_pct"] = round(price / p["avg_price"] - 1, 4) if p["avg_price"] else 0.0
        return rows

    def position(self, symbol: str, desk: str = "swing") -> dict | None:
        return self.store.one("SELECT * FROM positions WHERE symbol=? AND desk=?", (symbol, desk))

    def snapshot(self) -> dict:
        """현재 시각 기준 계좌 요약."""
        positions = self.positions()
        value = sum(p["market_value"] for p in positions)
        equity = self.cash + value
        sectors: dict[str, float] = {}
        for p in positions:
            sectors[p["sector"] or "기타"] = sectors.get(p["sector"] or "기타", 0.0) + p["market_value"]
        for p in positions:
            p["weight"] = round(p["market_value"] / equity, 4) if equity else 0.0
        return {
            "cash": round(self.cash, 2),
            "positions_value": round(value, 2),
            "equity": round(equity, 2),
            "cash_pct": round(self.cash / equity, 4) if equity else 1.0,
            "positions": positions,
            "sector_value": sectors,
            "scalp_value": sum(p["market_value"] for p in positions if p["desk"] == "scalp"),
        }

    def record_equity(self) -> dict:
        snap = self.snapshot()
        now = self.data.as_of
        self.store.execute(
            "INSERT OR REPLACE INTO equity(ts, day, equity, cash, positions_value, benchmark) VALUES(?,?,?,?,?,?)",
            (iso(now), now.date().isoformat(), snap["equity"], snap["cash"], snap["positions_value"],
             self.data.quote(self.cfg.fund.benchmark)),
        )
        self.store.emit("equity", {"equity": snap["equity"]})
        return snap
