"""第 3 段闸门：对已经落盘的素材做判定，不生成。

    uv run python -m vn_workflow.s3_lint_art --name americano

判的都是「肉眼一看就知道废了、但程序也能判」的那几类：
  缺图     —— plan 里有、目录里没有
  画幅     —— 背景 / CG 不是 16:9，立绘不是竖版
  亮度     —— 整张过曝或全黑，或者干脆是一块纯色
  去背     —— 立绘没有 alpha、边缘没抠干净，或者身上还留着绿幕没抠掉
  底边     —— 脚下的地面没抠掉，或者人被裁成了半身像（写实取向下这两样最常见）
  一致性   —— 同一个角色的各张表情图主色调漂得太远（换了衣服 / 换了人）

占位图（.svg）跳过判定：它本来就不是要看的东西。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from util.paths import Story

from .s3_gen_art import ArtJob, build_plan, existing

ASPECT_TOL = 0.12
DARK, BRIGHT = 0.06, 0.94
FLAT_STD = 0.02
EDGE_ALPHA = 0.55
HUE_DRIFT = 0.22
# 立绘底边那条带子：人站着时底下只有两只鞋，占不到画面一半宽。
# 超过就是出事了，再看那截东西是什么颜色来分是哪一种事（见 _check_one）。
BOTTOM_WIDE = 0.40
BOTTOM_PALE = 0.55
# 抠干净的立绘实测在 0.2% 以下（口径见 image_api.green_residue）。1% 是出事线：
# 没抠掉的那块青绿曾经是 1.7%。
GREEN_RESIDUE = 0.01


class Issue:
    def __init__(self, level: str, code: str, message: str, asset: str = "") -> None:
        self.level, self.code, self.message, self.asset = level, code, message, asset

    def __str__(self) -> str:
        where = f"[{self.asset}] " if self.asset else ""
        return f"{self.level.upper():5s} {self.code:12s} {where}{self.message}"


def _stats(path: Path) -> dict[str, Any] | None:
    """缩到 64px 再统计，够判亮度和主色调，又不至于慢。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None
    with Image.open(path) as im:
        has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        w, h = im.size
        rgba = im.convert("RGBA").resize((64, 64))
    px = [rgba.getpixel((x, y)) for y in range(64) for x in range(64)]
    lum = [(0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 for r, g, b, _ in px]
    mean = sum(lum) / len(lum)
    std = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
    opaque = [p for p in px if p[3] > 32] or px
    avg = tuple(sum(c[i] for c in opaque) / len(opaque) / 255 for i in range(3))
    # 立绘抠完背之后，透明区的 RGB 是黑的（PNG 把全透明像素归零），
    # 拿整张算亮度等于在量那片黑——深色衣服的立绘会被误判成「整张全黑」。
    # 所以立绘的亮度只看不透明的那部分。
    mean_opaque = sum((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 for r, g, b, _ in opaque) / len(
        opaque
    )
    edge = [px[i] for i in range(64)] + [px[64 * 63 + i] for i in range(64)]
    edge += [px[r * 64] for r in range(64)] + [px[r * 64 + 63] for r in range(64)]

    # 最底下两行：各行不透明像素占多宽，以及这些像素里「浅色低饱和」占多少。
    # 宽而浅 = 脚下挂着没抠掉的地面；宽而实 = 人被裁断了，底边是身体。
    bottom_width, bottom_pale, bottom_n = 0.0, 0, 0
    for row in (62, 63):
        line = [px[row * 64 + c] for c in range(64)]
        solid = [p for p in line if p[3] > 32]
        bottom_width = max(bottom_width, len(solid) / 64)
        bottom_n += len(solid)
        bottom_pale += sum(
            1 for r, g, b, _ in solid if min(r, g, b) >= 168 and max(r, g, b) - min(r, g, b) <= 42
        )

    return {
        "w": w,
        "h": h,
        "alpha": has_alpha,
        "mean": mean,
        "mean_opaque": mean_opaque,
        "std": std,
        "avg": avg,
        "edge_transparent": sum(1 for p in edge if p[3] < 32) / len(edge),
        "green": 0.0,  # 立绘单独算，要拿 raw/ 的原色判（见 _check_one 的调用处）
        "bottom_width": bottom_width,
        "bottom_pale": bottom_pale / bottom_n if bottom_n else 0.0,
    }


def _check_one(job: ArtJob, path: Path, stats: dict[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    want = {"sprite": 9 / 16, "est": 16 / 9}.get(job.kind, 4 / 5)
    got = stats["w"] / stats["h"]
    if abs(got - want) / want > ASPECT_TOL:
        out.append(
            Issue("error", "aspect", f"画幅 {stats['w']}x{stats['h']}，期望比例 {want:.2f}", job.id)
        )
    lit = stats["mean_opaque"] if job.kind == "sprite" else stats["mean"]
    if lit < DARK:
        out.append(Issue("error", "too-dark", f"整张几乎全黑（亮度 {lit:.3f}）", job.id))
    elif stats["mean"] > BRIGHT and job.kind != "sprite":
        out.append(Issue("error", "too-bright", f"整张过曝（亮度 {stats['mean']:.3f}）", job.id))
    if stats["std"] < FLAT_STD:
        out.append(Issue("error", "flat", "整张是一块纯色，多半是生成失败", job.id))
    if job.kind == "sprite":
        if not stats["alpha"]:
            out.append(Issue("error", "no-alpha", "立绘没有 alpha 通道，没去背", job.id))
        elif stats["edge_transparent"] < EDGE_ALPHA:
            out.append(
                Issue(
                    "warn",
                    "cutout",
                    f"边缘只有 {stats['edge_transparent']:.0%} 是透明的，背没抠干净",
                    job.id,
                )
            )
        # 绿幕没抠干净：留下来的像素里还有原本是绿幕的。这条比边缘那圈环灵敏得多——
        # 它查的是**整张**，抠漏在哪儿都跑不掉。
        if (green := stats["green"]) > GREEN_RESIDUE:
            out.append(Issue("error", "green", f"身上有 {green:.1%} 的像素还发绿", job.id))
        # 边缘那圈环看不见脚下：地面残留在画面中下部，四边可能干干净净。
        # 所以底边单独判一次，再按颜色分成两种病。
        if stats["bottom_width"] > BOTTOM_WIDE:
            if stats["bottom_pale"] > BOTTOM_PALE:
                out.append(
                    Issue(
                        "error",
                        "ground",
                        f"底边 {stats['bottom_width']:.0%} 宽是浅色，脚下的地面/投影没抠掉",
                        job.id,
                    )
                )
            else:
                out.append(
                    Issue(
                        "error",
                        "cropped",
                        f"底边 {stats['bottom_width']:.0%} 宽都是人，多半被裁成了半身像",
                        job.id,
                    )
                )
    return out


def run(story: Story, *, verbose: bool = True) -> list[Issue]:
    jobs = build_plan(story)
    issues: list[Issue] = []
    stats_by_id: dict[str, dict[str, Any]] = {}
    placeholders = 0

    for job in jobs:
        path = existing(story, job)
        if path is None:
            issues.append(Issue("error", "missing", "plan 里有这张，但目录里没有", job.id))
            continue
        if path.suffix.lower() == ".svg":
            placeholders += 1
            continue
        stats = _stats(path)
        if stats is None:
            continue
        if job.kind == "sprite":
            # 绿残留要拿原图的颜色判：去溢色会把没抠干净的背景洗成灰的，
            # 只看成品会读到 0。没有 raw/ 的（换绿幕之前生成的）这条就跳过。
            raw = story.raw / path.name
            if raw.exists():
                from util import image_api

                stats["green"] = image_api.green_residue(path.read_bytes(), raw.read_bytes())
        stats_by_id[job.id] = stats
        issues += _check_one(job, path, stats)

    # 同人一致性：同一个角色的各张表情图，主色调不该漂得太远
    by_char: dict[str, list[tuple[str, tuple[float, ...]]]] = {}
    for job in jobs:
        if job.kind == "sprite" and job.id in stats_by_id:
            by_char.setdefault(job.character, []).append((job.id, stats_by_id[job.id]["avg"]))
    for char, items in by_char.items():
        base = next((v for i, v in items if i.endswith("_normal")), items[0][1])
        for aid, avg in items:
            drift = max(abs(a - b) for a, b in zip(avg, base, strict=False))
            if drift > HUE_DRIFT:
                issues.append(
                    Issue(
                        "warn",
                        "drift",
                        f"{char} 这张的主色调和 normal 差 {drift:.2f}，可能换了衣服或换了人",
                        aid,
                    )
                )

    if verbose:
        for issue in issues:
            print(issue)
        checked = len(stats_by_id)
        note = f"，{placeholders} 张占位图跳过" if placeholders else ""
        if not issues:
            print(f"[s3_lint] 干净：{checked} 张真素材全过{note}")
        else:
            errors = sum(i.level == "error" for i in issues)
            print(f"[s3_lint] {checked} 张真素材，{errors} 个错误{note}")
    return issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s3_lint_art")
    ap.add_argument("--name", required=True)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    issues = run(Story(args.name))
    return 1 if any(i.level == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
