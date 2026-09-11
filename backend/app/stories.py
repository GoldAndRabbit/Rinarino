"""读 vn/stories/ 的仓库层。

后端只读不写：生成端往那个目录里放什么，这里就照原样端出去。
把整部小说打成一个 payload，前端一次取完，播放过程零请求。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from util.paths import DIST, STORIES, Story, all_stories

ASSET_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg", ".svg", ".mp4", ".webm", ".mp3", ".wav", ".ogg")

__all__ = ["STORIES", "DIST", "asset_index", "list_stories", "load_story", "resolve_asset"]


def asset_index(story: Story) -> dict[str, str]:
    """素材 id → URL。id 不带后缀，谁先落盘就用谁，占位图和真素材可以混着放。"""
    out: dict[str, str] = {}
    if not story.assets.is_dir():
        return out
    for p in sorted(story.assets.iterdir()):
        if p.suffix.lower() not in ASSET_SUFFIXES:
            continue
        out.setdefault(p.stem, f"/assets/{story.name}/{p.name}")
    return out


def _cover_url(story: Story, meta: dict[str, Any], assets: dict[str, str]) -> str | None:
    for key in (meta.get("cover"), "cg_three", "est_old_street"):
        if key and (url := assets.get(key)):
            return url
    return next(iter(assets.values()), None)


def list_stories() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for story in all_stories():
        meta = story.read_json("meta.json")
        assets = asset_index(story)
        compiled = story.read_json("story.json")
        items.append(
            {
                "name": story.name,
                "title": meta.get("title") or story.name,
                "subtitle": meta.get("subtitle", ""),
                "tagline": meta.get("tagline", ""),
                "shape": meta.get("shape", ""),
                "cover": _cover_url(story, meta, assets),
                "nodes": len(compiled.get("nodes", [])),
                "endings": len(meta.get("endings") or compiled.get("endings") or []),
                "characters": [
                    {"key": c["key"], "name": c["name"], "color": c.get("color", "")}
                    for c in story.read_json("cast.json").get("characters", [])
                ],
                "ready": bool(compiled),
            }
        )
    return items


def load_story(name: str) -> dict[str, Any] | None:
    story = Story(name)
    if not story.exists() or not story.path("meta.json").exists():
        return None
    compiled = story.read_json("story.json")
    meta = story.read_json("meta.json")
    assets = asset_index(story)
    prompts_file = story.assets / "prompts.json"
    prompts = (
        __import__("json").loads(prompts_file.read_text(encoding="utf-8"))
        if prompts_file.exists()
        else {}
    )
    return {
        "prompts": prompts,
        "name": story.name,
        "meta": meta,
        "cast": story.read_json("cast.json"),
        "amb": story.read_json("amb.json"),
        "story": compiled,
        "assets": assets,
        "cover": _cover_url(story, meta, assets),
        "brief": (
            story.path("brief.txt").read_text(encoding="utf-8").strip()
            if story.path("brief.txt").exists()
            else ""
        ),
    }


def resolve_asset(name: str, filename: str) -> Path | None:
    """挡住 ../ 一类的路径穿越：产物必须落在这部小说的 assets 目录里。"""
    base = (STORIES / name / "assets").resolve()
    try:
        target = (base / filename).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None
