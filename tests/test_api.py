"""后端：列表、详情、素材、路径穿越。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["ok"] is True


def test_list_stories():
    items = client.get("/api/stories.json").json()
    assert items and all({"name", "title", "nodes", "endings"} <= set(i) for i in items)


def test_story_payload_is_self_contained():
    name = client.get("/api/stories.json").json()[0]["name"]
    data = client.get(f"/api/story/{name}.json").json()
    # 前端一次取完就能播完：剧本、素材索引、设定表、界面文案都在里面
    assert data["story"]["nodes"] and data["assets"] and data["cast"]["characters"]
    assert data["meta"]["title"]
    for node in data["story"]["nodes"]:
        for tag in ("bg", "cg"):
            if ref := (node.get("tags") or {}).get(tag):
                assert ref in data["assets"], f"{node['id']} 引用了不存在的素材 {ref}"


def test_missing_story_is_404():
    assert client.get("/api/story/nope.json").status_code == 404


def test_asset_served():
    name = client.get("/api/stories.json").json()[0]["name"]
    url = next(iter(client.get(f"/api/story/{name}.json").json()["assets"].values()))
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("evil", ["../meta.json", "..%2Fmeta.json", "a/../../meta.json"])
def test_traversal_blocked(evil: str):
    assert client.get(f"/assets/americano/{evil}").status_code in (404, 400)


def test_character_page_renders_structured_expressions():
    """表情是 {表情, 动作} 对象；前端当字符串拼就会渲染出 [object Object]。"""
    from pathlib import Path

    src = Path("frontend/js/cast.js").read_text(encoding="utf-8")
    assert "spec?.表情" in src and "spec?.动作" in src, "角色页要分别取出两段"
    assert "${esc(desc)}" not in src, "别再把整个对象往模板里塞"

    name = client.get("/api/stories.json").json()[0]["name"]
    cast = client.get(f"/api/story/{name}.json").json()["cast"]
    for ch in cast["characters"]:
        for key, spec in ch["expressions"].items():
            assert isinstance(spec, dict), f"{ch['name']}.{key} 该是对象"
            assert spec.get("表情") and spec.get("动作"), f"{ch['name']}.{key} 两段都要有"
