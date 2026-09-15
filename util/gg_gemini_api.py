"""生图 transport 层：Google Gemini 图片模型（google-genai 的 Interactions API）。

和 image_api.py（火山方舟 Seedream）并排，接口对齐，同一段 prompt 可以两边直接对比：

    generate_png(prompt, *, kind, seed, ref_images, model) -> bytes     和 image_api 同签名
    generate(prompt, ...) -> (png, meta)                                 多带一份用量和耗时

和 Seedream 不一样、在这里抹平了的几处：
  - 返回的是 JPEG，统一转成 PNG——立绘去背、prompts.json 记录都按 PNG 走
  - 尺寸不是像素，是「比例 + 档位」（"9:16" + "2K"），按 kind 从配置里取，和播放框对齐
  - 参考图直接放进 input（base64 + mime），本地文件、bytes、data URI 都收
  - SDK 是同步的，包成 async（asyncio.to_thread），好接 s3 的并发排程
  - **没有 seed**：同一段 prompt 每次出图都不一样。seed 参数收下只为和 image_api 同签名

鉴权 env GOOGLE_GEMINI_API_KEY（也认 GEMINI_API_KEY / GOOGLE_API_KEY），
配置在 config/llm_api.yaml 的 gemini 段。

CLI:
    uv run python -m util.gg_gemini_api --prompt "..." --kind sprite -o out.png
    # 拿 prompts.json 里这张图当初真正用过的 prompt 重画一张，和 Seedream 对比
    uv run python -m util.gg_gemini_api --from-prompts americano sprite_zhao_normal \\
        --cutout -o zhao.png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from PIL import Image

from .llm_api import ROOT, load_dotenv, load_yaml

logger = logging.getLogger(__name__)

Kind = Literal["bg", "cg", "est", "sprite"]

ENV_KEY_ALIASES = ("GOOGLE_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")

DEFAULTS: dict[str, Any] = {
    "image_model": "gemini-3.1-flash-image",
    "image_size": "2K",
    "timeout": 180,
    # 比例和播放框对齐，和 ark.sizes 是同一套：背景 / CG 4:5，定场 16:9，立绘 9:16
    "aspect_ratios": {"bg": "4:5", "cg": "4:5", "est": "16:9", "sprite": "9:16"},
}


class GeminiBlockedError(RuntimeError):
    """没出图：被安全策略拦下，或者模型只回了一段文字。异常信息里带着它给的原因。
    这种重试没用，得改 prompt，所以不走重试。"""


def gemini_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    raw = load_yaml().get("gemini")
    if isinstance(raw, dict):
        ratios = {**DEFAULTS["aspect_ratios"], **(raw.get("aspect_ratios") or {})}
        cfg.update({k: v for k, v in raw.items() if k != "aspect_ratios"})
        cfg["aspect_ratios"] = ratios
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
    return f"{' / '.join(ENV_KEY_ALIASES)}（model={gemini_config()['image_model']}）"


def _ref_bytes(ref: bytes | str) -> bytes:
    if isinstance(ref, bytes):
        return ref
    if ref.startswith("data:"):
        return base64.b64decode(ref.split(",", 1)[1])
    if ref.startswith(("http://", "https://")):
        raise ValueError("Gemini 这边的参考图要本地文件、bytes 或 data URI，不收 URL")
    return Path(ref).read_bytes()


def _image_part(data: bytes) -> dict[str, str]:
    with Image.open(io.BytesIO(data)) as im:
        mime = Image.MIME.get(im.format or "PNG", "image/png")
    return {"type": "image", "data": base64.b64encode(data).decode(), "mime_type": mime}


def to_png(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.save(buf, "PNG")
    return buf.getvalue()


def _image_tokens(usage: Any) -> int | None:
    """用量里「输出了多少图片 token」。

    total_output_tokens 只算文字，图片的在按模态拆开的那张表里。
    """
    for item in getattr(usage, "output_tokens_by_modality", None) or []:
        if str(getattr(item, "modality", "")).lower().endswith("image"):
            return getattr(item, "tokens", None)
    return None


def _generate_sync(
    prompt: str,
    *,
    kind: Kind,
    ref_images: list[bytes | str] | None,
    aspect_ratio: str | None,
    image_size: str | None,
    model: str | None,
) -> tuple[bytes, dict[str, Any]]:
    from google import genai
    from google.genai import types

    key = api_key()
    if not key:
        raise RuntimeError(f"没有 {credential_hint()}")
    cfg = gemini_config()
    client = genai.Client(
        api_key=key, http_options=types.HttpOptions(timeout=int(float(cfg["timeout"]) * 1000))
    )
    ratio = aspect_ratio or cfg["aspect_ratios"].get(kind, DEFAULTS["aspect_ratios"]["bg"])
    size = image_size or cfg["image_size"]
    started = time.perf_counter()
    interaction = client.interactions.create(
        model=model or cfg["image_model"],
        input=[
            {"type": "text", "text": prompt},
            *(_image_part(_ref_bytes(r)) for r in ref_images or []),
        ],
        response_format={"type": "image", "aspect_ratio": ratio, "image_size": size},
    )
    image = getattr(interaction, "output_image", None)
    if image is None or not getattr(image, "data", None):
        why = interaction.errors or interaction.output_text or interaction.status
        raise GeminiBlockedError(f"Gemini 没出图：{str(why)[:300]}")
    meta = {
        "model": interaction.model,
        "aspect_ratio": ratio,
        "image_size": size,
        "mime": getattr(image, "mime_type", None),
        "image_tokens": _image_tokens(interaction.usage),
        "seconds": round(time.perf_counter() - started, 1),
    }
    return to_png(base64.b64decode(image.data)), meta


async def generate(
    prompt: str,
    *,
    kind: Kind = "bg",
    ref_images: list[bytes | str] | None = None,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    model: str | None = None,
    max_attempts: int = 3,
) -> tuple[bytes, dict[str, Any]]:
    from google.genai import errors

    last = ""
    for attempt in range(max(1, max_attempts)):
        try:
            return await asyncio.to_thread(
                _generate_sync,
                prompt,
                kind=kind,
                ref_images=ref_images,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                model=model,
            )
        # 服务端 5xx 和网络抖动重试；4xx（key 不对、参数不对）和安全拦截重试多少次都一样
        except (errors.ServerError, httpx.TransportError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt >= max_attempts - 1:
                raise
            wait = (attempt + 1) * 3.0
            logger.warning("Gemini 生图失败（%s），%.0fs 后第 %s 次重试", last, wait, attempt + 2)
            await asyncio.sleep(wait)
    raise RuntimeError(f"Gemini 重试耗尽: {last}")


async def generate_png(
    prompt: str,
    *,
    kind: Kind = "bg",
    seed: int | None = None,  # Gemini 没有 seed，收下只为和 image_api.generate_png 同签名
    ref_images: list[bytes | str] | None = None,
    model: str | None = None,
) -> bytes:
    png, _ = await generate(prompt, kind=kind, ref_images=ref_images, model=model)
    return png


def recorded_prompt(story: str, asset: str) -> tuple[str, Kind]:
    """prompts.json 里这张图当初真正用过的 prompt 和种类。

    拿它重画，才是同一段 prompt 的公平对比。
    """
    path = ROOT / "vn" / "stories" / story / "assets" / "prompts.json"
    record = json.loads(path.read_text(encoding="utf-8")).get(asset)
    if not record:
        raise SystemExit(f"{path} 里没有 {asset}")
    return record["prompt"], record.get("kind", "bg")


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="util.gg_gemini_api", description="Gemini 生图")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt")
    src.add_argument(
        "--from-prompts",
        nargs=2,
        metavar=("STORY", "ASSET"),
        help="拿 prompts.json 里这张图当初用过的 prompt（和 Seedream 对比用）",
    )
    ap.add_argument("--kind", choices=["bg", "cg", "est", "sprite"], default=None)
    ap.add_argument("--ref", action="append", default=[], help="参考图：本地路径，可重复")
    ap.add_argument("--aspect-ratio", help="覆盖配置，如 9:16")
    ap.add_argument("--image-size", help="覆盖配置：1K / 2K / 4K")
    ap.add_argument("--model")
    ap.add_argument(
        "--cutout", action="store_true", help="立绘：再跑一遍和 Seedream 同一套绿幕去背"
    )
    ap.add_argument("-o", "--out", default=str(ROOT / "gemini.png"))
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.from_prompts:
        prompt, kind = recorded_prompt(*args.from_prompts)
    else:
        prompt, kind = args.prompt, "bg"
    kind = args.kind or kind
    png, meta = asyncio.run(
        generate(
            prompt,
            kind=kind,
            ref_images=args.ref or None,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            model=args.model,
        )
    )
    out = Path(args.out)
    out.write_bytes(png)
    with Image.open(io.BytesIO(png)) as im:
        size = f"{im.width}x{im.height}"
    print(
        f"✓ {out}  {size}  {meta['model']} · {meta['aspect_ratio']} · {meta['image_size']} · "
        f"图片 token {meta['image_tokens']} · {meta['seconds']}s"
    )
    if args.cutout:
        from .image_api import cutout, green_residue

        cut = cutout(png)
        cut_path = out.with_name(f"{out.stem}_cut.png")
        cut_path.write_bytes(cut)
        print(f"✓ {cut_path}  去背后绿残留 {green_residue(cut, png):.2%}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (RuntimeError, GeminiBlockedError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
