"""提示词模板：vn_workflow/prompts_template/*.md + 一份变量表。

模板里用 `{{变量名}}` 占位，渲染时替换。变量有三层，后面的盖前面的：

  1. prompts_template/vars.yaml  —— 整个项目的默认值（画风取向、语言、口径…）
  2. 调用处传进来的 kwargs      —— 这一次生成的具体内容（剧本、设定、报告…）

两边都卡死：模板里出现了没给值的变量 → 报错；传了模板里根本没用的变量 → 也报错。
提示词是「代码」，拼错一个名字不该安静地渲染成空字符串跑完还花了钱。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from .paths import ROOT

TEMPLATES = ROOT / "vn_workflow" / "prompts_template"
VARS_FILE = TEMPLATES / "vars.yaml"

RE_SLOT = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


@lru_cache(maxsize=1)
def defaults() -> dict[str, Any]:
    """项目级默认变量。没有 yaml（或没装 pyyaml）就当空表，模板照样能跑。"""
    if not VARS_FILE.exists():
        return {}
    import yaml

    return yaml.safe_load(VARS_FILE.read_text(encoding="utf-8")) or {}


def slots(name: str) -> set[str]:
    """这个模板用到了哪些变量。"""
    return set(RE_SLOT.findall(template(name)))


def template(name: str) -> str:
    p = TEMPLATES / f"{name}.md"
    if not p.exists():
        have = ", ".join(sorted(f.stem for f in TEMPLATES.glob("*.md")))
        raise SystemExit(f"没有这个提示词模板：{name}（有的是：{have}）")
    return p.read_text(encoding="utf-8")


def render(name: str, **override: Any) -> str:
    """渲染模板。缺变量、多变量都直接报错，不做「猜你想要」。"""
    src = template(name)
    values: dict[str, Any] = {**defaults(), **override}
    used = set(RE_SLOT.findall(src))

    if missing := sorted(used - values.keys()):
        raise SystemExit(f"[prompts] {name} 缺变量：{', '.join(missing)}")
    if extra := sorted(set(override) - used):
        # 传了模板不认的变量：多半是模板改了名字而调用处没跟上
        raise SystemExit(f"[prompts] {name} 用不到这些变量：{', '.join(extra)}")

    return RE_SLOT.sub(lambda m: str(values[m.group(1)]), src)
