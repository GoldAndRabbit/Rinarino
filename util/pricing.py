"""价格表。只服务于「开跑前把账报出来」，不参与任何计费逻辑。

数字来自 config/llm_api.yaml 的 pricing 段——放配置而不是散在代码里，
是因为价目表会变，而改价不该需要动代码。真实账单以控制台为准。
"""

from __future__ import annotations

from typing import Any

from .llm_api import load_yaml

__all__ = ["currency", "image_unit", "video_unit", "fmt"]


def _table(kind: str) -> dict[str, Any]:
    pricing = load_yaml().get("pricing") or {}
    table = pricing.get(kind)
    return table if isinstance(table, dict) else {}


def currency() -> str:
    return str((load_yaml().get("pricing") or {}).get("currency", "CNY"))


def _lookup(kind: str, model: str | None, fallback: float) -> float:
    table = _table(kind)
    if model and model in table:
        return float(table[model])
    return float(table.get("default", fallback))


def image_unit(model: str | None = None) -> float:
    """元/张。"""
    return _lookup("image", model, 0.22)


def video_unit(model: str | None = None) -> float:
    """元/秒。"""
    return _lookup("video", model, 1.0)


def fmt(amount: float) -> str:
    symbol = {"CNY": "¥", "USD": "$"}.get(currency(), "")
    return f"{symbol}{amount:.2f}"
