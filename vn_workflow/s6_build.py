"""第 6 段：打包成一个不依赖后端的 HTML。

产物 vn/dist/<名字>.html + vn/dist/assets/<名字>/。双击就能玩，
不需要 FastAPI、不需要联网——播放端本来就只认 story.json 和一个素材目录。
--inline 把素材也塞进 HTML（data URI），换来单文件可分发，代价是体积。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from util.paths import DIST, ROOT, Story

FRONTEND = ROOT / "frontend"
# 播放端只有一份：引擎 + 播放器 + 播放区样式，宿主 WebUI 和这里都用它
ENGINE_DIR = FRONTEND / "engine"
ENGINE = ENGINE_DIR / "engine.js"
PLAYER = ENGINE_DIR / "player.js"
PLAY_CSS = ENGINE_DIR / "play.css"

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{play_css}
{shell_css}</style></head>
<body>
<div class="wrap">
  <div class="kicker">{subtitle}</div>
  <h1>{title}</h1>
  <div class="controls">
    <button class="ctl" id="btn-prev">← 上一步</button>
    <button class="ctl ctl-primary" id="btn-next">下一步 →</button>
    <button class="ctl" id="btn-restart">重开</button>
  </div>
  <div id="screen"></div>
  <div class="vars" id="vars"></div>
</div>
<script type="module">
{engine}
{player}
const DATA = {data};
{runtime}
</script></body></html>
"""

# 只有**壳**的样式写在这儿：页面外框、标题、按钮。
# 屏幕本身（画面 / 立绘 / 台词 / 选项 / 结局 / 变量条）一律用 engine/play.css，
# 和宿主 WebUI 是同一份——播放界面再也不会两边各改一遍。
SHELL_CSS = """
* { box-sizing: border-box }
body{margin:0;background:#f4f4f1;color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,
"PingFang SC","Hiragino Sans GB",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;cursor:pointer}
.wrap{max-width:520px;margin:0 auto;padding:24px 16px 40px}
.kicker{font:11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
h1{font-size:22px;margin:6px 0 12px;font-weight:600}
.controls{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
.ctl{border:1px solid var(--line);background:var(--surface);border-radius:6px;padding:4px 11px;
font-size:12.5px;color:var(--ink-2)}
.ctl:disabled{opacity:.4;cursor:default}
.ctl-primary{border-color:#cddcf4;background:var(--accent-soft);color:var(--accent)}
"""

# 运行时也就剩「接线」了：引擎推状态，播放器画屏幕，按钮和键盘喂输入。
RUNTIME = """
const $ = (s) => document.querySelector(s);
const engine = new Engine(DATA.story);
const player = new Player(document, $('#screen'));
player.bind(DATA);
const draw = () => player.draw(engine);

player.onChoose = (i) => { engine.choose(i); draw(); };
$('#btn-next').onclick = () => { engine.next(); draw(); };
$('#btn-prev').onclick = () => { engine.prev(); draw(); };
$('#btn-restart').onclick = () => { engine.reset(); draw(); };
$('#screen').addEventListener('click', (e) => {
  if (e.target.closest('.choices, .ending')) return;
  if (engine.canNext) { engine.next(); draw(); }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); engine.next(); draw(); }
  if (e.key === 'ArrowLeft') { engine.prev(); draw(); }
});
draw();
"""


# 母版是 2560x1440 的 PNG（一张三四 MB），那是存档用的尺寸，不是上网用的。
# 打包时统一转成 WebP 并压到够用的边长——sprite 保留 alpha。
WEB_MAX = {"sprite": 1200, "default": 1600}
WEB_QUALITY = 82
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def web_encode(path: Path) -> tuple[bytes, str]:
    """→ (WebP bytes, 文件名)。转不了就原样返回，打包不该因为一张图失败。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return path.read_bytes(), path.name
    try:
        with Image.open(path) as im:
            has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
            im = im.convert("RGBA" if has_alpha else "RGB")
            cap = WEB_MAX["sprite" if path.stem.startswith("sprite_") else "default"]
            if max(im.size) > cap:
                im.thumbnail((cap, cap), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=WEB_QUALITY, method=5)
        return buf.getvalue(), f"{path.stem}.webp"
    except OSError:
        return path.read_bytes(), path.name


def data_uri(raw: bytes, name: str) -> str:
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def build(story: Story, *, inline: bool = False, verbose: bool = True) -> Path:
    compiled = story.read_json("story.json")
    if not compiled:
        raise SystemExit(f"[build] 还没有 story.json，先跑第 1 段：{story.name}")
    meta = story.read_json("meta.json")
    DIST.mkdir(parents=True, exist_ok=True)

    assets: dict[str, str] = {}
    sources = sorted(story.assets.iterdir()) if story.assets.is_dir() else []
    out_assets = DIST / "assets" / story.name
    if not inline:
        if out_assets.exists():
            shutil.rmtree(out_assets)
        out_assets.mkdir(parents=True, exist_ok=True)

    for p in sources:
        suffix = p.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            raw, name = web_encode(p)
        elif suffix == ".svg":
            raw, name = p.read_bytes(), p.name
        elif inline:
            # 单文件版只内联页面真正会显示的东西：打包出来的运行时不放音频和视频，
            # 把几 MB 的音轨 base64 进去纯属白背重量。
            continue
        elif suffix in (".wav", ".mp3", ".ogg", ".mp4", ".webm"):
            raw, name = p.read_bytes(), p.name
        else:
            continue
        if inline:
            assets.setdefault(p.stem, data_uri(raw, name))
        else:
            (out_assets / name).write_bytes(raw)
            assets.setdefault(p.stem, f"assets/{story.name}/{name}")

    payload: dict[str, Any] = {
        "story": compiled,
        "meta": meta,
        "cast": story.read_json("cast.json"),
        "assets": assets,
    }
    # frontend/engine/ 是同一份源码，打包只是把它内联进来，不另写一套播放端。
    # 内联进 <script type="module"> 后 export 没有意义，顶层的一律剥掉。
    def inline_js(path: Path) -> str:
        return re.sub(r"^export ", "", path.read_text(encoding="utf-8"), flags=re.M)

    html = PAGE.format(
        title=meta.get("title") or story.name,
        subtitle=meta.get("subtitle", ""),
        play_css=PLAY_CSS.read_text(encoding="utf-8"),
        shell_css=SHELL_CSS,
        engine=inline_js(ENGINE),
        player=inline_js(PLAYER),
        data=json.dumps(payload, ensure_ascii=False),
        runtime=RUNTIME,
    )
    out = DIST / f"{story.name}.html"
    out.write_text(html, encoding="utf-8")
    if verbose:
        size = out.stat().st_size / 1024
        how = "单文件内联" if inline else f"外挂 {len(assets)} 个素材"
        print(f"[build] ✓ {out.relative_to(ROOT)}  ({size:.0f}KB，{how})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s6_build")
    ap.add_argument("--name", required=True)
    ap.add_argument("--inline", action="store_true", help="素材塞进 HTML，产出单文件")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    build(Story(args.name), inline=args.inline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
