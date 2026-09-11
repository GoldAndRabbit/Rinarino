"""占位音轨的程序化合成（只用标准库）。

第 5 段没有可用的生成端时走这里：amb.json 里的 prompt 照样落盘，
只是音频本身换成一段能无缝循环的合成音。播放端拿到的结构完全一样，
以后接上真的音频模型，跑一次 --only amb --force 就换掉。
"""

from __future__ import annotations

import array
import math
import random
import wave
from pathlib import Path

RATE = 22050

# 小调五声，铺底不抢戏
PENTATONIC = [0, 3, 5, 7, 10]


def _write(path: Path, samples: array.array) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return path


def _normalize(buf: list[float], peak: float = 0.55) -> array.array:
    top = max(1e-9, max(abs(v) for v in buf))
    scale = peak * 32767 / top
    return array.array("h", (int(max(-32767, min(32767, v * scale))) for v in buf))


def _fade_loop(buf: list[float], seconds: float = 1.5) -> list[float]:
    """把尾巴叠回开头，循环播放时接缝听不出来。"""
    n = min(len(buf) // 2, int(seconds * RATE))
    for i in range(n):
        t = i / n
        buf[i] = buf[i] * t + buf[len(buf) - n + i] * (1 - t)
    return buf[: len(buf) - n]


def pad(path: Path, *, seconds: float = 24.0, root: float = 174.61, seed: int = 0) -> Path:
    """慢速和弦垫：几个正弦分音 + 缓慢的音量起伏，当 BGM 用。"""
    rng = random.Random(seed)
    total = int(seconds * RATE)
    chords = [[0, 7, 12], [-4, 3, 8], [-2, 5, 9], [-5, 2, 7]]
    bar = total / len(chords)
    buf = [0.0] * total
    for ci, chord in enumerate(chords):
        start, end = int(ci * bar), int((ci + 1) * bar)
        attack = int(0.35 * (end - start))
        for degree in chord:
            freq = root * (2 ** (degree / 12))
            phase = rng.random() * math.tau
            detune = 1 + rng.uniform(-0.0016, 0.0016)
            for i in range(start, end):
                k = i - start
                env = min(1.0, k / attack) * (1 - 0.55 * (k / (end - start)))
                lfo = 1 + 0.05 * math.sin(math.tau * 0.12 * (i / RATE))
                w = math.tau * freq * detune * (i / RATE) + phase
                buf[i] += env * lfo * (math.sin(w) + 0.28 * math.sin(2 * w) + 0.1 * math.sin(3 * w))
    return _write(path, _normalize(_fade_loop(buf), 0.45))


def bed(path: Path, *, seconds: float = 24.0, tone: float = 0.35, seed: int = 0) -> Path:
    """环境声床：低通噪声 + 极低频起伏 + 零星的点缀，当 amb 用。

    tone 越大越「亮」（街道），越小越「闷」（室内）。
    """
    rng = random.Random(seed)
    total = int(seconds * RATE)
    buf = [0.0] * total
    lp = 0.0
    alpha = 0.004 + 0.05 * tone
    for i in range(total):
        lp += alpha * (rng.uniform(-1, 1) - lp)
        swell = 1 + 0.35 * math.sin(math.tau * 0.05 * (i / RATE) + rng.random() * 0.0001)
        rumble = 0.22 * math.sin(math.tau * 48 * (i / RATE))
        buf[i] = lp * swell * 3.2 + rumble
    # 零星点缀：远处的一两声，用五声音阶避免刺耳
    for _ in range(int(seconds / 3)):
        at = rng.randrange(0, total - RATE)
        freq = 440 * 2 ** ((PENTATONIC[rng.randrange(len(PENTATONIC))] + 12) / 12)
        length = int(rng.uniform(0.25, 0.7) * RATE)
        for k in range(length):
            env = math.exp(-4.0 * k / length)
            buf[at + k] += 0.22 * env * math.sin(math.tau * freq * (k / RATE))
    return _write(path, _normalize(_fade_loop(buf), 0.40))
