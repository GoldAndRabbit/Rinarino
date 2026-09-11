"""目录约定。生成端只往 vn/stories/<名字>/ 写文件，这是它和播放端唯一的交接点。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STORIES = ROOT / "vn" / "stories"
DIST = ROOT / "vn" / "dist"


@dataclass(frozen=True)
class Story:
    name: str

    @property
    def dir(self) -> Path:
        return STORIES / self.name

    @property
    def assets(self) -> Path:
        return self.dir / "assets"

    def path(self, filename: str) -> Path:
        return self.dir / filename

    def asset(self, filename: str) -> Path:
        return self.assets / filename

    def exists(self) -> bool:
        return self.dir.is_dir()

    def read_json(self, filename: str, default: Any = None) -> Any:
        p = self.path(filename)
        if not p.exists():
            return {} if default is None else default
        return json.loads(p.read_text(encoding="utf-8"))

    def write_json(self, filename: str, data: Any) -> Path:
        p = self.path(filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return p

    def speakers(self) -> dict[str, str]:
        """中文名 → cast key，编译剧本时用来认出说话人。"""
        cast = self.read_json("cast.json")
        return {c["name"]: c["key"] for c in cast.get("characters", [])}

    def ensure(self) -> Path:
        self.assets.mkdir(parents=True, exist_ok=True)
        return self.dir


def all_stories() -> list[Story]:
    if not STORIES.is_dir():
        return []
    return [Story(p.name) for p in sorted(STORIES.iterdir()) if (p / "meta.json").exists()]
