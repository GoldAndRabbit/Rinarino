"""把整站打成纯静态目录，发 Cloudflare Pages 用。

    uv run python scripts/build_site.py          # → site/
    uv run python scripts/build_site.py --out /tmp/x --clean

后端（backend/app/main.py）只做三件事：端 /api/stories.json、端 /api/story/<名>.json、
发 /assets/ 下的文件。三件事读的都是**不会变的产物**，所以完全可以在构建时算好落成文件，
线上就不需要一个常驻进程——没有服务器要守，也没有隧道会掉。

产物布局和后端的 URL 逐字一致，前端一行都不用改：

    site/index.html  css/  js/  engine/     ← 原样拷贝 frontend/
    site/api/stories.json                   ← list_stories()
    site/api/story/<名字>.json               ← load_story(名字)
    site/assets/<名字>/<文件>                 ← vn/stories/<名字>/assets/

JSON 直接调 backend.app.stories，就是后端在跑的那份代码——线上和本地 dev 端出来的
是同一个 payload，不存在「静态版少了个字段」这种事。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import stories  # noqa: E402
from util.paths import all_stories  # noqa: E402
from vn_workflow.s6_build import web_encode  # noqa: E402

FRONTEND = ROOT / "frontend"


def build(out: Path, *, clean: bool = False, verbose: bool = True) -> Path:
    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 前端原样拷过去（没有构建步骤，原生 JS 就是最终产物）
    for item in FRONTEND.iterdir():
        dst = out / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)

    # 2. 两个接口落成文件
    api = out / "api"
    (api / "story").mkdir(parents=True, exist_ok=True)
    index = stories.list_stories()
    (api / "stories.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    for item in index:
        name = item["name"]
        payload = stories.load_story(str(name))
        (api / "story" / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    # 3. 素材。母版是 2000 多像素的 PNG（一张三四 MB），那是存档尺寸不是上网尺寸，
    # 所以走 s6_build 那套 web_encode 压成 WebP——和单文件页用的是同一份编码逻辑。
    # 文件名会从 .png 变成 .webp，所以上一步写好的 payload 里的 URL 得跟着改。
    total = 0
    for story in all_stories():
        if not story.assets.is_dir():
            continue
        dst = out / "assets" / story.name
        dst.mkdir(parents=True, exist_ok=True)
        renamed: dict[str, str] = {}
        for p in sorted(story.assets.iterdir()):
            if p.suffix.lower() == ".json":  # prompts.json 是给人看的，不上线
                continue
            raw, name = web_encode(p)
            (dst / name).write_bytes(raw)
            if name != p.name:
                renamed[p.name] = name
            total += 1
        _rewrite_asset_urls(api / "story" / f"{story.name}.json", renamed)
        _rewrite_asset_urls(api / "stories.json", renamed)

    if verbose:
        size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
        print(f"[site] {len(index)} 部小说，{total} 个素材，{size / 1e6:.0f}MB → {out}")
    return out


def _rewrite_asset_urls(path: Path, renamed: dict[str, str]) -> None:
    """把 payload 里的 /assets/<名字>/x.png 换成压好的 x.webp。

    直接在 JSON 文本上替换，不去解析结构：素材 URL 在 payload 里出现在好几处
    （assets 表、meta.cover、各结点），一处漏了播放端就是一张裂图。
    """
    if not renamed or not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    for old_name, new_name in renamed.items():
        text = text.replace(f"/{old_name}", f"/{new_name}")
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="build_site", description="打成纯静态站")
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--clean", action="store_true", help="先清空目标目录")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    build(Path(args.out), clean=args.clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
