"""ink 子集 → story.json。

支持的语法（够视觉小说用，且每一条都能被程序判定）：

    // 行注释
    VAR affinity_zhao = 0

    === opening ===                 结点（knot）
    # node: 开场                     结点在分支图上的名字
    # bg: bg_cafe_day                进场指令：背景 / CG / 立绘 / 环境声 / BGM / 视频
    # sprite: zhao:normal, zhi:smile
    旁白文本                          没有「名字:」前缀 → 旁白
    陆昭: 台词                        有前缀 → 该角色说话
    ~ affinity_zhao += 1             赋值（= += -=）
    {affinity_zhao >= 3: -> end_a}   条件跳转，写在选项前，先命中先走
    * [选项文本]                      选项；* 一次性，+ 可重复
        ~ affinity_zhao += 1         选项体：缩进，可含台词 / 赋值
        -> zhao_2                    选项落点
    -> END                           无选项结点的直接跳转；END 收尾

编译产物是一张显式的图：nodes / choices / diverts 全是 id 引用，
于是「死节点、孤节点、成环、选项数」都能由 lint 纯静态判掉。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

END = "END"
DONE = "DONE"
TERMINALS = {END, DONE}

DIRECTIVE_KEYS = {"node", "bg", "cg", "sprite", "amb", "music", "video", "clear"}

RE_KNOT = re.compile(r"^={2,}\s*(\w+)\s*={0,}\s*$")
RE_VAR = re.compile(r"^VAR\s+(\w+)\s*=\s*(.+?)\s*$")
RE_DIVERT = re.compile(r"^->\s*([\w.]+)\s*$")
RE_CHOICE = re.compile(r"^([*+])\s*(.*)$")
RE_EFFECT = re.compile(r"^~\s*(\w+)\s*(=|\+=|-=)\s*(.+?)\s*$")
RE_COND_LINE = re.compile(r"^\{(.+?)\s*:\s*(.+?)\}$")
RE_TAG = re.compile(r"^#\s*(\w+)\s*:\s*(.*)$")
RE_SPEAKER = re.compile(r"^([^:：\s][^:：]{0,15})[:：]\s*(.+)$")
RE_COND = re.compile(r"^(\w+)\s*(>=|<=|==|!=|>|<)\s*(-?\d+)$")
RE_CHOICE_TEXT = re.compile(r"^(?:\{(.+?)\}\s*)?\[(.*?)\]\s*(?:->\s*([\w.]+))?\s*$")


class CompileError(Exception):
    """带行号的编译错误。行号原样回灌给模型，它才知道改哪儿。"""

    def __init__(self, line_no: int, message: str, source: str = "") -> None:
        tail = f"  |  {source.strip()}" if source else ""
        super().__init__(f"第 {line_no} 行: {message}{tail}")
        self.line_no = line_no
        self.message = message
        self.source = source.strip()


@dataclass
class Line:
    speaker: str | None
    text: str
    tags: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text}
        if self.speaker:
            out["speaker"] = self.speaker
        if self.tags:
            out["tags"] = self.tags
        return out


@dataclass
class Choice:
    text: str
    target: str
    cond: list[list[Any]] = field(default_factory=list)
    effects: list[list[Any]] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    sticky: bool = False
    line_no: int = 0

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text, "target": self.target}
        if self.cond:
            out["cond"] = self.cond
        if self.effects:
            out["effects"] = self.effects
        if self.lines:
            out["lines"] = [i.to_json() for i in self.lines]
        if self.sticky:
            out["sticky"] = True
        return out


@dataclass
class Node:
    id: str
    label: str = ""
    line_no: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)
    effects: list[list[Any]] = field(default_factory=list)
    diverts: list[dict[str, Any]] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)
    next: str | None = None

    def targets(self) -> list[tuple[str, int]]:
        out = [(d["target"], self.line_no) for d in self.diverts]
        out += [(c.target, c.line_no) for c in self.choices]
        if self.next:
            out.append((self.next, self.line_no))
        return out

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "label": self.label or self.id}
        if self.tags:
            out["tags"] = self.tags
        if self.effects:
            out["effects"] = self.effects
        out["lines"] = [i.to_json() for i in self.lines]
        if self.diverts:
            out["diverts"] = self.diverts
        if self.choices:
            out["choices"] = [c.to_json() for c in self.choices]
        if self.next:
            out["next"] = self.next
        return out


def parse_cond(expr: str, line_no: int, source: str) -> list[list[Any]]:
    """`a >= 2 && b < 1` → [["a", ">=", 2], ["b", "<", 1]]，全部与关系。"""
    clauses: list[list[Any]] = []
    for part in re.split(r"&&|\band\b", expr):
        part = part.strip()
        if not part:
            continue
        m = RE_COND.match(part)
        if not m:
            raise CompileError(line_no, f"看不懂的条件 {part!r}（只支持 变量 比较符 整数）", source)
        clauses.append([m.group(1), m.group(2), int(m.group(3))])
    if not clauses:
        raise CompileError(line_no, "条件是空的", source)
    return clauses


def parse_value(raw: str, line_no: int, source: str) -> Any:
    raw = raw.strip()
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw in ("true", "false"):
        return raw == "true"
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    raise CompileError(line_no, f"看不懂的值 {raw!r}（只支持整数 / true / false / 字符串）", source)


def compile_text(
    src: str, *, title: str = "", speakers: dict[str, str] | None = None
) -> dict[str, Any]:
    """把 ink 文本编译成 story dict。speakers 把中文名映射成 cast key。"""
    alias = {k.lower(): v for k, v in (speakers or {}).items()}
    variables: dict[str, Any] = {}
    nodes: list[Node] = []
    current: Node | None = None
    pending_tags: dict[str, str] = {}
    open_choice: Choice | None = None
    meta_title = title

    def flush_tags(into: Line) -> None:
        nonlocal pending_tags
        if pending_tags:
            into.tags.update(pending_tags)
            pending_tags = {}

    def sink() -> tuple[list[Line], list[list[Any]]]:
        """当前该往哪儿写：选项体里，还是结点正文里。"""
        if open_choice is not None:
            return open_choice.lines, open_choice.effects
        assert current is not None
        return current.lines, current.effects

    raw_lines = src.splitlines()
    for idx, raw in enumerate(raw_lines, start=1):
        stripped = raw.split("//")[0].rstrip() if "//" in raw else raw.rstrip()
        body = stripped.strip()
        if not body:
            continue
        indented = len(stripped) - len(stripped.lstrip()) >= 2

        if m := RE_KNOT.match(body):
            current = Node(id=m.group(1), line_no=idx)
            if m.group(1) in {n.id for n in nodes}:
                raise CompileError(idx, f"结点 {m.group(1)} 重复定义", raw)
            nodes.append(current)
            open_choice = None
            pending_tags = {}
            continue

        if m := RE_VAR.match(body):
            if current is not None:
                raise CompileError(idx, "VAR 只能写在第一个结点之前", raw)
            variables[m.group(1)] = parse_value(m.group(2), idx, raw)
            continue

        if body.startswith("#") and (m := RE_TAG.match(body)):
            key, value = m.group(1), m.group(2).strip()
            if key == "title" and current is None:
                meta_title = value
                continue
            if key not in DIRECTIVE_KEYS:
                known = ", ".join(sorted(DIRECTIVE_KEYS))
                raise CompileError(idx, f"不认识的指令 #{key}（可用：{known}）", raw)
            if current is None:
                raise CompileError(idx, "指令写在了第一个结点之前", raw)
            if key == "node":
                current.label = value
                continue
            if not current.lines and open_choice is None:
                current.tags[key] = value
            else:
                pending_tags[key] = value
            continue

        if current is None:
            raise CompileError(idx, "正文写在了第一个结点之前（缺 === knot ===）", raw)

        if m := RE_CHOICE.match(body):
            sticky = m.group(1) == "+"
            cm = RE_CHOICE_TEXT.match(m.group(2).strip())
            if not cm:
                raise CompileError(idx, "选项要写成 `* [文本] -> 落点` 或 `* {条件} [文本]`", raw)
            cond_src, text, target = cm.group(1), cm.group(2).strip(), cm.group(3)
            if not text:
                raise CompileError(idx, "选项文本是空的", raw)
            open_choice = Choice(
                text=text,
                target=target or "",
                cond=parse_cond(cond_src, idx, raw) if cond_src else [],
                sticky=sticky,
                line_no=idx,
            )
            current.choices.append(open_choice)
            continue

        if m := RE_EFFECT.match(body):
            name, op, value = m.group(1), m.group(2), parse_value(m.group(3), idx, raw)
            if name not in variables:
                raise CompileError(idx, f"变量 {name} 没有用 VAR 声明", raw)
            sink()[1].append([name, op, value])
            continue

        if m := RE_COND_LINE.match(body):
            inner = m.group(2).strip()
            dm = RE_DIVERT.match(inner)
            if not dm:
                raise CompileError(idx, "条件行目前只支持 `{条件: -> 落点}`", raw)
            if open_choice is not None:
                raise CompileError(idx, "条件跳转不能写在选项体里", raw)
            if current.choices:
                raise CompileError(idx, "条件跳转要写在选项前面", raw)
            current.diverts.append(
                {"cond": parse_cond(m.group(1), idx, raw), "target": dm.group(1)}
            )
            continue

        if m := RE_DIVERT.match(body):
            target = m.group(1)
            if open_choice is not None:
                open_choice.target = target
                open_choice = None
            elif current.choices:
                raise CompileError(idx, "结点已经有选项了，不该再有裸跳转", raw)
            elif current.next:
                raise CompileError(idx, f"结点 {current.id} 有两个跳转", raw)
            else:
                current.next = target
            continue

        if body.startswith(("*", "+", "~", "->", "{", "}", "=")):
            raise CompileError(idx, "看不懂这一行", raw)
        if open_choice is not None and not indented:
            open_choice = None

        speaker = None
        text = body
        if sm := RE_SPEAKER.match(body):
            name = sm.group(1).strip()
            key = alias.get(name.lower())
            if key or name.lower() in alias.values():
                speaker = key or name.lower()
                text = sm.group(2).strip()
        line = Line(speaker=speaker, text=text)
        flush_tags(line)
        sink()[0].append(line)

    if not nodes:
        raise CompileError(0, "一个结点都没有")

    for node in nodes:
        for choice in node.choices:
            if not choice.target:
                raise CompileError(choice.line_no, f"选项「{choice.text}」没有落点（缺 -> ）")

    story: dict[str, Any] = {
        "version": 1,
        "title": meta_title,
        "vars": variables,
        "start": nodes[0].id,
        "nodes": [n.to_json() for n in nodes],
    }
    story["endings"] = [
        n.id for n in nodes if n.next in TERMINALS or any(c.target in TERMINALS for c in n.choices)
    ]
    return story


def compile_ink(
    path: str | Path, *, title: str = "", speakers: dict[str, str] | None = None
) -> dict[str, Any]:
    p = Path(path)
    return compile_text(p.read_text(encoding="utf-8"), title=title, speakers=speakers)


def dump_story(story: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
