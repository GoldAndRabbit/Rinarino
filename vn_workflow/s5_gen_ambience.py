"""第 5 段：环境声。

amb.json 里每个 id 一段 prompt——那是给音频模型看的真源。没有可用的生成端时
落一段程序化合成的循环床（audio_synth.bed），文件名、清单和播放端的结构不变。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from util import audio_synth
from util.paths import Story

# prompt 里出现这些词就往「亮」的方向调，室内场景默认更闷
BRIGHT = ("街", "户外", "车", "人声", "雨", "风")


def tone_of(prompt: str) -> float:
    hits = sum(word in prompt for word in BRIGHT)
    return min(0.85, 0.18 + 0.16 * hits)


def existing(story: Story, aid: str) -> Path | None:
    for suffix in (".mp3", ".ogg", ".wav"):
        p = story.asset(f"{aid}{suffix}")
        if p.exists():
            return p
    return None


def run(story: Story, *, force: bool = False, verbose: bool = True) -> dict[str, Any]:
    spec: dict[str, str] = story.read_json("amb.json").get("ambience") or {}
    todo = [k for k in spec if force or existing(story, k) is None]
    if verbose:
        print(f"[amb] 需要 {len(spec)} 段，其中 {len(todo)} 段要生成（合成占位音）")
    story.ensure()
    for aid in todo:
        prompt = spec[aid]
        path = audio_synth.bed(
            story.asset(f"{aid}.wav"), tone=tone_of(prompt), seed=abs(hash(aid)) % 9999
        )
        if verbose:
            print(f"  ✓ {path.name}  ({path.stat().st_size // 1024}KB)")
    return {"planned": len(spec), "generated": len(todo)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s5_gen_ambience")
    ap.add_argument("--name", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    run(Story(args.name), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
