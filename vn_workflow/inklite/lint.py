"""第 1 段闸门：对编译产物做纯静态判定。

不看文笔，只看图结构——这些错模型会反复犯，而且每一条都能精确到结点，
所以判定结果可以原样回灌给模型重写。这是整条流水线里唯一不需要人看的剧本质检。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .compiler import TERMINALS

# 模拟时最多展开多少个 (结点, 变量) 状态。剧本小，正常几百个就跑完；
# 超了说明分支爆炸，这条检查就放弃，不去猜。
SIM_BUDGET = 40_000

Level = Literal["error", "warn"]


@dataclass(frozen=True)
class LintIssue:
    level: Level
    code: str
    message: str
    node: str = ""

    def __str__(self) -> str:
        where = f"[{self.node}] " if self.node else ""
        return f"{self.level.upper():5s} {self.code:14s} {where}{self.message}"


def _edges(node: dict[str, Any]) -> list[tuple[str, str]]:
    """(落点, 来源描述)。"""
    out = [(d["target"], "条件跳转") for d in node.get("diverts", [])]
    out += [(c["target"], f"选项「{c['text']}」") for c in node.get("choices", [])]
    if nxt := node.get("next"):
        out.append((nxt, "跳转"))
    return out


def _collect_vars(node: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(被写入的变量, 被读取的变量)。"""
    written = {e[0] for e in node.get("effects", [])}
    read = {c[0] for d in node.get("diverts", []) for c in d["cond"]}
    for choice in node.get("choices", []):
        written |= {e[0] for e in choice.get("effects", [])}
        read |= {c[0] for c in choice.get("cond", [])}
    return written, read


OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}
APPLY = {"=": lambda _c, v: v, "+=": lambda c, v: c + v, "-=": lambda c, v: c - v}


def _test(cond: list[list[Any]] | None, variables: dict[str, Any]) -> bool:
    if not cond:
        return True
    return all(OPS[op](variables.get(name, 0), value) for name, op, value in cond)


def _apply(effects: list[list[Any]] | None, variables: dict[str, Any]) -> dict[str, Any]:
    out = dict(variables)
    for name, op, value in effects or []:
        out[name] = APPLY.get(op, APPLY["="])(out.get(name, 0), value)
    return out


def simulate(story: dict[str, Any]) -> tuple[set[str], bool]:
    """把所有选项组合走一遍，返回 (真正走得到的结点, 预算是否够用)。

    图可达性只看有没有边；这里连**变量**一起算。`{好感 >= 3: -> 真结局}` 这种
    条件跳转，如果任何一条路径都攒不出低于 3 的好感，擦肩结局就是死的——
    边在图上是连着的，玩家却永远看不到。这类错一眼看不出来，但程序一跑就知道。
    """
    by_id = {n["id"]: n for n in story.get("nodes", [])}
    start = story.get("start")
    if start not in by_id:
        return set(), True

    reached: set[str] = set()
    seen: set[tuple[str, tuple[tuple[str, Any], ...]]] = set()
    stack: list[tuple[str, dict[str, Any]]] = [(start, dict(story.get("vars", {})))]
    budget = SIM_BUDGET
    while stack:
        nid, variables = stack.pop()
        if nid in TERMINALS or nid not in by_id:
            continue
        key = (nid, tuple(sorted(variables.items())))
        if key in seen:
            continue
        seen.add(key)
        budget -= 1
        if budget <= 0:
            return reached, False
        reached.add(nid)
        node = by_id[nid]
        variables = _apply(node.get("effects"), variables)

        for divert in node.get("diverts", []):
            if _test(divert["cond"], variables):
                stack.append((divert["target"], variables))
                break
        else:
            choices = [c for c in node.get("choices", []) if _test(c.get("cond"), variables)]
            for choice in choices:
                stack.append((choice["target"], _apply(choice.get("effects"), variables)))
            if not choices and node.get("next"):
                stack.append((node["next"], variables))
    return reached, True


def lint_story(story: dict[str, Any], *, min_choices: int = 2) -> list[LintIssue]:
    issues: list[LintIssue] = []
    nodes: list[dict[str, Any]] = story.get("nodes", [])
    by_id = {n["id"]: n for n in nodes}
    start = story.get("start")
    declared = set(story.get("vars", {}))

    # 死节点：跳到一个不存在的结点
    for node in nodes:
        for target, why in _edges(node):
            if target in TERMINALS or target in by_id:
                continue
            issues.append(
                LintIssue("error", "dead-link", f"{why} 跳向不存在的结点 {target}", node["id"])
            )

    # 出口：既没有选项也没有跳转 —— 玩家会卡死在这一屏
    for node in nodes:
        if not node.get("choices") and not node.get("next") and not node.get("diverts"):
            issues.append(LintIssue("error", "no-exit", "结点既没有选项也没有跳转", node["id"]))
        elif node.get("diverts") and not node.get("choices") and not node.get("next"):
            issues.append(
                LintIssue(
                    "error", "no-fallback", "只有条件跳转，条件都不成立时无路可走", node["id"]
                )
            )

    # 选项数：分支剧情里只给 1 个选项等于没得选
    for node in nodes:
        count = len(node.get("choices", []))
        if 0 < count < min_choices:
            issues.append(
                LintIssue("error", "too-few-choices", f"只给了 {count} 个选项", node["id"])
            )

    # 可达性 —— 从 start 走一遍
    if start not in by_id:
        issues.append(LintIssue("error", "no-start", f"起点结点 {start} 不存在"))
        return issues
    reached: set[str] = set()
    stack = [start]
    while stack:
        nid = stack.pop()
        if nid in reached or nid in TERMINALS or nid not in by_id:
            continue
        reached.add(nid)
        stack += [t for t, _ in _edges(by_id[nid])]
    for node in nodes:
        if node["id"] not in reached:
            issues.append(LintIssue("error", "orphan", "从起点走不到这个结点", node["id"]))

    # 成环：只看「没有选项」的跳转子图，这种环玩家按不了任何键，必然死循环
    plain: dict[str, str] = {
        n["id"]: n["next"] for n in nodes if n.get("next") and not n.get("choices")
    }
    seen_cycle: set[str] = set()
    for origin in plain:
        slow, fast = origin, origin
        while True:
            fast = plain.get(plain.get(fast, ""), "")
            slow = plain.get(slow, "")
            if not fast or not slow:
                break
            if fast == slow:
                if fast not in seen_cycle:
                    seen_cycle.add(fast)
                    issues.append(
                        LintIssue("error", "cycle", "无选项的跳转成环，玩家会卡死在里面", fast)
                    )
                break

    # 结局：至少要走得到一个 END
    endings = [
        n["id"]
        for n in nodes
        if n["id"] in reached
        and (
            n.get("next") in TERMINALS
            or any(c["target"] in TERMINALS for c in n.get("choices", []))
        )
    ]
    if not endings:
        issues.append(LintIssue("error", "no-ending", "没有任何可达的结局（-> END）"))
    elif len(endings) < 2:
        issues.append(LintIssue("warn", "single-ending", f"只有 1 个结局：{endings[0]}"))

    # 真可达性：把变量也算上，走一遍所有选项组合
    playable, enough = simulate(story)
    if not enough:
        issues.append(LintIssue("warn", "sim-budget", "分支太多，跳过了「带变量的可达性」检查"))
    else:
        for node in nodes:
            nid = node["id"]
            if nid in reached and nid not in playable:
                issues.append(
                    LintIssue(
                        "error",
                        "unplayable",
                        "图上连着，但没有任何一组选择能走到——多半是条件跳转的阈值跟"
                        "变量能攒到的范围对不上",
                        nid,
                    )
                )

    # 变量：读了没声明的、声明了没人读的
    written: set[str] = set()
    read: set[str] = set()
    for node in nodes:
        w, r = _collect_vars(node)
        written |= w
        read |= r
    for name in sorted(read - declared):
        issues.append(LintIssue("error", "undeclared-var", f"条件里读了未声明的变量 {name}"))
    for name in sorted(declared - read):
        issues.append(LintIssue("warn", "unused-var", f"变量 {name} 从没被任何条件读过"))
    for name in sorted(read - written):
        issues.append(LintIssue("warn", "never-written", f"变量 {name} 从没被任何选项改过"))

    return issues


def format_issues(issues: list[LintIssue]) -> str:
    return "\n".join(str(i) for i in issues)


def has_errors(issues: list[LintIssue]) -> bool:
    return any(i.level == "error" for i in issues)
