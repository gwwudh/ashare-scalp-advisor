from __future__ import annotations

from app.models import Bar


def _ema_last(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi_last(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _macd_last(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    if len(closes) < 35:
        return None, None, None
    # simplified: compute full series last points
    def ema_series(vals: list[float], period: int) -> list[float | None]:
        out: list[float | None] = [None] * len(vals)
        if len(vals) < period:
            return out
        k = 2 / (period + 1)
        ema = sum(vals[:period]) / period
        out[period - 1] = ema
        for i in range(period, len(vals)):
            ema = vals[i] * k + ema * (1 - k)
            out[i] = ema
        return out

    ef = ema_series(closes, 12)
    es = ema_series(closes, 26)
    dif: list[float | None] = []
    for i in range(len(closes)):
        if ef[i] is None or es[i] is None:
            dif.append(None)
        else:
            dif.append(ef[i] - es[i])  # type: ignore[operator]
    # signal on dif
    valid = [d for d in dif if d is not None]
    if len(valid) < 9:
        return dif[-1], None, None
    # rebuild dea aligned
    dea: list[float | None] = [None] * len(dif)
    k = 2 / 10
    # find first 9 valid
    buf: list[float] = []
    prev = None
    for i, d in enumerate(dif):
        if d is None:
            continue
        buf.append(d)
        if len(buf) < 9:
            continue
        if prev is None:
            prev = sum(buf[-9:]) / 9
        else:
            prev = d * (2 / 10) + prev * (1 - 2 / 10)
        dea[i] = prev
    d_last = dif[-1]
    a_last = dea[-1]
    h_last = None if d_last is None or a_last is None else d_last - a_last
    return d_last, a_last, h_last


def _kdj_last(bars: list[Bar], n: int = 9) -> tuple[float | None, float | None, float | None]:
    if len(bars) < n:
        return None, None, None
    k, d = 50.0, 50.0
    for i in range(n - 1, len(bars)):
        window = bars[i - n + 1 : i + 1]
        hh = max(b.high for b in window)
        ll = min(b.low for b in window)
        rsv = 50.0 if hh == ll else (bars[i].close - ll) / (hh - ll) * 100
        k = (rsv + 2 * k) / 3
        d = (k + 2 * d) / 3
    j = 3 * k - 2 * d
    return k, d, j


def indicator_snapshot(bars: list[Bar]) -> str:
    if not bars:
        return "指标:暂无K线"
    closes = [b.close for b in bars]
    e5 = _ema_last(closes, 5)
    e10 = _ema_last(closes, 10)
    e20 = _ema_last(closes, 20)
    dif, dea, hist = _macd_last(closes)
    kk, kd, kj = _kdj_last(bars)
    rsi = _rsi_last(closes, 14)
    last = closes[-1]
    vol = bars[-1].volume

    def f(v: float | None, nd: int = 2) -> str:
        return "-" if v is None else f"{v:.{nd}f}"

    return (
        f"最新收盘:{last:.3f} 量:{vol:.0f}\n"
        f"EMA5/10/20:{f(e5)}/{f(e10)}/{f(e20)}\n"
        f"MACD DIF/DEA/柱:{f(dif,3)}/{f(dea,3)}/{f(hist,3)}\n"
        f"KDJ K/D/J:{f(kk)}/{f(kd)}/{f(kj)}\n"
        f"RSI14:{f(rsi)}"
    )
