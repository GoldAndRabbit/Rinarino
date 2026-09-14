"""打包探索解谜的单文件页：vn/dist/<名字>.html + vn/dist/assets/<名字>/。

和 vn_workflow 第 6 段是同一个页面壳、同一套素材编码（直接复用 s6_build），
只是内联进去的是探索引擎和它的播放器——两种玩法的播放端各是各的，壳和素材管线是一份。

    uv run python -m vn_workflow_v2.build --name stanley [--inline]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from util.paths import DIST, ROOT, Story
from vn_workflow.s6_build import PAGE, PLAY_CSS, SHELL_CSS, inline_js, pack_assets

EXPLORE = ROOT / "frontend" / "explore"

# 探索玩法的屏幕自己管点击（台词、行动、背包各有各的意思），这里只接按钮和键盘
RUNTIME = """
const $ = (s) => document.querySelector(s);
const engine = new ExploreEngine(DATA.story);
const player = new ExplorePlayer(document, $('#screen'));
player.bind(DATA);
const draw = () => player.draw(engine);
player.onChange = draw;
$('#btn-next').onclick = () => { engine.next(); draw(); };
$('#btn-prev').onclick = () => { engine.prev(); draw(); };
$('#btn-restart').onclick = () => { engine.reset(); draw(); };
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input')) return;
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); engine.next(); draw(); }
  if (e.key === 'ArrowLeft') { engine.prev(); draw(); }
});
draw();
"""


def build(story: Story, *, inline: bool = False, verbose: bool = True) -> Path:
    compiled = story.read_json("story.json")
    if compiled.get("engine") != "explore":
        raise SystemExit(
            f"[v2 build] {story.name} 不是探索解谜剧本（story.json 里没写 engine: explore）"
        )
    meta = story.read_json("meta.json")
    DIST.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "story": compiled,
        "meta": meta,
        "cast": story.read_json("cast.json"),
        "assets": pack_assets(story, dist=DIST, inline=inline),
    }
    # 探索玩法的样式叠在剧情那份后面：颜色、字体这些 token 沿用同一份
    css = "\n".join(p.read_text(encoding="utf-8") for p in (PLAY_CSS, EXPLORE / "play.css"))
    html = PAGE.format(
        title=meta.get("title") or story.name,
        subtitle=meta.get("subtitle", ""),
        play_css=css,
        shell_css=SHELL_CSS,
        engine=inline_js(EXPLORE / "engine.js"),
        player=inline_js(EXPLORE / "player.js"),
        data=json.dumps(payload, ensure_ascii=False),
        runtime=RUNTIME,
    )
    out = DIST / f"{story.name}.html"
    out.write_text(html, encoding="utf-8")
    if verbose:
        size = out.stat().st_size / 1024
        how = "单文件内联" if inline else f"外挂 {len(payload['assets'])} 个素材"
        print(f"[v2 build] ✓ {out}  ({size:.0f}KB，{how})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vn_workflow_v2.build")
    ap.add_argument("--name", required=True)
    ap.add_argument("--inline", action="store_true", help="素材塞进 HTML，产出单文件")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    build(Story(args.name), inline=args.inline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
