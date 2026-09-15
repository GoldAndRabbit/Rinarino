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

Batch（半价，异步）：同一批图一次交上去，按标准价的 50% 计费，官方目标 24 小时内出结果。
    submit_batch / wait_batch / batch_results；请求走 generateContent（不是 interactions）

CLI:
    uv run python -m util.gg_gemini_api --prompt "..." --kind sprite -o out.png
    # 拿参考图批量画几张表情：先只提交，再用 --job 取回
    uv run python -m util.gg_gemini_api --batch --no-wait --ref normal.jpg \\
        --from-prompts americano sprite_zhao_cold --from-prompts americano sprite_zhao_sad
    uv run python -m util.gg_gemini_api --job batches/xxx --cutout --out-dir gemini_batch
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


# 带参考图时补一句。Seedream 的参考图参数本身就意味着「照着这个人画」，多模态模型不会这么默认——
# 不说清楚，它可能只借参考图的构图，画出另一个人来。
REF_NOTE = (
    "参考图是这个角色的标准立绘：保持同一个人、同一套服装和同一种画风，"
    "只按下面的描述改变表情和动作。\n\n"
)


def _with_ref_note(prompt: str, refs: list[bytes | str] | None) -> str:
    return REF_NOTE + prompt if refs else prompt


_CLIENT: Any = None


def _client() -> Any:
    """整个进程共用一个 client。

    每次现建的话，`_client().batches.get(...)` 这种写法里临时 client 没人引用，
    会被回收，析构时顺手关掉 httpx 连接——请求还没发出去就报
    「client has been closed」。缓存起来就没有这个坑了，也省得每次重新握手。"""
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        from google.genai import types

        key = api_key()
        if not key:
            raise RuntimeError(f"没有 {credential_hint()}")
        timeout_ms = int(float(gemini_config()["timeout"]) * 1000)
        _CLIENT = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
    return _CLIENT


def _generate_sync(
    prompt: str,
    *,
    kind: Kind,
    ref_images: list[bytes | str] | None,
    aspect_ratio: str | None,
    image_size: str | None,
    model: str | None,
) -> tuple[bytes, dict[str, Any]]:
    cfg = gemini_config()
    client = _client()
    ratio = aspect_ratio or cfg["aspect_ratios"].get(kind, DEFAULTS["aspect_ratios"]["bg"])
    size = image_size or cfg["image_size"]
    started = time.perf_counter()
    interaction = client.interactions.create(
        model=model or cfg["image_model"],
        input=[
            {"type": "text", "text": _with_ref_note(prompt, ref_images)},
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


# --- Batch：半价，异步 ------------------------------------------------------------
#
# 同一批图一次交上去，按标准价的 50% 计费，代价是不实时：官方目标 24 小时内出结果，
# 多数情况快得多。适合「这一轮要重画几十张」，不适合「现在就要看一张」。
# 请求走 generateContent 那套（比例 / 档位在 image_config，参考图在 inline_data），
# 每条请求的 metadata 里带着 key，结果按 key 对回去，不靠返回顺序。

BATCH_DONE = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_PARTIALLY_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def batch_request(
    key: str,
    prompt: str,
    *,
    kind: Kind = "bg",
    ref_images: list[bytes | str] | None = None,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
) -> dict[str, Any]:
    cfg = gemini_config()
    parts: list[dict[str, Any]] = [{"text": _with_ref_note(prompt, ref_images)}]
    for ref in ref_images or []:
        data = _ref_bytes(ref)
        parts.append({"inline_data": {"mime_type": _image_part(data)["mime_type"], "data": data}})
    ratio = aspect_ratio or cfg["aspect_ratios"].get(kind, DEFAULTS["aspect_ratios"]["bg"])
    return {
        "contents": [{"role": "user", "parts": parts}],
        "metadata": {"key": key},
        "config": {
            "response_modalities": ["IMAGE"],
            "image_config": {"aspect_ratio": ratio, "image_size": image_size or cfg["image_size"]},
        },
    }


def submit_batch(
    requests: list[dict[str, Any]], *, display_name: str, model: str | None = None
) -> str:
    """提交一批请求，返回任务名（batches/…）。任务名要记下来，取结果全靠它。"""
    client = _client()
    job = client.batches.create(
        model=model or gemini_config()["image_model"],
        src=requests,
        config={"display_name": display_name},
    )
    return job.name


def wait_batch(
    name: str, *, poll: float = 30.0, timeout: float = 48 * 3600, on_poll: Any = None
) -> Any:
    """一直等到任务结束（成功、部分成功、失败、取消、过期都算结束）。"""
    import httpx
    from google.genai import errors

    client = _client()
    started = time.monotonic()
    while True:
        # 一等可能是几个小时，中途断一次网、Google 回一次 5xx 很正常。
        # 查状态不花钱也不改任何东西，下一轮再问就是，不能让整个等待进程因此退出。
        try:
            job = client.batches.get(name=name)
        except (httpx.TransportError, errors.ServerError) as exc:
            print(f"[batch] 查状态失败，{poll:.0f}s 后再试：{type(exc).__name__}", flush=True)
            time.sleep(poll)
            continue
        if on_poll:
            on_poll(job)
        if job.state and job.state.name in BATCH_DONE:
            return job
        if time.monotonic() - started > timeout:
            raise TimeoutError(f"batch 任务 {name} 等了 {timeout:.0f}s 还没结束")
        time.sleep(poll)


def batch_results(job: Any) -> dict[str, bytes | str]:
    """key → PNG；没出图的那条给一句原因（字符串）。"""
    out: dict[str, bytes | str] = {}
    responses = (job.dest.inlined_responses if job.dest else None) or []
    for i, item in enumerate(responses):
        key = (item.metadata or {}).get("key") or f"#{i}"
        if item.error:
            out[key] = f"出错：{item.error}"
            continue
        candidates = (item.response.candidates if item.response else None) or []
        image = next(
            (
                part.inline_data.data
                for cand in candidates
                for part in ((cand.content.parts if cand.content else None) or [])
                if part.inline_data and part.inline_data.data
            ),
            None,
        )
        if image:
            out[key] = to_png(image)
        else:
            reason = candidates[0].finish_reason if candidates else "空响应"
            out[key] = f"没出图：{reason}"
    return out


def recorded_prompt(story: str, asset: str) -> tuple[str, Kind]:
    """prompts.json 里这张图当初真正用过的 prompt 和种类。

    拿它重画，才是同一段 prompt 的公平对比。
    """
    path = ROOT / "vn" / "stories" / story / "assets" / "prompts.json"
    record = json.loads(path.read_text(encoding="utf-8")).get(asset)
    if not record:
        raise SystemExit(f"{path} 里没有 {asset}")
    return record["prompt"], record.get("kind", "bg")


def _save(out: Path, png: bytes, *, cutout_too: bool) -> str:
    out.write_bytes(png)
    with Image.open(io.BytesIO(png)) as im:
        note = f"{im.width}x{im.height}"
    if cutout_too:
        from .image_api import cutout, green_residue

        cut = cutout(png)
        out.with_name(f"{out.stem}_cut.png").write_bytes(cut)
        note += f" · 去背后绿残留 {green_residue(cut, png):.2%}"
    return note


def _fetch_job(name: str, out_dir: Path, *, cutout_too: bool, poll: float) -> int:
    started = time.monotonic()

    def report(job: Any) -> None:
        print(f"[batch] {job.state.name}  {time.monotonic() - started:.0f}s", flush=True)

    job = wait_batch(name, poll=poll, on_poll=report)
    if job.state.name not in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"):
        print(f"[batch] ✗ {job.state.name}：{job.error}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for key, result in batch_results(job).items():
        if isinstance(result, str):
            failed += 1
            print(f"  ✗ {key}  {result}")
            continue
        out = out_dir / f"{key}.png"
        print(f"  ✓ {out}  {_save(out, result, cutout_too=cutout_too)}")
    return 1 if failed else 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="util.gg_gemini_api", description="Gemini 生图：单张实时 / batch 半价"
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt")
    src.add_argument(
        "--from-prompts",
        action="append",
        nargs=2,
        metavar=("STORY", "ASSET"),
        help="拿 prompts.json 里这张图当初用过的 prompt（和 Seedream 对比用）；--batch 时可重复",
    )
    src.add_argument("--job", help="取回一个已提交的 batch 任务（没跑完会一直等）")
    ap.add_argument(
        "--batch", action="store_true", help="把 --from-prompts 列的图作为一个 batch 提交"
    )
    ap.add_argument("--no-wait", action="store_true", help="--batch：只提交，打出任务名就退出")
    ap.add_argument("--poll", type=float, default=30.0, help="batch 轮询间隔（秒）")
    ap.add_argument("--kind", choices=["bg", "cg", "est", "sprite"], default=None)
    ap.add_argument("--ref", action="append", default=[], help="参考图：本地路径，可重复")
    ap.add_argument("--aspect-ratio", help="覆盖配置，如 9:16")
    ap.add_argument("--image-size", help="覆盖配置：1K / 2K / 4K")
    ap.add_argument("--model")
    ap.add_argument(
        "--cutout", action="store_true", help="立绘：再跑一遍和 Seedream 同一套绿幕去背"
    )
    ap.add_argument("-o", "--out", default=str(ROOT / "gemini.png"))
    ap.add_argument("--out-dir", default=str(ROOT / "gemini_batch"), help="batch 的结果放哪")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if args.job:
        return _fetch_job(args.job, Path(args.out_dir), cutout_too=args.cutout, poll=args.poll)

    if args.batch:
        if not args.from_prompts:
            ap.error("--batch 要配 --from-prompts")
        requests = []
        for story, asset in args.from_prompts:
            prompt, kind = recorded_prompt(story, asset)
            requests.append(
                batch_request(
                    asset,
                    prompt,
                    kind=args.kind or kind,
                    ref_images=args.ref or None,
                    aspect_ratio=args.aspect_ratio,
                    image_size=args.image_size,
                )
            )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = submit_batch(requests, display_name=f"rinarino-{stamp}", model=args.model)
        print(f"[batch] 已提交 {len(requests)} 张：{name}", flush=True)
        if args.no_wait:
            again = f"--job {name} --out-dir {args.out_dir}{' --cutout' if args.cutout else ''}"
            print(f"  取结果：uv run python -m util.gg_gemini_api {again}")
            return 0
        return _fetch_job(name, Path(args.out_dir), cutout_too=args.cutout, poll=args.poll)

    if args.from_prompts and len(args.from_prompts) > 1:
        ap.error("不加 --batch 一次只画一张")
    if args.from_prompts:
        prompt, kind = recorded_prompt(*args.from_prompts[0])
    else:
        prompt, kind = args.prompt, "bg"
    png, meta = asyncio.run(
        generate(
            prompt,
            kind=args.kind or kind,
            ref_images=args.ref or None,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            model=args.model,
        )
    )
    out = Path(args.out)
    note = _save(out, png, cutout_too=args.cutout)
    print(
        f"✓ {out}  {note}  {meta['model']} · {meta['aspect_ratio']} · {meta['image_size']} · "
        f"图片 token {meta['image_tokens']} · {meta['seconds']}s"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (RuntimeError, GeminiBlockedError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
