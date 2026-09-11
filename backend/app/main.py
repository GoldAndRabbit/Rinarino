"""Rinarino 后端：把 vn/stories/ 端给前端，外加托管前端静态文件。

三层单向依赖里，这一层是宿主：
    vn_workflow/  生成端：一句设定 → 素材，只往 vn/stories/<名字>/ 写文件
    vn/           产物：素材 + 编译好的 story.json
    backend/      宿主：读产物、发 HTTP；一行都不 import 生成端的生成逻辑
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import stories

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="Rinarino", description="视觉互动小说", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "stories": len(stories.list_stories())}


@app.get("/api/stories")
def api_stories() -> list[dict[str, object]]:
    return stories.list_stories()


@app.get("/api/stories/{name}")
def api_story(name: str) -> dict[str, object]:
    payload = stories.load_story(name)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"没有这部小说：{name}")
    return payload


@app.get("/assets/{name}/{filename}")
def api_asset(name: str, filename: str) -> FileResponse:
    path = stories.resolve_asset(name, filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"没有这个素材：{name}/{filename}")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
