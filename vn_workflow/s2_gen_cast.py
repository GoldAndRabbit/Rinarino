"""第 2 段：读剧本，产出 cast.json（外观设定）和 meta.json（界面文案）。

分成两个文件是有原因的：cast.json 是**给生图看的**，重跑立绘要拿它当唯一真源；
meta.json 是**给人看的**，标题、变量 label、结局说明会被反复手改。
所以 --keep-meta 重跑 cast 时不覆盖界面文案。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import Any

from util import llm_api, prompts
from util.paths import Story

RE_TAG = re.compile(r"^\s*#\s*(bg|cg|sprite|amb|music|video)\s*:\s*(.+)$", re.M)
RE_VAR = re.compile(r"^VAR\s+(\w+)", re.M)
RE_TITLE = re.compile(r"^\s*#\s*title\s*:\s*(.+)$", re.M)


def scan_ink(src: str) -> dict[str, Any]:
    """把剧本里真正引用过的 id 扫出来——cast 必须覆盖且只覆盖这些。"""
    found: dict[str, set[str]] = {k: set() for k in ("bg", "cg", "amb", "music", "video")}
    sprites: dict[str, set[str]] = {}
    for key, raw in RE_TAG.findall(src):
        if key == "sprite":
            for item in raw.split(","):
                name, _, exp = item.strip().partition(":")
                if name:
                    sprites.setdefault(name.strip(), set()).add(exp.strip() or "normal")
        else:
            found[key].add(raw.strip())
    return {
        **{k: sorted(v) for k, v in found.items()},
        "sprites": {k: sorted(v) for k, v in sprites.items()},
        "vars": RE_VAR.findall(src),
        "title": (m.group(1).strip() if (m := RE_TITLE.search(src)) else ""),
    }


def check_coverage(cast: dict[str, Any], scan: dict[str, Any]) -> list[str]:
    """cast 漏了什么 / 多了什么。和 s1 一样，判定结果直接回灌。"""
    problems: list[str] = []
    for field, key in (("backgrounds", "bg"), ("cgs", "cg")):
        want, got = set(scan[key]), set(cast.get(field) or {})
        if missing := want - got:
            problems.append(f"{field} 少了：{', '.join(sorted(missing))}")
        if extra := got - want:
            problems.append(f"{field} 多了剧本里没用过的：{', '.join(sorted(extra))}")
    # 真正的不变式是「剧本里摆上台的人，设定表里都得有」。
    # 早先这里查的是 VAR 和角色一一对应——那是恋爱形态的规矩（一人一个好感值），
    # 推理形态只有一个 clue 变量，照那条查会把好好的剧本判成错的。
    keys = {c.get("key") for c in cast.get("characters", [])}
    if missing := set(scan["sprites"]) - keys:
        names = ", ".join(sorted(missing))
        problems.append(f"characters 少了这些 key（剧本 # sprite: 里用到了）：{names}")
    if unused := keys - set(scan["sprites"]):
        problems.append(f"characters 里这几个人剧本从没让他们上台：{', '.join(sorted(unused))}")
    for ch in cast.get("characters", []):
        want = set(scan["sprites"].get(ch.get("key", ""), set())) | {"normal"}
        got = set(ch.get("expressions") or {})
        if missing := want - got:
            problems.append(f"{ch.get('name')} 的 expressions 少了：{', '.join(sorted(missing))}")
        if missing_fields := {"身份", "面部", "四肢", "上衣", "下装", "鞋", "配饰", "气质"} - set(
            ch.get("desc") or {}
        ):
            lack = ", ".join(sorted(missing_fields))
            problems.append(f"{ch.get('name')} 的 desc 少了字段：{lack}")
    return problems


def default_meta(story: Story, cast: dict[str, Any], scan: dict[str, Any]) -> dict[str, Any]:
    compiled = story.read_json("story.json")
    names = {c["key"]: c["name"] for c in cast.get("characters", [])}
    return {
        "story": story.name,
        "title": scan["title"] or story.name,
        "subtitle": "INK · 分支剧情",
        "shape": "",
        "tagline": "",
        "cover": next(iter(cast.get("cgs") or {}), None),
        "vars": {k: {"label": f"{names.get(k, k)} · 好感", "hidden": False} for k in scan["vars"]},
        "endings": {
            eid: {
                "title": eid,
                "character": next((k for k in names if k in eid), ""),
                "kind": "true" if eid.endswith("_true") else "pass",
            }
            for eid in compiled.get("endings", [])
        },
        "ui": {
            "narrator": "旁白",
            "next": "下一步",
            "prev": "上一步",
            "restart": "重开",
            "music_on": "音乐 开",
            "music_off": "音乐 关",
        },
    }


async def run(
    story: Story, *, keep_meta: bool = False, rounds: int = 3, verbose: bool = True
) -> dict[str, Any]:
    src = story.path("story.ink").read_text(encoding="utf-8")
    scan = scan_ink(src)
    messages = [
        {"role": "system", "content": prompts.render("cast.system")},
        {"role": "user", "content": prompts.render("cast.user", ink=src, scan=scan)},
    ]
    cast: dict[str, Any] = {}
    for attempt in range(1, rounds + 1):
        if verbose:
            print(f"[s2_cast] 第 {attempt}/{rounds} 次，模型在写设定…")
        cast = await llm_api.chat_json(messages)
        problems = check_coverage(cast, scan)
        if not problems:
            break
        if verbose:
            print("[s2_cast] ✗ 覆盖不全：\n  " + "\n  ".join(problems))
        messages += [
            {"role": "assistant", "content": __import__("json").dumps(cast, ensure_ascii=False)},
            {
                "role": "user",
                "content": prompts.render("cast.retry", problems="\n".join(problems)),
            },
        ]
    else:
        raise SystemExit("[s2_cast] 设定表覆盖不全，放弃")

    cast["story"] = story.name
    story.write_json("cast.json", cast)
    if not (keep_meta and story.path("meta.json").exists()):
        story.write_json("meta.json", default_meta(story, cast, scan))
    if verbose:
        chars = len(cast.get("characters", []))
        print(
            f"[s2_cast] ✓ {chars} 个角色，{len(cast.get('backgrounds') or {})} 个背景，"
            f"{len(cast.get('cgs') or {})} 张 CG"
        )
    return cast


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s2_gen_cast")
    ap.add_argument("--name", required=True)
    ap.add_argument("--keep-meta", action="store_true", help="不覆盖已有的界面文案")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    if not llm_api.has_credentials():
        print(f"没有 {llm_api.credential_hint()}，这一段跑不了", file=sys.stderr)
        return 1
    asyncio.run(run(Story(args.name), keep_meta=args.keep_meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
