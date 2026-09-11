"""主控：调度、缓存、闸门、回灌重试。

    # 从零起一个故事
    uv run python -m vn_workflow.pipeline --name island --shape survival \
        --brief "四个陌生人在一场船难后醒在同一座无人岛上…"

    # 只重跑某一类素材
    uv run python -m vn_workflow.pipeline --name island --only sprite --force

    # 改了 vars.yaml 的画风之后，把它套到已有的作品上（设定不动）
    uv run python -m vn_workflow.pipeline --name island --only restyle --only art --force

    # 只报账不动手
    uv run python -m vn_workflow.pipeline --name island --dry-run

六段流水线，每段都带缓存：产物存在就跳过，--force 才重来。中途断了直接重跑，
已经过闸的段不会重做——所以贵的那两段（art / video）不会因为后面报错而白花钱。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

from util.paths import STORIES, Story

from . import (
    s1_gen_story,
    s1_lint_ink,
    s2_gen_cast,
    s2_restyle,
    s3_gen_art,
    s3_lint_art,
    s4_gen_video,
    s5_gen_ambience,
    s5_gen_music,
    s6_build,
)

# --only 认的名字。前六个是段，后面几个是第 3 段的四类素材和两道闸门。
STAGES = ("story", "cast", "art", "video", "amb", "music", "build")
# 只认名字才跑的一段：全量流程里不出现，否则每次重跑都会把手改过的 style 冲掉
OPT_IN = ("restyle",)
# 报账那行按**执行顺序**印，restyle 夹在 cast 和 art 中间
ORDER = ("story", "cast", "restyle", "art", "video", "amb", "music", "build")
ART_KINDS = ("bg", "est", "cg", "sprite")
GATES = ("artlint",)
SELECTABLE = (*STAGES, *OPT_IN, *ART_KINDS, *GATES)


def resolve(only: list[str]) -> tuple[set[str], set[str]]:
    """--only 的参数 → (要跑的段, 第 3 段限定的素材类别)。"""
    if not only:
        return set(STAGES), set()
    stages = {o for o in only if o in STAGES}
    kinds = {o for o in only if o in ART_KINDS}
    if kinds:
        stages.add("art")
    if "artlint" in only:
        stages.add("artlint")
    stages |= {o for o in only if o in OPT_IN}
    return stages, kinds


def report(story: Story, stages: set[str], kinds: set[str]) -> None:
    """开跑前把账报出来，免得 --force 刷掉了什么心里没数。"""
    print(f"[plan] {story.name} → {story.dir}")
    print(f"[plan] 要跑：{', '.join(s for s in (*ORDER, *GATES) if s in stages)}")
    if kinds:
        print(f"[plan] 第 3 段只做：{', '.join(sorted(kinds))}")


async def run(
    name: str,
    *,
    brief: str = "",
    shape: str = "romance_branch",
    only: list[str] | None = None,
    force: bool = False,
    keep_meta: bool = False,
    dry_run: bool = False,
    inline: bool = False,
) -> dict[str, Any]:
    story = Story(name)
    stages, kinds = resolve(only or [])
    story.ensure()
    if brief:
        story.path("brief.txt").write_text(f"{shape} | {brief}\n", encoding="utf-8")
    report(story, stages, kinds)

    result: dict[str, Any] = {}
    t0 = time.perf_counter()

    def step(label: str) -> None:
        print(f"\n── {label}  ({time.perf_counter() - t0:.1f}s)")

    # 1 story
    if "story" in stages:
        step("1 story · 剧本")
        if not force and story.path("story.ink").exists():
            print("[story] 已有 story.ink，跳过（--force 重写）")
        elif dry_run:
            print("[story] dry-run")
        else:
            text = brief or story.path("brief.txt").read_text(encoding="utf-8").split("|", 1)[-1]
            await s1_gen_story.run(story, brief=text.strip(), shape=shape)
    # 闸门：无论剧本是这次写的还是上次留下的，都要过一遍
    if story.path("story.ink").exists() and not dry_run:
        issues, text = s1_lint_ink.check(story)
        if any(i.level == "error" for i in issues):
            print(text, file=sys.stderr)
            raise SystemExit("[pipeline] 剧本没过闸，后面的段不跑了")
        result["story"] = {"nodes": len(story.read_json("story.json").get("nodes", []))}

    # 2 cast
    if "cast" in stages:
        step("2 cast · 设定表")
        if not force and story.path("cast.json").exists():
            print("[cast] 已有 cast.json，跳过（--force 重写）")
        elif dry_run:
            print("[cast] dry-run")
        else:
            await s2_gen_cast.run(story, keep_meta=keep_meta)

    # 2.5 restyle：只把 house_style 换到 art_direction 上，设定一个字不动。
    # 不进全量流程——`--only restyle` 才跑，否则每次重跑都会把手改过的 style 冲掉。
    if "restyle" in stages:
        step("2.5 restyle · 换画风")
        s2_restyle.run(story, dry_run=dry_run)

    # 3 art
    if "art" in stages:
        step("3 art · 背景 / 定场图 / 事件 CG / 立绘")
        result["art"] = await s3_gen_art.run(
            story, only=kinds or None, force=force, dry_run=dry_run
        )
    if ("artlint" in stages or "art" in stages) and not dry_run:
        issues = s3_lint_art.run(story)
        result["artlint"] = sum(i.level == "error" for i in issues)

    # 4 video
    if "video" in stages:
        step("4 video · 过场视频")
        result["video"] = await s4_gen_video.run(story, force=force, dry_run=dry_run)

    # 5 amb / music
    if "amb" in stages and not dry_run:
        step("5 amb · 环境声")
        result["amb"] = s5_gen_ambience.run(story, force=force)
    if "music" in stages and not dry_run:
        step("5 music · BGM")
        result["music"] = s5_gen_music.run(story, force=force)

    # 6 build
    if "build" in stages and not dry_run:
        step("6 build · 打包")
        s6_build.build(story, inline=inline)

    print(f"\n[pipeline] ✓ 完成，{time.perf_counter() - t0:.1f}s")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vn_workflow.pipeline", description="视觉小说生成流水线")
    ap.add_argument("--name", required=True, help="小说名，也是 vn/stories/ 下的目录名")
    ap.add_argument("--brief", default="", help="一句设定")
    ap.add_argument("--shape", default="romance_branch", help="剧情形态，见 vn_workflow/shapes/")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="STAGE",
        help=f"只跑这些：{', '.join(SELECTABLE)}（可重复）",
    )
    ap.add_argument("--force", action="store_true", help="无视缓存，重做")
    ap.add_argument("--keep-meta", action="store_true", help="重跑 cast 时不覆盖界面文案")
    ap.add_argument("--dry-run", action="store_true", help="只报账，不生成")
    ap.add_argument("--inline", action="store_true", help="打包成单文件 HTML")
    ap.add_argument("--list", action="store_true", help="列出已有的小说")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        for p in sorted(STORIES.iterdir()) if STORIES.is_dir() else []:
            if (p / "meta.json").exists():
                meta = Story(p.name).read_json("meta.json")
                print(f"{p.name:16s} {meta.get('title', '')}")
        return 0

    bad = [o for o in args.only if o not in SELECTABLE]
    if bad:
        ap.error(f"--only 不认识：{', '.join(bad)}（可用：{', '.join(SELECTABLE)}）")

    story = Story(args.name)
    if not story.exists() and not args.brief:
        ap.error(f"{args.name} 是新的，需要 --brief 给一句设定")

    asyncio.run(
        run(
            args.name,
            brief=args.brief,
            shape=args.shape,
            only=args.only,
            force=args.force,
            keep_meta=args.keep_meta,
            dry_run=args.dry_run,
            inline=args.inline,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
