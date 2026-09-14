"""vn_workflow_v2 主控：探索解谜玩法的工作流。

    uv run python -m vn_workflow_v2.pipeline --name stanley                  # lint → art → build
    uv run python -m vn_workflow_v2.pipeline --name stanley --only lint
    uv run python -m vn_workflow_v2.pipeline --name stanley --only art --dry-run   # 只报账

lint 不过，后面的段一律不跑：解不开的剧本配上美术也是白花钱。
art 有没画成的就非零退出（和 vn_workflow 的规矩一样：旧图还在，不喊的话看起来像成功了）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from util.paths import Story
from vn_workflow import s3_gen_art

from . import build, lint

STAGES = ("lint", "art", "build")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vn_workflow_v2.pipeline", description="探索解谜工作流")
    ap.add_argument("--name", required=True, help="vn/stories/ 下的目录名")
    ap.add_argument(
        "--only", action="append", default=[], choices=STAGES, help="只跑这些段（可重复）"
    )
    ap.add_argument("--force", action="store_true", help="art：无视缓存重画")
    ap.add_argument("--dry-run", action="store_true", help="art：只报账不生图")
    ap.add_argument("--inline", action="store_true", help="build：素材塞进 HTML")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    story = Story(args.name)
    if not story.path("story.json").exists():
        ap.error(f"{args.name} 还没有 story.json")
    stages = set(args.only) or set(STAGES)

    if "lint" in stages:
        print("── lint · validate + solve")
        if not lint.passed(lint.run(story)):
            print("[v2] 剧本没过闸，后面的段不跑了", file=sys.stderr)
            return 1

    if "art" in stages:
        print("\n── art · 背景 / CG")
        result = asyncio.run(s3_gen_art.run(story, force=args.force, dry_run=args.dry_run))
        if blocked := result.get("blocked"):
            print(f"[v2] ✗ {len(blocked)} 张没画成：{'、'.join(blocked)}", file=sys.stderr)
            return 1

    if "build" in stages:
        print("\n── build · 单文件页")
        build.build(story, inline=args.inline)

    print("\n[v2] ✓ 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
