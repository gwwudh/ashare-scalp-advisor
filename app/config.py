from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]


class PollConfig(BaseModel):
    quote_interval_sec: float = 5
    kline_interval_sec: float = 30
    situation_interval_sec: float = 60


class BarsConfig(BaseModel):
    primary: str = "5m"
    trigger: str = "1m"
    primary_limit: int = 120
    trigger_limit: int = 240


class LevelsConfig(BaseModel):
    lookback: int = 40
    zone_pct: float = 0.003
    breakout_buffer_pct: float = 0.002
    hold_bars: int = 2


class RiskConfig(BaseModel):
    hard_stop_pct: float = 5.0
    hard_take_pct: float = 20.0
    default_atr_mult_sl: float = 1.2
    default_rr: float = 2.0


class NotifyConfig(BaseModel):
    cooldown_sec: int = 180
    webhook_url: str = ""
    use_llm: bool = True


class LLMConfig(BaseModel):
    # 全部留空：未配置则不启用 AI 文案，纯规则运行。
    # 仓库不内置任何服务商地址或模型名，由部署方自行填写或走环境变量。
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""  # 优先用环境变量 LLM_API_KEY
    model: str = ""
    timeout_sec: float = 25.0


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class AppConfig(BaseModel):
    poll: PollConfig = Field(default_factory=PollConfig)
    bars: BarsConfig = Field(default_factory=BarsConfig)
    levels: LevelsConfig = Field(default_factory=LevelsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


class WatchStock(BaseModel):
    code: str
    name: str = ""
    cost: float | None = None
    qty: float | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def load_config(path: Path | None = None) -> AppConfig:
    # 可选加载 .env
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except Exception:  # noqa: BLE001
            pass
    raw = _load_yaml(path or ROOT / "config.yaml")
    cfg = AppConfig.model_validate(raw)
    # 环境变量覆盖密钥与地址
    import os

    if os.getenv("LLM_BASE_URL"):
        cfg.llm.base_url = os.getenv("LLM_BASE_URL", cfg.llm.base_url)
    if os.getenv("LLM_API_KEY"):
        cfg.llm.api_key = os.getenv("LLM_API_KEY", "")
    if os.getenv("LLM_MODEL"):
        cfg.llm.model = os.getenv("LLM_MODEL", cfg.llm.model)
    return cfg


def load_watchlist(path: Path | None = None) -> list[WatchStock]:
    raw = _load_yaml(path or ROOT / "watchlist.yaml")
    stocks = raw.get("stocks") or []
    return [WatchStock.model_validate(s) for s in stocks]


def save_watchlist(stocks: list[WatchStock], path: Path | None = None) -> None:
    target = path or ROOT / "watchlist.yaml"
    payload = {
        "stocks": [s.model_dump(exclude_none=True) for s in stocks],
    }
    with target.open("w", encoding="utf-8") as f:
        f.write("# 自选股：code 用 6 位数字；cost 为持仓成本（算 -5%/+20%）；qty 可选\n")
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def normalize_code(code: str) -> str:
    c = code.strip().upper().replace(".SH", "").replace(".SZ", "")
    c = "".join(ch for ch in c if ch.isdigit())
    if len(c) != 6:
        raise ValueError(f"invalid code: {code}")
    return c


def code_to_tencent(code: str) -> str:
    c = normalize_code(code)
    if c.startswith(("5", "6", "9")):
        return f"sh{c}"
    return f"sz{c}"
