"""探索解谜剧本的闸门：validate 查剧本写没写错，solve 在状态空间里走一遍，证明每个结局都走得到。

    uv run python -m vn_workflow_v2.lint --name stanley

两件事都交给浏览器里跑的那份 JS 引擎来做，不在 Python 里另写一套规则——
两套规则迟早对不上，到时候闸门说能解、玩家却卡死了。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from util.paths import ROOT, Story

ENGINE = ROOT / "frontend" / "explore" / "engine.js"

SCRIPT = """
import fs from 'node:fs';
const { validate, solve } = await import(process.argv[2]);
const story = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify({ problems: validate(story), ...solve(story) }));
"""


def check(story: Story) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("[lint] 需要 node：闸门跑的就是浏览器里那份引擎")
    compiled = story.path("story.json")
    if not compiled.exists():
        raise SystemExit(f"[lint] {story.name} 还没有 story.json")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "lint.mjs"
        script.write_text(SCRIPT, encoding="utf-8")
        proc = subprocess.run(
            [node, str(script), ENGINE.as_uri(), str(compiled)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"[lint] 引擎跑挂了：\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def passed(result: dict[str, Any]) -> bool:
    return not result["problems"] and not result["missing"] and bool(result["exhausted"])


def run(story: Story, *, verbose: bool = True) -> dict[str, Any]:
    result = check(story)
    if verbose:
        problems = result["problems"]
        print(f"[lint] validate：{'干净' if not problems else f'{len(problems)} 处问题'}")
        for p in problems:
            print(f"  ✗ {p}")
        how = "搜完了" if result["exhausted"] else "没搜完，结论不可信"
        print(f"[lint] solve：{result['explored']} 个状态，{how}")
        for eid, path in result["endings"].items():
            print(f"  ✓ {eid}  最少做 {len(path)} 件事")
        for eid in result["missing"]:
            print(f"  ✗ {eid}  走不到")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vn_workflow_v2.lint")
    ap.add_argument("--name", required=True)
    ap.add_argument("--path", action="store_true", help="把每个结局的最短路径也打出来")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    story = Story(args.name)
    result = run(story)
    if args.path:
        for eid, path in result["endings"].items():
            print(f"\n【{eid}】")
            for i, step in enumerate(path, 1):
                print(f"  {i:>2}. {step}")
    return 0 if passed(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
