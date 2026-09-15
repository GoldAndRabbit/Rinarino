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


# 路径带 .json 后缀不是洁癖：整站要能静态化（scripts/build_site.py 把这两个接口
# 落成同名文件发到 CF Pages）。「/api/stories」既当文件又当目录是做不到的，
# 所以索引和单部各占一条不冲突的路径，dev 和线上共用同一套 URL。
@app.get("/api/stories.json")
def api_stories() -> list[dict[str, object]]:
    return stories.list_stories()


@app.get("/api/story/{name}.json")
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
    # no-cache 不是「不缓存」，是「每次回来问一句」：浏览器带着 ETag 来，
    # 没变就 304（几十字节），变了才重传。素材 URL 里不带 hash，改了图文件名不变,
    # 所以给 max-age 就等于让本地开发时看到的是一小时前的素材——改完图刷新也不变，
    # 只有硬刷新才管用。线上不靠这个头：Pages 自己按内容哈希管缓存。
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


class FrontendFiles(StaticFiles):
    """前端静态文件同样要 no-cache，理由和素材一样。

    不带这个头时浏览器按 last-modified 自己估一个新鲜期：HTML 是导航请求会重新拿，
    CSS / JS 却直接吃本地旧缓存——新页面配旧脚本，旧 app.js 找不到已经删掉的元素，
    整页半死不活，普通刷新也救不回来。"""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if FRONTEND.is_dir():
    app.mount("/", FrontendFiles(directory=FRONTEND, html=True), name="frontend")
