"""브로커 인터페이스.

1단계에는 `PaperBroker` 구현만 있다. 나중에 실거래 브로커를 붙일 때 이 인터페이스를 구현하면 되지만,
그 전에 config.validate 의 TRADING_MODE 검사를 사람이 직접 풀어야 한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Broker(ABC):
    is_paper: bool = True

    @abstractmethod
    def buy(self, symbol: str, qty: int, ref_price: float, *, desk: str, stop_price: float | None,
            target_price: float | None, sector: str | None, decision_id: int | None = None,
            bar_volume: float | None = None, note: str = "") -> dict: ...

    @abstractmethod
    def sell(self, symbol: str, qty: int, ref_price: float, *, desk: str, reason: str,
             bar_volume: float | None = None, note: str = "") -> dict: ...

    @abstractmethod
    def reject(self, symbol: str, side: str, qty: int, ref_price: float, *, desk: str, reason: str,
               decision_id: int | None = None) -> dict: ...
