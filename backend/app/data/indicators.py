"""기술적 지표 계산. 모든 숫자는 여기서 코드로 계산해 에이전트에 넘긴다(LLM 암산 금지)."""

from __future__ import annotations

from math import sqrt
from statistics import fmean, pstdev

from ..models import Bar


def sma(values: list[float], n: int) -> float | None:
    return fmean(values[-n:]) if len(values) >= n else None


def ema_series(values: list[float], n: int) -> list[float]:
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def macd(values: list[float]) -> tuple[float, float, float] | None:
    if len(values) < 35:
        return None
    fast, slow = ema_series(values, 12), ema_series(values, 26)
    line = [f - s for f, s in zip(fast, slow)]
    signal = ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def bollinger(values: list[float], n: int = 20, k: float = 2.0) -> tuple[float, float, float] | None:
    if len(values) < n:
        return None
    window = values[-n:]
    mid, sd = fmean(window), pstdev(window)
    return mid + k * sd, mid, mid - k * sd


def atr(bars: list[Bar], n: int = 14) -> float | None:
    if len(bars) <= n:
        return None
    trs = []
    for prev, cur in zip(bars[:-1], bars[1:]):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    value = fmean(trs[:n])
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def pct_change(values: list[float], n: int) -> float | None:
    if len(values) <= n or values[-n - 1] == 0:
        return None
    return values[-1] / values[-n - 1] - 1


def realized_vol(values: list[float], n: int = 20) -> float | None:
    """n일 실현 변동성(연율)."""
    if len(values) <= n:
        return None
    rets = [values[i] / values[i - 1] - 1 for i in range(len(values) - n, len(values))]
    return pstdev(rets) * sqrt(252)


def vwap(bars: list[Bar]) -> float | None:
    vol = sum(b.volume for b in bars)
    if vol <= 0:
        return None
    return sum((b.high + b.low + b.close) / 3 * b.volume for b in bars) / vol


def r(x: float | None, digits: int = 2) -> float | None:
    return None if x is None else round(x, digits)
