"""LLM transport 层（从 uhwoo/backend/llm_api.py 移植）：OpenAI 兼容 /v1/chat/completions。

不含业务话术，只负责拼 URL / header / payload、SSE 解码、transient 重试。
文本生成统一走 deepseek（默认经阿里云百炼兼容模式）。

provider:
  - aliyun        env ALIYUN_BAILIAN_API_KEY (DashScope 兼容模式，model=deepseek-v4-flash)
  - deepseek      env DEEPSEEK_API_KEY       (官方直连，model=deepseek-chat)
  - siliconflow   env SILICONFLOW_API_KEY

配置: config/llm_api.yaml（base_url / default_model / vn 段）。
鉴权环境变量名固定在代码里，不进 yaml；支持项目根目录的 .env。

公开接口:
  - chat_complete(messages, ...) -> str
  - chat_complete_streaming(...) -> str          底层流式聚合，丢弃 reasoning
  - chat_stream(...) -> AsyncIterator[tuple[Literal["reasoning","content"], str]]
  - chat_json(messages, ...) -> Any              要一段 JSON，自动扒 ```json 围栏

CLI: uv run python -m vn_workflow.llm_api [-m MODEL] [--no-stream] [message...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "llm_api.yaml"

ENV_KEY = {
    "aliyun": "ALIYUN_BAILIAN_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
}

# 偶发断连 / 读超时可重试
TRANSIENT_EXC: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)

DEFAULTS: dict[str, dict[str, Any]] = {
    "aliyun": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "deepseek-v4-flash",
    },
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat"},
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
}


# 按顺序找 .env，先到先得；真实环境变量始终优先。
# 邻仓的 .env 是**只读的借用**：key 不复制一份到本仓，源头轮换了这边跟着变。
# 想换地方就改 config/llm_api.yaml 的 env_files，或设 RINARINO_ENV_FILE。
DEFAULT_ENV_FILES = (".env", "../pitchasso/.env", "../uhwoo/.env")

_dotenv_loaded = False


def env_files() -> list[Path]:
    paths: list[Path] = []
    if explicit := os.environ.get("RINARINO_ENV_FILE", "").strip():
        paths.append(Path(explicit).expanduser())
    configured = DEFAULT_ENV_FILES
    # 这里不能用 load_yaml()：它自己会先调 load_dotenv()，会绕成环
    if CONFIG_PATH.exists():
        try:
            import yaml

            raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if listed := raw.get("env_files"):
                configured = tuple(listed)
        except Exception:  # 配置坏了就退回默认，不阻塞启动
            pass
    paths += [(ROOT / p).resolve() for p in configured]
    return paths


def load_dotenv(force: bool = False) -> None:
    """在任何 os.environ 读取前加载 .env（已存在的环境变量优先，先找到的文件优先）。"""
    global _dotenv_loaded
    if _dotenv_loaded and not force:
        return
    _dotenv_loaded = True
    for f in env_files():
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def key_source(name: str) -> str:
    """这把 key 是从哪儿来的——只报出处，不报值。"""
    load_dotenv()
    if not os.environ.get(name, "").strip():
        return "（没有）"
    for f in env_files():
        if not f.is_file():
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        if any(line.strip().startswith(f"{name}=") for line in lines):
            try:
                return str(f.relative_to(ROOT.parent))
            except ValueError:
                return str(f)
    return "环境变量"


@lru_cache(maxsize=1)
def load_yaml() -> dict[str, Any]:
    load_dotenv()
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # 配置坏了不阻塞启动，退回默认
        logger.warning("读取 %s 失败：%s", CONFIG_PATH, exc)
        return {}


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    base_url: str
    model: str
    models: tuple[str, ...]
    enable_thinking: bool | None
    timeout: float


@lru_cache(maxsize=1)
def load_config() -> LlmConfig:
    raw = load_yaml()
    vn = raw.get("vn") or {}
    provider = os.environ.get("VN_PROVIDER") or vn.get("provider") or "aliyun"
    provider = provider if provider in DEFAULTS else "aliyun"
    section = raw.get(provider) if isinstance(raw.get(provider), dict) else {}
    base_url = section.get("base_url") or DEFAULTS[provider]["base_url"]
    model = (
        os.environ.get("VN_MODEL")
        or vn.get("model")
        or section.get("default_model")
        or DEFAULTS[provider]["default_model"]
    )
    thinking = vn.get("enable_thinking")
    models = tuple(vn.get("models") or ()) or (model,)
    if model not in models:
        models = (model, *models)
    return LlmConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        model=model,
        models=models,
        enable_thinking=None if thinking is None else bool(thinking),
        timeout=float(vn.get("timeout", 180)),
    )


def api_key(provider: str) -> str:
    load_dotenv()
    return os.environ.get(ENV_KEY[provider], "").strip()


def has_credentials() -> bool:
    return bool(api_key(load_config().provider))


def credential_hint() -> str:
    cfg = load_config()
    return f"{ENV_KEY[cfg.provider]}（provider={cfg.provider}, model={cfg.model}）"


def request_kwargs(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    stream: bool,
    enable_thinking: bool | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    cfg = load_config()
    provider = provider or cfg.provider
    key = api_key(provider)
    if not key:
        raise RuntimeError(f"{ENV_KEY[provider]} is not set")
    payload: dict[str, Any] = dict(extra_payload or {})
    if enable_thinking is None:
        enable_thinking = cfg.enable_thinking
    if enable_thinking is not None and provider == "aliyun":
        payload["enable_thinking"] = bool(enable_thinking)
    payload.update({"model": model or cfg.model, "messages": messages, "stream": stream})
    base = cfg.base_url if provider == cfg.provider else DEFAULTS[provider]["base_url"].rstrip("/")
    return (
        f"{base}/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
    )


async def chat_complete(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    enable_thinking: bool | None = None,
    extra_payload: dict[str, Any] | None = None,
    max_attempts: int = 4,
) -> str:
    url, headers, payload = request_kwargs(
        messages,
        provider=provider,
        model=model,
        stream=False,
        enable_thinking=enable_thinking,
        extra_payload=extra_payload,
    )
    timeout = timeout if timeout is not None else load_config().timeout
    delays = (0.6, 1.2, 2.4)
    last: BaseException | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            # 部分深度思考模型上游强制要求 stream=True，转流式重试一次
            body = exc.response.text if exc.response is not None else ""
            if exc.response is not None and exc.response.status_code == 400 and "stream" in body:
                return await chat_complete_streaming(
                    messages,
                    provider=provider,
                    model=model,
                    timeout=timeout,
                    enable_thinking=enable_thinking,
                    extra_payload=extra_payload,
                )
            raise RuntimeError(f"HTTP {exc.response.status_code}: {body[:300]}") from exc
        except TRANSIENT_EXC as exc:
            last = exc
            if attempt >= max_attempts - 1:
                raise
            wait = delays[min(attempt, len(delays) - 1)]
            logger.warning(
                "chat 失败 (%s)，%.1fs 后第 %s/%s 次重试",
                type(exc).__name__,
                wait,
                attempt + 2,
                max_attempts,
            )
            await asyncio.sleep(wait)
    assert last is not None
    raise last


async def chat_complete_streaming(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    enable_thinking: bool | None = None,
    extra_payload: dict[str, Any] | None = None,
    max_attempts: int = 4,
) -> str:
    """对外等价于 chat_complete（返回最终正文 str），底层走流式聚合、丢弃 reasoning。"""
    delays = (0.6, 1.2, 2.4)
    last: BaseException | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            parts: list[str] = []
            async for kind, chunk in chat_stream(
                messages,
                provider=provider,
                model=model,
                timeout=timeout,
                enable_thinking=enable_thinking,
                extra_payload=extra_payload,
            ):
                if kind == "content":
                    parts.append(chunk)
            return "".join(parts)
        except TRANSIENT_EXC as exc:
            last = exc
            if attempt >= max_attempts - 1:
                raise
            wait = delays[min(attempt, len(delays) - 1)]
            logger.warning(
                "chat(stream) 失败 (%s)，%.1fs 后第 %s/%s 次重试",
                type(exc).__name__,
                wait,
                attempt + 2,
                max_attempts,
            )
            await asyncio.sleep(wait)
    assert last is not None
    raise last


async def chat_stream(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
    enable_thinking: bool | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[Literal["reasoning", "content"], str]]:
    """thinking 模型先 ("reasoning", ...)，再 ("content", ...)；普通模型只有 content。"""
    url, headers, payload = request_kwargs(
        messages,
        provider=provider,
        model=model,
        stream=True,
        enable_thinking=enable_thinking,
        extra_payload=extra_payload,
    )
    timeout = timeout if timeout is not None else load_config().timeout
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = obj["choices"][0].get("delta") or {}
                except (KeyError, IndexError, TypeError):
                    continue
                if rs := delta.get("reasoning_content"):
                    yield ("reasoning", rs)
                if ct := delta.get("content"):
                    yield ("content", ct)


_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def strip_fence(text: str) -> str:
    """模型爱把 JSON / ink 包在 ``` 围栏里，剥掉。"""
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip()


async def chat_json(messages: list[dict[str, Any]], **kw: Any) -> Any:
    """要一段 JSON。解析失败时把原文附在异常里，方便原样回灌给模型重写。"""
    raw = await chat_complete(messages, **kw)
    body = strip_fence(raw)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型没给出合法 JSON：{exc}\n--- 原文 ---\n{raw[:2000]}") from exc


async def _main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(prog="vn_workflow.llm_api")
    parser.add_argument("-p", "--provider", default=cfg.provider, choices=sorted(DEFAULTS))
    parser.add_argument("-m", "--model", default=cfg.model)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--thinking", action="store_true")
    parser.add_argument("message", nargs="*")
    args = parser.parse_args(sys.argv[1:])

    user_msg = " ".join(args.message).strip() or "你是谁"
    print(f"[cfg] provider={args.provider} base_url={cfg.base_url}")
    print(f"[model] {args.model}  [mode] {'non-stream' if args.no_stream else 'stream'}")
    print(f"[user] {user_msg!r}\n[assistant]")
    messages = [{"role": "user", "content": user_msg}]
    t0 = time.perf_counter()
    if args.no_stream:
        print(
            await chat_complete(
                messages, provider=args.provider, model=args.model, enable_thinking=args.thinking
            )
        )
    else:
        async for kind, chunk in chat_stream(
            messages, provider=args.provider, model=args.model, enable_thinking=args.thinking
        ):
            print(chunk, end="", flush=True, file=sys.stderr if kind == "reasoning" else sys.stdout)
        print()
    print(f"[total] {time.perf_counter() - t0:.3f}s", file=sys.stderr)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except (RuntimeError, httpx.HTTPStatusError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
