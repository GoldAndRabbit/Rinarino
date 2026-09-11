"""第 5 段备选：BGM。

和环境声是二选一的关系——一部作品要么靠环境声撑气氛，要么给它写曲子，
两样都堆上去会打架。amb.json 的 music 段是 prompt 真源。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from util import audio_synth
from util.paths import Story

# 结局段抬一个大三度，比主题亮
ROOTS = {"ending": 220.00, "default": 174.61}


def root_of(mid: str) -> float:
    return ROOTS["ending"] if "end" in mid else ROOTS["default"]


def existing(story: Story, mid: str) -> Path | None:
    for suffix in (".mp3", ".ogg", ".wav"):
        p = story.asset(f"{mid}{suffix}")
        if p.exists():
            return p
    return None


def run(story: Story, *, force: bool = False, verbose: bool = True) -> dict[str, Any]:
    spec: dict[str, str] = story.read_json("amb.json").get("music") or {}
    todo = [k for k in spec if force or existing(story, k) is None]
    if verbose:
        print(f"[music] 需要 {len(spec)} 段，其中 {len(todo)} 段要生成（合成占位音）")
    story.ensure()
    for mid in todo:
        path = audio_synth.pad(
            story.asset(f"{mid}.wav"), root=root_of(mid), seed=abs(hash(mid)) % 9999
        )
        if verbose:
            print(f"  ✓ {path.name}  ({path.stat().st_size // 1024}KB)")
    return {"planned": len(spec), "generated": len(todo)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s5_gen_music")
    ap.add_argument("--name", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    run(Story(args.name), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
