from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.models import Situation

log = logging.getLogger("llm")

SYSTEM_PROMPT = """你是A股盯盘助手，风格类似同花顺问财的结构化判断。
要求：
1) 不要再写「买入/卖出/观望」开头（界面已单独显示指令），直接写原因摘要
2) 用一两句点出「三重确认」哪几项已满足/未满足（价位到位、量能配合、动量拐点）
3) 可点名支撑/压力数字；不要免责声明；总字数≤45字
示例：价位触及348支撑、量比0.76达标，但J未破10且无长下影，动量未拐点暂等。"""

CHAT_SYSTEM_PROMPT = """你是A股盘中助手。结合用户问题与盘面/三重确认上下文作答。
要求：直接给 买入/卖出/观望 + 依据；可引用支撑压力与 RSI/KDJ/量比；≤100字。"""


class LLMAdvisor:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 25.0,
        enabled: bool = True,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        # 模型名不内置默认值：未配置则视为未启用，避免回连到某个预设服务
        self.model = model or ""
        self.timeout = timeout
        self.enabled = enabled and bool(self.base_url and self.api_key and self.model)

    @classmethod
    def from_env_and_cfg(cls, cfg_llm: dict[str, Any] | None = None) -> "LLMAdvisor":
        cfg_llm = cfg_llm or {}
        return cls(
            base_url=os.getenv("LLM_BASE_URL", cfg_llm.get("base_url", "")),
            api_key=os.getenv("LLM_API_KEY", cfg_llm.get("api_key", "")),
            model=os.getenv("LLM_MODEL", cfg_llm.get("model", "")),
            timeout=float(cfg_llm.get("timeout_sec", 25)),
            enabled=bool(cfg_llm.get("enabled", True)),
        )

    def _endpoints(self) -> list[str]:
        base = self.base_url
        cands = [
            f"{base}/v1/chat/completions",
            f"{base}/chat/completions",
            base if base.endswith("/chat/completions") else "",
        ]
        return [u for u in cands if u]

    def chat(
        self,
        user_content: str,
        system: str | None = None,
        max_tokens: int = 160,
        temperature: float = 0.2,
    ) -> str | None:
        if not self.enabled:
            return None
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system or SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        for url in self._endpoints():
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    r = client.post(url, json=payload, headers=headers)
                    if r.status_code >= 400:
                        log.warning("llm http %s %s %s", url, r.status_code, r.text[:200])
                        last_err = RuntimeError(f"http {r.status_code}")
                        continue
                    data = r.json()
                    text = self._extract_text(data)
                    if text:
                        return text.strip().replace("\n", " ")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("llm call failed %s: %s", url, exc)
        if last_err:
            log.warning("llm chat gave up: %s", last_err)
        return None

    def _situation_user_prompt(self, sit: Situation) -> str:
        r1 = f"{sit.resistance:.3f}" if sit.resistance is not None else "-"
        s1 = f"{sit.support:.3f}" if sit.support is not None else "-"
        rs = ",".join(f"{x:.3f}" for x in (sit.resistances or [])[:3]) or "-"
        ss = ",".join(f"{x:.3f}" for x in (sit.supports or [])[:3]) or "-"
        return (
            f"标的:{sit.name}({sit.code})\n"
            f"周期:{sit.timeframe} 现价:{sit.price:.3f} 涨跌:{sit.change_pct}\n"
            f"趋势:{sit.trend} 规则建议:{sit.action} 置信度:{sit.confidence}\n"
            f"锁定压力三档:{rs} 当前压力:{r1}\n"
            f"锁定支撑三档:{ss} 当前支撑:{s1}\n"
            f"三重确认模式:{sit.checklist_mode or '无'} 通过:{sit.checklist_passed}/{sit.checklist_total}\n"
            f"三重确认明细:\n{sit.checklist_text or '无'}\n"
            f"波峰波谷:{sit.swing or '无'} 量价:{sit.vp_summary or '无'}\n"
            f"规则理由:{sit.reason}\n"
            f"请给出一句原因摘要（不要以买入/卖出/观望开头）。"
        )

    def advise(self, sit: Situation) -> str | None:
        return self.chat(self._situation_user_prompt(sit), system=SYSTEM_PROMPT, max_tokens=120)

    def answer_question(self, question: str, context: str) -> str | None:
        user = (
            f"【盘面上下文】\n{context}\n\n"
            f"【用户问题】\n{question.strip() or '现在该怎么操作？'}\n\n"
            f"请给出简短操作建议。"
        )
        return self.chat(user, system=CHAT_SYSTEM_PROMPT, max_tokens=180)

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text", "")))
                return "".join(parts)
            if isinstance(choices[0].get("text"), str):
                return choices[0]["text"]
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        if isinstance(data.get("response"), str):
            return data["response"]
        return ""
