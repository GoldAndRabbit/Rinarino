"""视频 transport 层：火山引擎方舟 Seedance（异步任务制）。

    POST {base_url}/contents/generations/tasks     提交 → {"id": "cgt-..."}
    GET  {base_url}/contents/generations/tasks/{id} 轮询 → status / content.video_url

content 是一个多模态数组：一段 text（提示词），外加若干带 role 的参考项——
  image_url + role=reference_image   参考图（首帧 / 尾帧 / 人物一致性）
  video_url + role=reference_video   参考视频（借它的运镜）
  audio_url + role=reference_audio   参考音频（借它的配乐）
顶层参数：generate_audio / ratio / duration / watermark / seed。

鉴权 env ARK_API_KEY（与生图同一把），配置 config/llm_api.yaml 的 seedance 段。

公开接口:
  - submit(prompt, ...) -> task_id
  - poll(task_id) -> dict            单次查询
  - wait(task_id, ...) -> dict       轮询到终态
  - generate_video(prompt, ...) -> bytes   提交 + 等待 + 下载，一步到位

CLI: uv run python -m vn_workflow.video_api --prompt "..." --image a.png -o out.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .image_api import ENV_KEY, api_key, has_credentials
from .llm_api import ROOT, load_yaml

__all__ = [
    "ENV_KEY",
    "has_credentials",
    "seedance_config",
    "submit",
    "poll",
    "wait",
    "generate_video",
]

DEFAULTS: dict[str, Any] = {
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "model": "doubao-seedance-2-5-260628",
    "ratio": "16:9",
    "duration": 5,
    "generate_audio": True,
    "watermark": False,
    "timeout": 120.0,
    "poll_interval": 5.0,
    "poll_timeout": 900.0,
}

TERMINAL_OK = {"succeeded", "success"}
TERMINAL_BAD = {"failed", "cancelled", "canceled"}


def seedance_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    raw = load_yaml().get("seedance")
    if isinstance(raw, dict):
        cfg.update(raw)
    return cfg


def _headers() -> dict[str, str]:
    key = api_key()
    if not key:
        raise RuntimeError(f"{ENV_KEY} is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def as_uri(src: str | Path | bytes, *, fallback_mime: str = "image/png") -> str:
    """http(s) 原样返回；本地路径 / bytes 转成 data URI。"""
    if isinstance(src, bytes):
        return f"data:{fallback_mime};base64," + base64.b64encode(src).decode()
    text = str(src)
    if text.startswith(("http://", "https://", "data:")):
        return text
    path = Path(text)
    mime = mimetypes.guess_type(path.name)[0] or fallback_mime
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def build_content(
    prompt: str,
    *,
    images: list[str | Path | bytes] | None = None,
    video: str | Path | None = None,
    audio: str | Path | None = None,
) -> list[dict[str, Any]]:
    """按 role 拼多模态 content 数组。images 的顺序 = 提示词里「图片1/图片2」的顺序。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in images or []:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": as_uri(img, fallback_mime="image/png")},
                "role": "reference_image",
            }
        )
    if video:
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": as_uri(video, fallback_mime="video/mp4")},
                "role": "reference_video",
            }
        )
    if audio:
        content.append(
            {
                "type": "audio_url",
                "audio_url": {"url": as_uri(audio, fallback_mime="audio/mpeg")},
                "role": "reference_audio",
            }
        )
    return content


async def submit(
    prompt: str,
    *,
    images: list[str | Path | bytes] | None = None,
    video: str | Path | None = None,
    audio: str | Path | None = None,
    model: str | None = None,
    ratio: str | None = None,
    duration: int | None = None,
    generate_audio: bool | None = None,
    seed: int | None = None,
) -> str:
    """提交一个生成任务，返回 task id。"""
    cfg = seedance_config()
    payload: dict[str, Any] = {
        "model": model or cfg["model"],
        "content": build_content(prompt, images=images, video=video, audio=audio),
        "generate_audio": cfg["generate_audio"] if generate_audio is None else generate_audio,
        "ratio": ratio or cfg["ratio"],
        "duration": int(duration or cfg["duration"]),
        "watermark": bool(cfg["watermark"]),
    }
    if seed is not None:
        payload["seed"] = int(seed) % 2_147_483_647
    url = f"{str(cfg['base_url']).rstrip('/')}/contents/generations/tasks"
    async with httpx.AsyncClient(timeout=float(cfg["timeout"])) as client:
        resp = await client.post(url, json=payload, headers=_headers())
    if resp.status_code >= 400:
        raise RuntimeError(f"Seedance 提交失败 HTTP {resp.status_code}: {resp.text[:300]}")
    task_id = resp.json().get("id")
    if not task_id:
        raise RuntimeError(f"Seedance 响应里没有 task id: {resp.text[:300]}")
    return str(task_id)


async def poll(task_id: str) -> dict[str, Any]:
    cfg = seedance_config()
    url = f"{str(cfg['base_url']).rstrip('/')}/contents/generations/tasks/{task_id}"
    async with httpx.AsyncClient(timeout=float(cfg["timeout"])) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise RuntimeError(f"Seedance 查询失败 HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def wait(
    task_id: str,
    *,
    interval: float | None = None,
    timeout: float | None = None,
    on_tick: Any = None,
) -> dict[str, Any]:
    """轮询到终态。成功返回整个 task dict，失败抛 RuntimeError。"""
    cfg = seedance_config()
    interval = float(interval if interval is not None else cfg["poll_interval"])
    deadline = time.monotonic() + float(timeout if timeout is not None else cfg["poll_timeout"])
    while True:
        task = await poll(task_id)
        status = str(task.get("status", "")).lower()
        if on_tick:
            on_tick(status, task)
        if status in TERMINAL_OK:
            return task
        if status in TERMINAL_BAD:
            err = task.get("error") or task.get("failure_reason") or task
            raise RuntimeError(f"Seedance 任务 {task_id} {status}: {str(err)[:300]}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Seedance 任务 {task_id} 超时，最后状态 {status!r}")
        await asyncio.sleep(interval)


def video_url_of(task: dict[str, Any]) -> str:
    content = task.get("content") or {}
    if isinstance(content, dict) and (url := content.get("video_url")):
        return str(url)
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and (url := (item.get("video_url") or {}).get("url")):
                return str(url)
    raise RuntimeError(f"任务里没有 video_url: {str(task)[:300]}")


async def generate_video(prompt: str, *, on_tick: Any = None, **kw: Any) -> bytes:
    """提交 → 等待 → 下载，返回 mp4 bytes。"""
    wait_kw = {k: kw.pop(k) for k in ("interval", "timeout") if k in kw}
    task_id = await submit(prompt, **kw)
    task = await wait(task_id, on_tick=on_tick, **wait_kw)
    async with httpx.AsyncClient(timeout=float(seedance_config()["timeout"])) as client:
        resp = await client.get(video_url_of(task))
        resp.raise_for_status()
        return resp.content


def _main() -> None:
    ap = argparse.ArgumentParser(prog="vn_workflow.video_api")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--image", action="append", default=[], help="参考图，可多次（首帧/尾帧）")
    ap.add_argument("--video", default=None, help="参考视频（借运镜）")
    ap.add_argument("--audio", default=None, help="参考音频（借配乐）")
    ap.add_argument("--ratio", default=None)
    ap.add_argument("--duration", type=int, default=None)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("-o", "--out", default=str(ROOT / "out.mp4"))
    args = ap.parse_args(sys.argv[1:])

    t0 = time.perf_counter()

    def tick(status: str, _task: dict[str, Any]) -> None:
        print(f"  [{time.perf_counter() - t0:6.1f}s] {status}", file=sys.stderr)

    mp4 = asyncio.run(
        generate_video(
            args.prompt,
            images=args.image or None,
            video=args.video,
            audio=args.audio,
            ratio=args.ratio,
            duration=args.duration,
            generate_audio=not args.no_audio,
            seed=args.seed,
            on_tick=tick,
        )
    )
    Path(args.out).write_bytes(mp4)
    print(f"✓ {args.out} ({len(mp4) // 1024}KB, {time.perf_counter() - t0:.1f}s)")


if __name__ == "__main__":
    try:
        _main()
    except (RuntimeError, TimeoutError, httpx.HTTPError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
