"""前端运行时用 node 跑：所有结局可达、回退能回到同一行。

播放端是原生 JS，没有构建步骤，所以测试就直接 import 那份源码，
测的和浏览器里跑的是同一个文件。
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from util.paths import ROOT, Story

STORY = Story("americano")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not STORY.path("story.json").exists(), reason="需要 node 和示例小说"
)

SCRIPT = """
import fs from 'node:fs';
const {Engine} = await import(process.argv[2]);
const story = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

const hit = {};
(function walk(e, depth) {
  if (depth > 60) return;
  while (!e.finished && !e.awaitingChoice) e.next();
  if (e.finished) { hit[e.ending] = (hit[e.ending] || 0) + 1; return; }
  for (const c of e.choices) {
    const fork = new Engine(story);
    fork.restore(e.snapshot()); fork.history = [];
    fork.choose(c.index);
    walk(fork, depth + 1);
  }
})(new Engine(story), 0);

const e = new Engine(story);
for (let i = 0; i < 6; i++) e.next();
const before = e.line.text;
e.next(); e.prev();

console.log(JSON.stringify({hit, prevWorks: e.line.text === before}));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    script = tmp_path_factory.mktemp("js") / "run.mjs"
    script.write_text(SCRIPT, encoding="utf-8")
    proc = subprocess.run(
        [
            NODE,
            str(script),
            (ROOT / "frontend" / "engine" / "engine.js").as_uri(),
            str(STORY.path("story.json")),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_every_ending_is_reachable(result):
    declared = set(STORY.read_json("story.json")["endings"])
    assert declared and set(result["hit"]) == declared


def test_prev_returns_to_the_same_line(result):
    assert result["prevWorks"] is True
