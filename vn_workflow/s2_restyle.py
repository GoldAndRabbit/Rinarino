"""换画风：把当前的 house_style 套到一部已经做好的作品上。

`cast.json` 里同时住着两样东西——**画风**（art_direction）和**这部作品本身**
（角色长相、表情动作、背景与 CG 的分镜）。改了 vars.yaml 的 house_style 之后，
用 `--only cast --force` 重跑会把两样一起重写：角色换了张脸、站姿变成坐姿、
「三人同桌」被改写成空镜——那不是换画风，那是换了部作品。

这一段只动 art_direction：

  style        ← house_style 逐字覆盖
  avoid_style  ← house_avoid 整段换掉
  avoid        ← 只留这部作品自己的忌讳，上一版取向留下的画风词摘掉

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

# 负面词分两段存：avoid_style 是画风层的（换取向时整段换掉），avoid 是这部作品
# 自己的忌讳（多余人物、畸形手指、过曝…），换画风不该把它们丢掉。
#
# 分开之前的那一版把画风词直接并进了 avoid。下面这几个就是当时写进去的写实取向
# 反向词，迁移时从 avoid 里摘掉——留着的话「避免：厚涂、插画风」会和新取向正面对打。
_LEGACY_STYLE_AVOID = ("卡通", "线稿", "描边", "厚涂", "插画风", "二次元", "赛璐璐平涂")


def _split(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(w).strip() for w in raw if str(w).strip()]
    return [w.strip() for w in str(raw or "").split("、") if w.strip()]


def restyle(
    art: dict[str, Any], house_style: str, house_avoid: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """返回改过的 art_direction 和「从 avoid 里摘掉了哪些旧画风词」。"""
    art = dict(art)
    art["style"] = house_style
    stale = set(_LEGACY_STYLE_AVOID) | set(_split(art.get("avoid_style")))
    own = _split(art.get("avoid"))
    dropped = [w for w in own if w in stale]
    art["avoid"] = "、".join(w for w in own if w not in stale)
    art["avoid_style"] = "、".join(house_avoid)
    return art, dropped


def run(story: Story, *, dry_run: bool = False, verbose: bool = True) -> dict[str, Any]:
    cast = story.read_json("cast.json")
    defaults = prompts.defaults()
    house_style = " ".join(str(defaults.get("house_style", "")).split())
    if not house_style:
        raise SystemExit("[restyle] vars.yaml 里没有 house_style")
    house_avoid = _split(defaults.get("house_avoid"))

    art, dropped = restyle(cast.get("art_direction") or {}, house_style, house_avoid)
    if verbose:
        print(f"[restyle] {story.name} style ← house_style（{len(house_style)} 字）")
        print(f"[restyle] avoid_style ← {art['avoid_style'] or '（空）'}")
        print(f"[restyle] avoid 里摘掉的旧画风词：{'、'.join(dropped) or '无'}")
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
