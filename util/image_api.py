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


def _bg_walk(
    im: Image.Image, tolerance: int, halo: int, *, smooth: int = 6, flat_max: int = 3
) -> Image.Image:
    """从四边往里走，吃掉背景，返回硬蒙版（255 前景 / 0 背景）。

    三档标准，都只从画面边缘连通地走，所以人物身上的白衬衫（不挨着边）动不了：
      1. 近白：随便走多远——模型给的底色本来就是纯白
      2. 平滑无纹理的浅色：也随便走多远。写实取向下模型爱画一片影棚地面加接触
         阴影，那片东西从 243 一路渐变到 156，第 1 档够不着、第 3 档走不完。
         但它的两个特征很好认：相邻像素**差不了几级**（渐变），而且局部**没有结构**
         （极差近 0）。鞋带、缝线、鞋底边这些都是高极差，走到那儿就停——
         白球鞋因此能囫囵留下来，而地面被一路吃穿。
      3. 浅色低饱和：只准再走 halo 像素——模型很爱在白底上刷一圈米色的
         柔光 / 投影，那圈东西不近白，前两档都不收，抠完就挂着一条浅色轮廓边。
         限步数是因为夏栀有一双白球鞋：不限的话会从鞋边一路啃进鞋里。
    """
    from PIL import ImageFilter

    w, h = im.size
    px = im.load()
    assert px is not None

    white = 255 - tolerance

    # 局部极差图：一次算完，循环里只查表
    grey = im.convert("L")
    hi_px = grey.filter(ImageFilter.MaxFilter(3)).load()
    lo_px = grey.filter(ImageFilter.MinFilter(3)).load()
    assert hi_px is not None and lo_px is not None

    def near_white(x: int, y: int) -> bool:
        r, g, b, _ = px[x, y]
        return r >= white and g >= white and b >= white

    def pale(x: int, y: int) -> bool:
        r, g, b, _ = px[x, y]
        lo, hi = min(r, g, b), max(r, g, b)
        return lo >= 168 and hi - lo <= 42

    def lum(x: int, y: int) -> int:
        r, g, b, _ = px[x, y]
        return (r * 2 + g * 5 + b) // 8

    def gradient(x: int, y: int) -> bool:
        """浅到能当背景、平到没有结构——地面和投影长这样，鞋子不长这样。"""
        r, g, b, _ = px[x, y]
        lo, hi = min(r, g, b), max(r, g, b)
        return lo >= 140 and hi - lo <= 42 and hi_px[x, y] - lo_px[x, y] <= flat_max

    # budget: 还能在「浅色低饱和」里走几步；近白像素随时把它充满
    budget = bytearray(w * h)
    seen = bytearray(w * h)
    queue: deque[tuple[int, int, int]] = deque()

    def seed(x: int, y: int) -> None:
        i = y * w + x
        if seen[i]:
            return
        if near_white(x, y):
            seen[i] = 1
            budget[i] = halo
            queue.append((x, y, halo))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    mask = Image.new("L", (w, h), 255)
    mpx = mask.load()
    assert mpx is not None
    while queue:
        x, y, left = queue.popleft()
        mpx[x, y] = 0
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            i = ny * w + nx
            if near_white(nx, ny):
                nleft = halo
            elif gradient(nx, ny) and abs(lum(nx, ny) - lum(x, y)) <= smooth:
                nleft = left  # 顺着渐变走不扣预算：地面有多宽就吃多宽
            elif halo and pale(nx, ny) and left > 0:
                nleft = left - 1
            else:
                continue
            # 走过一次还不够：从别的方向来可能剩的步数更多，够就再走一遍
            if seen[i] and budget[i] >= nleft:
                continue
            seen[i] = 1
            budget[i] = nleft
            queue.append((nx, ny, nleft))
    return mask


def _fill_white_holes(im: Image.Image, mask: Image.Image, *, max_area: float = 0.0025) -> None:
    """把**发丝之间围出来的白洞**也抠掉（原地改 mask）。

    从四边走进不去的白：一缕一缕的碎发之间夹着的那些白块。不抠掉的话贴到深色
    背景上，人物头上就顶着一团一团的白斑——比边缘毛刺显眼得多。
    但「围起来的白」也可能是奶白 T 恤、白衬衫，所以卡两道：
      1. 只认**很白**（比外面那档严得多），米白奶白都不算
      2. 只认**小块**（默认画面的 0.25%），衣服那种大片白一律留着
    """
    w, h = im.size
    px = im.load()
    mpx = mask.load()
    assert px is not None and mpx is not None
    limit = int(w * h * max_area)

    def very_white(x: int, y: int) -> bool:
        r, g, b = px[x, y][:3]
        return min(r, g, b) >= 242 and max(r, g, b) - min(r, g, b) <= 10

    seen = bytearray(w * h)
    for y0 in range(h):
        row = y0 * w
        for x0 in range(w):
            if seen[row + x0] or mpx[x0, y0] == 0 or not very_white(x0, y0):
                continue
            seen[row + x0] = 1
            blob = [(x0, y0)]
            queue: deque[tuple[int, int]] = deque(blob)
            while queue:
                x, y = queue.popleft()
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if not (0 <= nx < w and 0 <= ny < h):
                        continue
                    i = ny * w + nx
                    if seen[i] or mpx[nx, ny] == 0 or not very_white(nx, ny):
                        continue
                    seen[i] = 1
                    queue.append((nx, ny))
                    blob.append((nx, ny))
            # 整块走完再判：中途退出的话这块剩下的部分下一轮会被当成新的一块，
            # 一件白衬衫就会被一口一口啃掉
            if len(blob) <= limit:
                for x, y in blob:
                    mpx[x, y] = 0


def _soft_edge(im: Image.Image, hard: Image.Image, *, band: int = 5) -> Image.Image:
    """把硬边界内侧一条带子里的**残留白边**改成半透明。

    发丝、睫毛这种细节在白底上是半透明地过渡过去的，一刀切的蒙版会把过渡段
    整片留成不透明的白，贴到深色背景上就是一圈毛边。所以在边界带里按「有多白」
    重新定 alpha：越接近纯白越透明。只在带子里做，人物内部的浅色衣服不受影响。
    """
    from PIL import ImageChops, ImageFilter

    r, g, b = im.convert("RGB").split()
    lo = ImageChops.darker(ImageChops.darker(r, g), b)
    # lo >= 250 → 全透明；lo <= 198 → 全不透明；中间线性过渡
    soft = lo.point(lambda v: 255 if v <= 198 else (0 if v >= 250 else (250 - v) * 255 // 52))
    inner = hard.filter(ImageFilter.MinFilter(2 * band + 1))
    edge = ImageChops.subtract(hard, inner)
    # 带子外面不设限（取 255），带子里面取 soft
    relaxed = ImageChops.lighter(soft, ImageChops.invert(edge))
    return ImageChops.darker(hard, relaxed)


def cutout(png: bytes, *, tolerance: int = 26, feather: bool = True, halo: int = 12) -> bytes:
    """立绘去背：从四边 flood fill 掉与画面边缘连通的背景，再修一遍边。

    只做**边缘连通域**是关键：人物身上的白衬衫不挨着边，不会被一起抠掉。
    试过改成「和邻居比色差」的区域生长（想顺带吃掉模型自作主张画的彩色地面），
    结果它会顺着柔和的轮廓边缘一路啃进人物内部——前景占比从 43% 涨到 93%。
    所以那条路走不通；现在吃**白色影棚地面**靠的是 _bg_walk 的第 2 档
    （平滑 + 无纹理，见那儿的说明），它只沿着没有结构的渐变走，碰到鞋带缝线就停。
    真·彩色地面还是在 prompt 那头解（见 s3_gen_art 的背景要求）。
    """
    from PIL import ImageFilter

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    hard = _bg_walk(im, tolerance, halo)
    _fill_white_holes(im, hard)
    mask = _soft_edge(im, hard)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    im.putalpha(mask)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def recut(png: bytes, **kw: Any) -> bytes:
    """对**已经抠过**的图重抠一遍：先把透明的地方填回纯白，再走一次 cutout。

    改了去背算法之后不用重新花钱生图——原图的背景本来就是白的，
    填回白色就等价于拿到了模型当初给的那张。
    """
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
    flat.alpha_composite(im)
    buf = io.BytesIO()
    flat.convert("RGB").save(buf, "PNG")
    return cutout(buf.getvalue(), **kw)


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
