"""자체 페이퍼 브로커. 슬리피지·수수료·부분 체결을 모델링하고 계좌 장부를 SQLite 에 기록한다.

외부로 나가는 주문은 없다. 모든 체결은 이 프로세스 안의 장부 갱신일 뿐이다.
"""

from __future__ import annotations

from math import floor

from ..clock import Clock, iso
from ..config import Config
from ..store import Store
from .base import Broker


class PaperBroker(Broker):
    is_paper = True

    def __init__(self, store: Store, cfg: Config, clock: Clock):
        self.store, self.cfg, self.clock = store, cfg, clock

    # ── 내부 도우미 ────────────────────────────────────────
    def _commission(self, qty: int) -> float:
        b = self.cfg.broker
        return round(max(b.min_commission, qty * b.commission_per_share), 2) if qty > 0 else 0.0

    def _fillable(self, qty: int, bar_volume: float | None) -> int:
        if bar_volume is None:
            return qty
        return min(qty, floor(bar_volume * self.cfg.broker.max_participation))

    def _order(self, **cols) -> dict:
        cols.setdefault("ts", iso(self.clock.now()))
        oid = self.store.insert("orders", **cols)
        self.store.audit(cols["ts"], "broker", f"order_{cols['status']}", {"order_id": oid, **cols})
        self.store.emit("order", {"id": oid, "symbol": cols["symbol"], "status": cols["status"]})
        return {"id": oid, **cols}

    def reject(self, symbol, side, qty, ref_price, *, desk, reason, decision_id=None) -> dict:
        return self._order(symbol=symbol, side=side, desk=desk, qty=qty, filled_qty=0, ref_price=ref_price,
                           status="rejected", reason=reason, decision_id=decision_id)

    # ── 매수 ───────────────────────────────────────────────
    def buy(self, symbol, qty, ref_price, *, desk, stop_price, target_price, sector, decision_id=None,
            bar_volume=None, note="") -> dict:
        if qty <= 0 or ref_price <= 0:
            return self.reject(symbol, "buy", qty, ref_price, desk=desk, reason="수량 또는 가격이 0 이하", decision_id=decision_id)
        fill_qty = self._fillable(qty, bar_volume)
        if fill_qty <= 0:
            return self.reject(symbol, "buy", qty, ref_price, desk=desk, reason="거래량 부족으로 체결 불가", decision_id=decision_id)
        fill = round(ref_price * (1 + self.cfg.broker.slippage_bps / 1e4), 4)
        commission = self._commission(fill_qty)
        cash = float(self.store.get_state("cash", 0.0))
        cost = fill * fill_qty + commission
        if cost > cash:
            return self.reject(symbol, "buy", qty, ref_price, desk=desk, reason="현금 부족", decision_id=decision_id)
        self.store.set_state("cash", cash - cost)
        now = iso(self.clock.now())
        pos = self.store.one("SELECT * FROM positions WHERE symbol=? AND desk=?", (symbol, desk))
        if pos:
            new_qty = pos["qty"] + fill_qty
            avg = (pos["avg_price"] * pos["qty"] + fill * fill_qty) / new_qty
            self.store.update("positions", pos["id"], qty=new_qty, avg_price=avg,
                              stop_price=stop_price or pos["stop_price"],
                              target_price=target_price or pos["target_price"],
                              entry_cost=(pos["entry_cost"] or 0) + commission)
        else:
            self.store.insert("positions", symbol=symbol, desk=desk, qty=fill_qty, avg_price=fill,
                              stop_price=stop_price, target_price=target_price, sector=sector,
                              opened_at=now, decision_id=decision_id, entry_cost=commission)
        return self._order(ts=now, symbol=symbol, side="buy", desk=desk, qty=qty, filled_qty=fill_qty,
                           ref_price=ref_price, fill_price=fill, commission=commission,
                           slippage=round((fill - ref_price) * fill_qty, 2),
                           status="filled" if fill_qty == qty else "partial",
                           reason=None if fill_qty == qty else "거래량 참여율 한도로 부분 체결",
                           note=note, decision_id=decision_id)

    # ── 매도 ───────────────────────────────────────────────
    def sell(self, symbol, qty, ref_price, *, desk, reason, bar_volume=None, note="") -> dict:
        pos = self.store.one("SELECT * FROM positions WHERE symbol=? AND desk=?", (symbol, desk))
        if not pos or qty <= 0:
            return self.reject(symbol, "sell", qty, ref_price, desk=desk, reason="보유 수량 없음 (공매도 불가)")
        qty = min(qty, pos["qty"])
        fill_qty = self._fillable(qty, bar_volume)
        if fill_qty <= 0:
            return self.reject(symbol, "sell", qty, ref_price, desk=desk, reason="거래량 부족으로 체결 불가",
                               decision_id=pos["decision_id"])
        fill = round(ref_price * (1 - self.cfg.broker.slippage_bps / 1e4), 4)
        commission = self._commission(fill_qty)
        entry_cost = (pos["entry_cost"] or 0) * fill_qty / pos["qty"]
        pnl = (fill - pos["avg_price"]) * fill_qty - commission - entry_cost
        now = iso(self.clock.now())
        self.store.set_state("cash", float(self.store.get_state("cash", 0.0)) + fill * fill_qty - commission)
        if fill_qty == pos["qty"]:
            self.store.execute("DELETE FROM positions WHERE id=?", (pos["id"],))
        else:
            self.store.update("positions", pos["id"], qty=pos["qty"] - fill_qty,
                              entry_cost=(pos["entry_cost"] or 0) - entry_cost)
        trade_id = self.store.insert(
            "trades", symbol=symbol, desk=desk, qty=fill_qty, entry_price=pos["avg_price"], exit_price=fill,
            entry_ts=pos["opened_at"], exit_ts=now, pnl=round(pnl, 2),
            pnl_pct=round(pnl / (pos["avg_price"] * fill_qty), 5), costs=round(commission + entry_cost, 2),
            exit_reason=reason, sector=pos["sector"], decision_id=pos["decision_id"])
        order = self._order(ts=now, symbol=symbol, side="sell", desk=desk, qty=qty, filled_qty=fill_qty,
                            ref_price=ref_price, fill_price=fill, commission=commission,
                            slippage=round((ref_price - fill) * fill_qty, 2),
                            status="filled" if fill_qty == qty else "partial", reason=reason, note=note,
                            decision_id=pos["decision_id"])
        order["trade_id"], order["pnl"] = trade_id, round(pnl, 2)
        return order
