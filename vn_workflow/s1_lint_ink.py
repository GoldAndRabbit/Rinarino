"""第 1 段闸门 CLI：编译 story.ink，跑静态判定，顺手落盘 story.json。

uv run python -m vn_workflow.s1_lint_ink --name americano
uv run python -m vn_workflow.s1_lint_ink --name americano --no-write   只验不写
"""

from __future__ import annotations

import argparse
import sys

from util.paths import Story

from .inklite import CompileError, compile_ink, lint_story
from .inklite.compiler import dump_story
from .inklite.lint import LintIssue, format_issues, has_errors


def check(story: Story, *, write: bool = True) -> tuple[list[LintIssue], str]:
    """返回 (问题列表, 可回灌给模型的报告)。编译失败时问题列表只有一条 syntax。"""
    try:
        compiled = compile_ink(story.path("story.ink"), speakers=story.speakers())
    except CompileError as exc:
        issue = LintIssue("error", "syntax", str(exc))
        return [issue], str(issue)
    compiled["title"] = compiled.get("title") or story.read_json("meta.json").get("title", "")
    issues = lint_story(compiled)
    if write and not has_errors(issues):
        dump_story(compiled, story.path("story.json"))
    return issues, format_issues(issues)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s1_lint_ink")
    ap.add_argument("--name", required=True)
    ap.add_argument("--no-write", action="store_true", help="只验，不写 story.json")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    story = Story(args.name)
    if not story.path("story.ink").exists():
        print(f"没有剧本：{story.path('story.ink')}", file=sys.stderr)
        return 1
    issues, report = check(story, write=not args.no_write)
    print(report or "[s1_lint] 干净：没有语法错、死节点、孤节点、成环，选项数达标")
    if has_errors(issues):
        print(f"[s1_lint] {sum(i.level == 'error' for i in issues)} 个错误", file=sys.stderr)
        return 1
    if not args.no_write:
        print(f"[s1_lint] ✓ {story.path('story.json').relative_to(story.dir.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
