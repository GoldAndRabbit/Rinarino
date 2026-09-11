"""第 4 段：过场视频（火山引擎 Seedance，异步任务制）。

视频比别的贵得多，所以这一段的缓存最严：只做 cast.json 的 videos 段里列出来的，
而且默认拿已经生成好的背景 / CG 当参考图——过场和正片的画风必须是同一套，
靠文字描述对不齐，靠参考图才对得齐。

没有 ARK_API_KEY 时整段跳过（不造占位视频），播放端遇到缺失的 video 直接不放。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from typing import Any

from util import image_api, pricing, video_api
from util.paths import Story


def ref_for(story: Story, vid: str, spec: str, assets: dict[str, str]) -> list[str]:
    """给这条视频挑参考图：prompt 里点名的 id 优先，否则退回封面。"""
    named = [aid for aid in assets if aid in spec]
    if named:
        return [assets[a] for a in named[:2]]
    meta = story.read_json("meta.json")
    cover = meta.get("cover")
    return [assets[cover]] if cover in assets else []


def local_assets(story: Story) -> dict[str, str]:
    if not story.assets.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(story.assets.iterdir()):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            out.setdefault(p.stem, str(p))
    return out


async def run(
    story: Story, *, force: bool = False, dry_run: bool = False, verbose: bool = True
) -> dict[str, Any]:
    spec: dict[str, str] = story.read_json("cast.json").get("videos") or {}
    todo = [k for k in spec if force or not story.asset(f"{k}.mp4").exists()]
    have_key = image_api.has_credentials()

    if verbose:
        cfg = video_api.seedance_config()
        seconds = len(todo) * int(cfg["duration"])
        cost = pricing.fmt(seconds * pricing.video_unit(cfg["model"]))
        print(f"[video] 需要 {len(spec)} 条，其中 {len(todo)} 条要生成，共 {seconds}s，约 {cost}")
        if not have_key:
            print(f"[video] 没有 {image_api.credential_hint()}，整段跳过")
    if dry_run or not have_key or not todo:
        return {"planned": len(spec), "todo": len(todo), "generated": 0}

    story.ensure()
    assets = local_assets(story)
    manifest_path = story.asset("videos.json")
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    art = story.read_json("cast.json").get("art_direction", {})
    done = 0
    for vid in todo:
        prompt = "，".join(p for p in (art.get("style", ""), spec[vid], art.get("camera", "")) if p)
        refs = ref_for(story, vid, spec[vid], assets)
        t0 = time.perf_counter()

        def tick(status: str, _t: dict[str, Any], _v: str = vid, _t0: float = t0) -> None:
            if verbose:
                print(f"  [{_v}] {status}  {time.perf_counter() - _t0:.0f}s", file=sys.stderr)

        mp4 = await video_api.generate_video(
            prompt,
            images=refs or None,
            seed=int(hashlib.sha256(vid.encode()).hexdigest()[:8], 16),
            on_tick=tick,
        )
        out = story.asset(f"{vid}.mp4")
        out.write_bytes(mp4)
        manifest[vid] = {
            "file": out.name,
            "prompt": prompt,
            "refs": [str(r) for r in refs],
            "seconds": round(time.perf_counter() - t0, 1),
        }
        done += 1
        if verbose:
            print(f"  ✓ {out.name} ({len(mp4) // 1024}KB)")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"planned": len(spec), "todo": len(todo), "generated": done}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s4_gen_video")
    ap.add_argument("--name", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    asyncio.run(run(Story(args.name), force=args.force, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
