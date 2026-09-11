"""第 3 段闸门：对已经落盘的素材做判定，不生成。

    uv run python -m vn_workflow.s3_lint_art --name americano

判的都是「肉眼一看就知道废了、但程序也能判」的那几类：
  缺图     —— plan 里有、目录里没有
  画幅     —— 背景 / CG 不是 16:9，立绘不是竖版
  亮度     —— 整张过曝或全黑，或者干脆是一块纯色
  去背     —— 立绘没有 alpha，或者边缘没抠干净
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
    edge = [px[i] for i in range(64)] + [px[64 * 63 + i] for i in range(64)]
    edge += [px[r * 64] for r in range(64)] + [px[r * 64 + 63] for r in range(64)]
    return {
        "w": w,
        "h": h,
        "alpha": has_alpha,
        "mean": mean,
        "std": std,
        "avg": avg,
        "edge_transparent": sum(1 for p in edge if p[3] < 32) / len(edge),
    }


def _check_one(job: ArtJob, path: Path, stats: dict[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    want = {"sprite": 9 / 16, "est": 16 / 9}.get(job.kind, 4 / 5)
    got = stats["w"] / stats["h"]
    if abs(got - want) / want > ASPECT_TOL:
        out.append(
            Issue("error", "aspect", f"画幅 {stats['w']}x{stats['h']}，期望比例 {want:.2f}", job.id)
        )
    if stats["mean"] < DARK:
        out.append(Issue("error", "too-dark", f"整张几乎全黑（亮度 {stats['mean']:.3f}）", job.id))
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
