"""换画风：把当前的 house_style 套到一部已经做好的作品上。

`cast.json` 里同时住着两样东西——**画风**（art_direction）和**这部作品本身**
（角色长相、表情动作、背景与 CG 的分镜）。改了 vars.yaml 的 house_style 之后，
用 `--only cast --force` 重跑会把两样一起重写：角色换了张脸、站姿变成坐姿、
「三人同桌」被改写成空镜——那不是换画风，那是换了部作品。

这一段只动 art_direction：

  style  ← house_style 逐字覆盖
  avoid  ← 并进反向画风词（旧的一条都不删）

角色、背景、CG、videos 一个字不碰。跑完再 `--only art --force` 重画就行，
剧本和设定都还是原来那部。

    uv run python -m vn_workflow.s2_restyle --name americano
    uv run python -m vn_workflow.s2_restyle --name americano --dry-run
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from util import prompts
from util.paths import Story

# 拼进正文的负面词。写实取向下这几条是**反向画风词**：模型只要往二次元飘一点，
# 它们就把它拽回来。和 avoid 里原有的词是并集——旧的都是这部作品自己的忌讳
# （多余人物、畸形手指、过曝…），换画风不该把它们一起丢掉。
STYLE_AVOID = ("卡通", "线稿", "描边", "厚涂", "插画风", "二次元", "赛璐璐平涂")


def restyle(art: dict[str, Any], house_style: str) -> tuple[dict[str, Any], list[str]]:
    """返回改过的 art_direction 和「这次加了哪些负面词」。"""
    art = dict(art)
    art["style"] = house_style
    old = [w.strip() for w in str(art.get("avoid", "")).split("、") if w.strip()]
    added = [w for w in STYLE_AVOID if w not in old]
    art["avoid"] = "、".join([*STYLE_AVOID, *old]) if added else art.get("avoid", "")
    return art, added


def run(story: Story, *, dry_run: bool = False, verbose: bool = True) -> dict[str, Any]:
    cast = story.read_json("cast.json")
    house_style = " ".join(str(prompts.defaults().get("house_style", "")).split())
    if not house_style:
        raise SystemExit("[restyle] vars.yaml 里没有 house_style")

    art, added = restyle(cast.get("art_direction") or {}, house_style)
    if verbose:
        print(f"[restyle] {story.name} style ← house_style（{len(house_style)} 字）")
        print(f"[restyle] avoid 补了：{'、'.join(added) or '无，已经都在了'}")
        print(
            "[restyle] 角色 / 背景 / CG 不动："
            f"{len(cast.get('characters') or [])} 人，"
            f"{len(cast.get('backgrounds') or {})} 背景，{len(cast.get('cgs') or {})} CG"
        )
    if dry_run:
        return art
    cast["art_direction"] = art
    story.write_json("cast.json", cast)
    if verbose:
        print("[restyle] ✓ 写回 cast.json —— 接着跑 --only art --force 重画")
    return art


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s2_restyle", description="只换画风，不动设定")
    ap.add_argument("--name", required=True)
    ap.add_argument("--dry-run", action="store_true", help="只看会改成什么，不写回")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    story = Story(args.name)
    if not story.path("cast.json").exists():
        raise SystemExit(f"{args.name} 还没有 cast.json，先跑 --only cast")
    run(story, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
