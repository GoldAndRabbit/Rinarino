"""生图 transport 层（参考 pitchasso 的 seedream 调用）：火山引擎方舟 Seedream。

    POST {base_url}/images/generations
    请求: model / prompt / size / response_format(url|b64_json) / watermark / seed
         / image(参考图 url 或 data URI，4.0+ 支持，立绘同人一致性靠它)
    响应: {"data":[{"url": ...}], "usage":{...}}  —— url 是临时地址，拿到就下载。

鉴权 env ARK_API_KEY，配置 config/llm_api.yaml 的 ark 段。

公开接口:
  - has_credentials() -> bool
  - generate_png(prompt, *, kind, seed, ref_images) -> bytes
  - cutout(png) -> bytes            立绘去背：从四边 flood fill 掉近白底，转 RGBA
  - to_data_uri(png) -> str         把本地参考图喂回 image 参数

CLI: uv run python -m vn_workflow.image_api --prompt "..." --kind bg -o out.png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any, Literal

import httpx
from PIL import Image

from .llm_api import ROOT, load_dotenv, load_yaml

logger = logging.getLogger(__name__)

Kind = Literal["bg", "cg", "est", "sprite"]


class SensitiveContentError(RuntimeError):
    """提示词被内容安全拦下。

    实测这个判定是**会误判的**：同一段提示词拦一次、再发一次就过了。所以它按
    可重试处理（和网络抖动同一条路径），只是重试完还不过的时候要单独报出来——
    那就真是措辞问题，再重试多少次都一样。
    """


ENV_KEY = "ARK_API_KEY"
# 同一把 key 在不同仓里叫过不同名字，都认
ENV_KEY_ALIASES = ("ARK_API_KEY", "ARK_VOLCENGINE_API_KEY", "VOLCENGINE_API_KEY")
DEFAULTS: dict[str, Any] = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "image_model": "doubao-seedream-5-0-pro-260628",
    "watermark": False,
    "timeout": 180.0,
    # 方舟限制：3,686,400 ≤ 面积 ≤ 4,624,220
    "sizes": {
        "bg": "1920x2400",
        "cg": "1920x2400",
        "est": "2864x1608",
        "sprite": "1608x2856",
    },
}

TRANSIENT_EXC: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


def ark_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    raw = load_yaml().get("ark")
    if isinstance(raw, dict):
        sizes = {**DEFAULTS["sizes"], **(raw.get("sizes") or {})}
        cfg.update({k: v for k, v in raw.items() if k != "sizes"})
        cfg["sizes"] = sizes
    return cfg


def api_key() -> str:
    load_dotenv()
    for name in ENV_KEY_ALIASES:
        if value := os.environ.get(name, "").strip():
            return value
    return ""


def has_credentials() -> bool:
    return bool(api_key())


def credential_hint() -> str:
    names = " / ".join(ENV_KEY_ALIASES)
    return f"{names}（model={ark_config()['image_model']}）"


def size_for(kind: Kind) -> str:
    return ark_config()["sizes"].get(kind, DEFAULTS["sizes"]["bg"])


def to_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def generate_png(
    prompt: str,
    *,
    kind: Kind = "bg",
    seed: int | None = None,
    ref_images: list[str] | None = None,
    model: str | None = None,
    size: str | None = None,
    max_attempts: int = 4,
) -> bytes:
    """出一张图，返回 PNG bytes。ref_images 传 http(s) url 或 data URI。"""
    cfg = ark_config()
    key = api_key()
    if not key:
        raise RuntimeError(f"{ENV_KEY} is not set")
    payload: dict[str, Any] = {
        "model": model or cfg["image_model"],
        "prompt": prompt,
        "size": size or size_for(kind),
        "response_format": "url",
        "watermark": bool(cfg["watermark"]),
    }
    if seed is not None:
        payload["seed"] = int(seed) % 2_147_483_647
    if ref_images:
        # 单图用 str、多图用 list，方舟两种都收
        payload["image"] = ref_images[0] if len(ref_images) == 1 else list(ref_images)

    url = f"{str(cfg['base_url']).rstrip('/')}/images/generations"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    timeout = float(cfg["timeout"])
    last = ""
    for attempt in range(max(1, max_attempts)):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    body = resp.text
                    # 内容安全拦截是**改 prompt 才能解决**的，重试多少次都一样，
                    # 单独认出来并说清楚，免得在重试循环里白白耗掉几分钟。
                    if "SensitiveContent" in body:
                        raise SensitiveContentError(f"内容安全拦截：{body[:200]}")
                    raise RuntimeError(f"Seedream HTTP {resp.status_code}: {body[:300]}")
                items = resp.json().get("data") or []
                if not items:
                    raise RuntimeError(f"Seedream 响应无图: {resp.text[:300]}")
                first = items[0]
                if b64 := first.get("b64_json"):
                    return base64.b64decode(b64)
                got = await client.get(first["url"])
                got.raise_for_status()
                return got.content
        except (*TRANSIENT_EXC, SensitiveContentError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt >= max_attempts - 1:
                raise
            wait = (attempt + 1) * 2.0
            logger.warning("生图失败（%s），%.1fs 后第 %s 次重试", last, wait, attempt + 2)
            await asyncio.sleep(wait)
    raise RuntimeError(f"Seedream 重试耗尽: {last}")


# 色度键背景。立绘一律在这个绿幕前拍，抠图按**色距**判，不看亮度。
#
# 为什么不再用白底 flood fill：白底下「背景」和「白衣服」在像素上是同一种东西，
# 分不开。试过的判据全部失败——四周亮度（陷落的背景 145–198 vs 布料高光 223–237）、
# 局部纹理（2.7–3.4 vs 3.8–8.0）、有多白（246 vs 252，还是反的）——三组区间全都重叠。
# 白衬衫会被打成筛子，白球鞋会被啃穿，米色西装夹住的背景抠不掉。
# 换成衣服上不可能出现的饱和绿之后，判据退化成一个减法，没有任何启发式。
CHROMA = (0, 177, 64)  # #00B140，标准色度键绿
# 阈值按实测的分布定，不是拍脑袋：一张典型立绘里，背景的「绿超出量」（绿减去红蓝的
# 较大者）扎堆在 90–96，人物身上基本 ≤19，中间的 20–79 只占 0.5%——那是抗锯齿过渡带。
# 所以砍在 70、保在 30，两边都留了三十多的余量。
# 早先设成 40/12 的后果：模型给球鞋画了点薄荷配色，整块被当成绿幕抠掉，鞋上一个洞。
# 75 是量出来的分界：被手臂和头围住、连不到画面边缘的那块背景是 81，
# 而球鞋上那道薄荷配色是 56–69。卡在中间，两边各留六七的余量。
CHROMA_PURE = 75  # 超出这么多 → 一定是背景，不管连不连得到外面
CHROMA_BG = 60  # 连到画面边缘、又有这么绿 → 也是背景，整个抠掉
CHROMA_SOFT = 30  # 超出不到这么多 → 一定是前景；30~60 且连到外面的按比例给半透明


def cutout(png: bytes, *, feather: bool = True) -> bytes:
    """立绘去背：绿幕色度键 + 去溢色。

    判据用两个信号，缺一不可——只看颜色会把衣服上的绿配色一起抠掉，
    只看连通性会留下腋下和两腿之间夹着的背景：

      1. **纯背景色**（绿超出量 ≥ CHROMA_PURE）：全局抠。夹在身体之间的背景
         连不到画面边缘，只能靠颜色认出来。
      2. **连到画面边缘、又够绿的**（≥ CHROMA_BG）：整个抠掉，不按比例。
         影棚地面附近的绿幕在阴影里只有 71–81，够不着第 1 档；要是按比例给半透明，
         画面底下就留一条 alpha 30 的灰带子，闸门还会把它当成「人被裁断」。
      3. **发丝那一段**（CHROMA_SOFT ~ CHROMA_BG 且连到外面）：按绿超出量给半透明。
         头发边缘本来就是半透明地过渡过去的，一刀切会留一圈硬边。
         注意这三档都靠连通性兜底：衣服上的淡绿配色（实测球鞋上一道薄荷条纹
         是 50–85）被鞋包着连不出去，所以一根汗毛都不会少。
      4. 去溢色：绿幕会把绿光反到人物边缘，把绿通道压到红蓝的较大者即可——
         对本来就不发绿的像素是恒等变换，所以整张无脑做。
    """
    from PIL import ImageFilter

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = im.size
    px = im.load()
    assert px is not None

    excess = [[0] * w for _ in range(h)]
    for y in range(h):
        row = excess[y]
        for x in range(w):
            r, g, b, _ = px[x, y]
            hi = max(r, b)
            row[x] = g - hi
            if g > hi:
                px[x, y] = (r, hi, b, 255)  # 去溢色

    # 从四边沿着「够绿」的像素走，标出哪些中间带像素是连到外面的
    outside = [bytearray(w) for _ in range(h)]
    queue: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int) -> None:
        if excess[y][x] >= CHROMA_SOFT and not outside[y][x]:
            outside[y][x] = 1
            queue.append((x, y))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                seed(nx, ny)

    mask = Image.new("L", (w, h), 255)
    mpx = mask.load()
    assert mpx is not None
    span = CHROMA_BG - CHROMA_SOFT
    for y in range(h):
        row = excess[y]
        out_row = outside[y]
        for x in range(w):
            e = row[x]
            if e >= CHROMA_PURE or (out_row[x] and e >= CHROMA_BG):
                mpx[x, y] = 0
            elif e > CHROMA_SOFT and out_row[x]:
                mpx[x, y] = 255 * (CHROMA_BG - e) // span

    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    im.putalpha(mask)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def green_residue(cut: bytes, raw: bytes | None = None) -> float:
    """留下来的像素里还有多少其实是绿幕。抠干净了应该接近 0。

    要拿**原图的颜色**来判：去溢色会把残留背景的绿通道一起压掉，只看成品的话
    一片没抠干净的背景会被洗成灰的，指标读到 0——等于自己把探测器蒙上了。
    没有原图就退回看成品，聊胜于无。
    """
    im = Image.open(io.BytesIO(cut)).convert("RGBA").resize((160, 160))
    src = im if raw is None else Image.open(io.BytesIO(raw)).convert("RGB").resize((160, 160))
    alpha = im.getchannel("A")
    # 只看**实心留下**的像素（alpha > 200）和**明显是背景色**的（超出 > 40）。
    # 半透明的边和头发边缘压在绿幕上本来就带绿，算进来会把噪声抬到百分之几。
    kept = [(x, y) for y in range(160) for x in range(160) if alpha.getpixel((x, y)) > 200]
    if not kept:
        return 0.0
    hits = 0
    for x, y in kept:
        r, g, b = src.getpixel((x, y))[:3]
        if g - max(r, b) > 40:
            hits += 1
    return hits / len(kept)


def recut(png: bytes, **kw: Any) -> bytes:
    """重抠一遍。要拿**模型原样给的那张**（带绿幕的），不能拿抠过的成品——
    绿幕一旦抠掉就找不回来了，所以 s3 会把原图留在 assets/.raw/ 下（见 s3_gen_art）。
    改了去背算法之后从那儿重跑，不用重新花钱生图。
    """
    return cutout(png, **kw)


def foreground_ratio(png: bytes) -> float:
    """不透明像素占比。立绘正常在 0.25–0.35 左右；去背失败或模型画糊了会明显偏高。"""
    im = Image.open(io.BytesIO(png)).convert("RGBA").resize((96, 96))
    px = [im.getpixel((x, y)) for y in range(96) for x in range(96)]
    return sum(1 for v in px if v[3] > 96) / len(px)


def _main() -> None:
    ap = argparse.ArgumentParser(prog="util.image_api")
    # 改了去背算法之后就地重抠，不用重新花钱生图（原图背景本来就是白的）
    ap.add_argument("--recut", nargs="+", default=[], metavar="PNG", help="就地重抠这些立绘")
    ap.add_argument("--prompt")
    ap.add_argument("--kind", default="bg", choices=["bg", "cg", "sprite"])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--ref", action="append", default=[], help="参考图：http url 或本地路径")
    ap.add_argument("--no-cutout", action="store_true", help="立绘不去背")
    ap.add_argument("-o", "--out", default=str(ROOT / "out.png"))
    args = ap.parse_args(sys.argv[1:])

    if args.recut:
        for raw in args.recut:
            p = Path(raw)
            before = foreground_ratio(p.read_bytes())
            png = recut(p.read_bytes())
            p.write_bytes(png)
            print(f"✓ {p.name}  前景 {before:.2f} → {foreground_ratio(png):.2f}")
        return
    if not args.prompt:
        ap.error("要么给 --prompt 生图，要么给 --recut 重抠")

    refs = [r if r.startswith("http") else to_data_uri(Path(r).read_bytes()) for r in args.ref]
    png = asyncio.run(
        generate_png(args.prompt, kind=args.kind, seed=args.seed, ref_images=refs or None)
    )
    if args.kind == "sprite" and not args.no_cutout:
        png = cutout(png)
    Path(args.out).write_bytes(png)
    print(f"✓ {args.out} ({len(png) // 1024}KB)")


if __name__ == "__main__":
    try:
        _main()
    except (RuntimeError, httpx.HTTPError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
