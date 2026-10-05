from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.config import WatchStock, load_config, load_watchlist, normalize_code, save_watchlist
from app.engine.signals import SignalEngine
from app.indicator_snapshot import indicator_snapshot
from app.llm import LLMAdvisor
from app.notifier import Notifier
from app.poller import MarketPoller
from app.store import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
# 子路径部署：如 /stock；本地为空。nginx 可 strip 前缀，前端仍需 APP_BASE。
APP_BASE = (os.getenv("APP_BASE") or os.getenv("ROOT_PATH") or "").rstrip("/")

cfg = load_config()
store = StateStore()
engine = SignalEngine(cfg)
notifier = Notifier(cfg.notify.webhook_url)
llm = LLMAdvisor(
    base_url=cfg.llm.base_url,
    api_key=cfg.llm.api_key,
    model=cfg.llm.model,
    timeout=cfg.llm.timeout_sec,
    enabled=cfg.llm.enabled,
)
poller = MarketPoller(cfg, store, engine, notifier, load_watchlist(), llm=llm)


class WatchlistAdd(BaseModel):
    code: str
    name: str = ""
    cost: float | None = None
    qty: float | None = None


class ChatAsk(BaseModel):
    question: str = "现在怎么操作？"
    code: str | None = None
    timeframe: str = "5m"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    poller.start()
    yield
    poller.stop()


app = FastAPI(
    title="Ashare Scalp Advisor",
    version="0.1.0",
    lifespan=lifespan,
    root_path=APP_BASE or "",
)


@app.get("/api/health")
def health():
    return {"ok": True, "stocks": [s.code for s in poller.stocks]}


@app.get("/api/snapshot")
def snapshot():
    return store.snapshot()


@app.get("/api/watchlist")
def watchlist():
    return {"stocks": [s.model_dump() for s in poller.stocks]}


@app.post("/api/watchlist")
def add_watchlist(body: WatchlistAdd):
    try:
        code = normalize_code(body.code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    stocks = list(poller.stocks)
    if any(s.code == code for s in stocks):
        raise HTTPException(409, f"{code} 已在自选")

    name = body.name.strip()
    try:
        q = poller.market.get_quote(code)
        if not name:
            name = q.name
    except Exception:  # noqa: BLE001
        if not name:
            name = code

    stock = WatchStock(code=code, name=name, cost=body.cost, qty=body.qty)
    stocks.append(stock)
    save_watchlist(stocks)
    poller.reload_watchlist(stocks)
    poller.refresh_one(code)
    return {"ok": True, "stock": stock.model_dump()}


@app.delete("/api/watchlist/{code}")
def delete_watchlist(code: str):
    try:
        code = normalize_code(code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    stocks = [s for s in poller.stocks if s.code != code]
    if len(stocks) == len(poller.stocks):
        raise HTTPException(404, f"{code} 不在自选")
    save_watchlist(stocks)
    poller.reload_watchlist(stocks)
    # 清理内存态
    store.quotes.pop(code, None)
    store.klines.pop(code, None)
    store.situations.pop(code, None)
    store.chart_markers.pop(code, None)
    return {"ok": True, "count": len(stocks)}


@app.post("/api/watchlist/reload")
def reload_watchlist():
    stocks = load_watchlist()
    poller.reload_watchlist(stocks)
    return {"ok": True, "count": len(stocks)}


def _normalize_tf(tf: str) -> str:
    t = (tf or "5m").strip().lower()
    if t in ("primary", "5m"):
        return "5m"
    if t in ("trigger", "1m"):
        return "1m"
    if t in ("fs", "minute", "fen"):
        return "fs"
    if t in ("day", "daily", "1d"):
        return "day"
    return "5m"


def _tf_label(tf: str) -> str:
    return {"fs": "分时", "1m": "1 分钟", "5m": "5 分钟", "day": "日线"}.get(tf, tf)


@app.get("/api/kline/{code}")
def kline(code: str, tf: str = "5m"):
    try:
        code = normalize_code(code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    timeframe = _normalize_tf(tf)
    data = store.kline_payload(code, timeframe)
    bars = store.get_bars(code, timeframe)

    # 切换周期时若尚未轮询到，即时拉取
    if not bars:
        try:
            limit = 300 if timeframe == "fs" else (180 if timeframe == "day" else 120)
            bars = poller.market.get_kline(code, timeframe, limit)
            if bars:
                store.set_kline(code, timeframe, bars)
                data = store.kline_payload(code, timeframe)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"拉取K线失败: {exc}") from exc

    if not data:
        raise HTTPException(404, f"no kline yet for {code}")

    markers = store.snapshot()["markers"].get(code, [])
    quote = store.quotes.get(code)
    stock = next((s for s in poller.stocks if s.code == code), None)

    board = None
    if quote and stock and bars:
        board = engine.analyze_for_timeframe(stock, quote, bars, timeframe)

    levels = []
    if board:
        for i, p in enumerate(board.resistances[:3], start=1):
            levels.append({"type": f"resistance{i}", "price": p, "label": f"压力{i}"})
        for i, p in enumerate(board.supports[:3], start=1):
            levels.append({"type": f"support{i}", "price": p, "label": f"支撑{i}"})
        if board.stop_loss:
            levels.append({"type": "stop", "price": board.stop_loss, "label": "止损"})
        if board.take_profit:
            levels.append({"type": "take", "price": board.take_profit, "label": "止盈"})

    return {
        "code": code,
        "timeframe": timeframe,
        "timeframe_label": _tf_label(timeframe),
        "bars": data,
        "markers": markers,
        "levels": levels,
        "quote": asdict(quote) if quote else None,
        "situation": asdict(board) if board else None,
    }


@app.post("/api/chat")
def chat_ask(body: ChatAsk):
    if not llm.enabled:
        raise HTTPException(503, "LLM 未配置或未启用")

    code = body.code
    if code:
        try:
            code = normalize_code(code)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        code = poller.stocks[0].code if poller.stocks else None
    if not code:
        raise HTTPException(400, "无可用标的")

    which = _normalize_tf(body.timeframe)
    bars = store.get_bars(code, which)
    if not bars:
        bars = store.get_bars(code, "5m")
    quote = store.quotes.get(code)
    sit = store.situations.get(code)
    snap = store.snapshot()
    events = [e for e in snap.get("events", []) if e.get("code") == code][-8:]
    event_lines = "；".join(e.get("title") or e.get("detail") or "" for e in events) or "暂无通知"

    ctx_parts = [
        f"标的:{code}",
        f"周期:{_tf_label(which)}",
    ]
    if quote:
        ctx_parts.append(
            f"现价:{quote.price} 涨跌:{quote.change_pct}% "
            f"今开:{quote.open} 最高:{quote.high} 最低:{quote.low}"
        )
    if sit:
        ctx_parts.append(
            f"趋势:{sit.trend} 规则建议:{sit.action} 理由:{sit.reason} "
            f"支撑:{sit.support} 压力:{sit.resistance} "
            f"站稳支撑:{sit.holding_support} 突破确认:{sit.breakout_confirmed} "
            f"突破失败:{sit.breakout_failed} 支撑跌破:{sit.support_broken} "
            f"止损:{sit.stop_loss} 止盈:{sit.take_profit}"
        )
    ctx_parts.append(indicator_snapshot(bars))
    ctx_parts.append(f"通知流:{event_lines}")
    context = "\n".join(ctx_parts)

    answer = llm.answer_question(body.question, context)
    if not answer:
        raise HTTPException(502, "大模型未返回有效回答，请稍后重试")
    return {"ok": True, "code": code, "answer": answer, "question": body.question}


@app.get("/")
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("__APP_BASE__", APP_BASE)
    return HTMLResponse(html)


@app.get("/static/{file_path:path}")
def static_files(file_path: str):
    """显式静态路由，避免部分环境下 StaticFiles mount 404。"""
    target = (STATIC / file_path).resolve()
    if not str(target).startswith(str(STATIC.resolve())):
        raise HTTPException(404, "not found")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
