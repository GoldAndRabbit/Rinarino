"""第 1 段：模型写 ink，两道确定性闸门验，不过就回灌重写，最多 4 次。

选 ink 而不是「让模型写一段散文」，唯一的理由就是**产物有编译器**。模型写 ink
会犯的错是有限几类——语法错、某个结点只给了 1 个选项、结局写了但走不到、
结点之间成环——这几类全部可以由程序判定，而且判定结果（第几行、哪个结点）
可以原样回灌给模型重写。这是整条流水线里唯一不需要人看的剧本质检。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from util import llm_api, prompts
from util.paths import ROOT, Story

from .inklite import CompileError, compile_text, lint_story
from .inklite.compiler import dump_story
from .inklite.lint import format_issues, has_errors

SHAPES = ROOT / "vn_workflow" / "shapes"
MAX_ROUNDS = 4


def shape_spec(shape: str) -> str:
    p = SHAPES / f"{shape}.md"
    if not p.exists():
        available = ", ".join(sorted(f.stem for f in SHAPES.glob("*.md")))
        raise SystemExit(f"没有这种剧情形态：{shape}（可用：{available}）")
    return p.read_text(encoding="utf-8")


def verify(src: str, story: Story) -> tuple[bool, str]:
    """跑两道闸门。返回 (是否通过, 可回灌的报告)。"""
    try:
        compiled = compile_text(src, speakers=story.speakers())
    except CompileError as exc:
        return False, f"编译不过：{exc}"
    issues = lint_story(compiled)
    if has_errors(issues):
        return False, "静态检查没过：\n" + format_issues(issues)
    return True, format_issues(issues)


async def run(
    story: Story, *, brief: str, shape: str, rounds: int = MAX_ROUNDS, verbose: bool = True
) -> Path:
    story.ensure()
    messages = [
        {"role": "system", "content": prompts.render("story.system")},
        {
            "role": "user",
            "content": prompts.render("story.user", shape_spec=shape_spec(shape), brief=brief),
        },
    ]
    for attempt in range(1, rounds + 1):
        if verbose:
            print(f"[s1_story] 第 {attempt}/{rounds} 次，模型在写…")
        raw = await llm_api.chat_complete(messages)
        src = llm_api.strip_fence(raw)
        ok, report = verify(src, story)
        if ok:
            story.path("story.ink").write_text(src.rstrip() + "\n", encoding="utf-8")
            compiled = compile_text(src, speakers=story.speakers())
            dump_story(compiled, story.path("story.json"))
            if verbose:
                print(
                    f"[s1_story] ✓ 过闸：{len(compiled['nodes'])} 结点，"
                    f"{len(compiled['endings'])} 结局"
                )
                if report:
                    print(report)
            return story.path("story.ink")
        if verbose:
            print(f"[s1_story] ✗ 没过：\n{report}")
        # 把判定结果原样回灌。模型看得到自己写的原文和精确到行的错。
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": prompts.render("story.retry", report=report)},
        ]
    raise SystemExit(f"[s1_story] 写了 {rounds} 次都没过闸，放弃")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s1_gen_story")
    ap.add_argument("--name", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--shape", default="romance_branch")
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if not llm_api.has_credentials():
        print(f"没有 {llm_api.credential_hint()}，这一段跑不了", file=sys.stderr)
        return 1
    asyncio.run(run(Story(args.name), brief=args.brief, shape=args.shape, rounds=args.rounds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
