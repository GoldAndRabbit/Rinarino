"""探索解谜引擎用 node 跑：剧本自洽、每个结局解得开、几条关键的玩法规则。

和剧情引擎的测试一样，直接 import 浏览器里跑的那一份源码，测的就是玩家拿到的东西。
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from util.paths import ROOT, Story

STORY = Story("clockhouse")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not STORY.path("story.json").exists(), reason="需要 node 和探索示例"
)

SCRIPT = r"""
import fs from 'node:fs';
const { ExploreEngine, validate, solve } = await import(process.argv[2]);
const story = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};

out.problems = validate(story);
const solved = solve(story);
out.endings = Object.fromEntries(Object.entries(solved.endings).map(([k, v]) => [k, v.length]));
out.missing = solved.missing;
out.exhausted = solved.exhausted;

const drain = (e) => { while (e.canNext) e.next(); };
const find = (e, text) => {
  drain(e);
  const a = e.actions.find((x) => x.text === text);
  if (!a) {
    const have = e.actions.map((x) => x.text).join(' / ');
    throw new Error(`「${e.current}」没有行动「${text}」，只有：${have}`);
  }
  return a;
};
const act = (e, text, input) => e.choose(find(e, text).index, input);

const e = new ExploreEngine(story);
drain(e);
out.hiddenAtStart = e.actions.some((a) => a.text === '转身走进雨里');

act(e, '推门进去');
act(e, '去书房');
const code = act(e, '拨动数字轮', '0000');
out.wrongCode = { why: code.why, at: e.current, attempts: e.state.attempts.study_code };

act(e, '回前厅');
act(e, '去厨房');
act(e, '打开冷藏柜');
act(e, '去后花园');
const gate = find(e, '进温室');
const tried = e.choose(gate.index);
out.lockedGate = { locked: gate.locked, reason: gate.reason, why: tried.why, at: e.current };

const feed = find(e, '把东西扔给猎犬');
const wrong = e.choose(feed.index, 'meat');
out.wrongItem = { why: wrong.why, stillHas: e.state.inventory.includes('meat') };

drain(e);
const before = JSON.stringify(e.snapshot());
act(e, '回厨房');
e.prev();
out.prevWorks = JSON.stringify(e.snapshot()) === before;

const c = new ExploreEngine(story);
drain(c);
c.state.inventory.push('meat', 'pills');
out.combine = { ok: c.combine('meat', 'pills'), inventory: [...c.state.inventory].sort() };

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    script = tmp_path_factory.mktemp("js") / "run.mjs"
    script.write_text(SCRIPT, encoding="utf-8")
    proc = subprocess.run(
        [
            NODE,
            str(script),
            (ROOT / "frontend" / "explore" / "engine.js").as_uri(),
            str(STORY.path("story.json")),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_story_is_self_consistent(result):
    assert result["problems"] == []


def test_every_ending_is_solvable(result):
    """密室逃脱最怕卡死：让机器把状态空间走完，每个结局都得走得到。"""
    assert result["exhausted"], "状态空间没搜完，结论不可信"
    assert result["missing"] == []
    assert set(result["endings"]) == set(STORY.read_json("story.json")["endings"])


def test_wrong_code_keeps_you_at_the_lock(result):
    assert result["wrongCode"] == {"why": "wrong_code", "at": "study_door", "attempts": 1}


def test_locked_exit_says_what_is_missing(result):
    gate = result["lockedGate"]
    assert gate["locked"] and "猎犬" in gate["reason"]
    assert gate["why"] == "locked" and gate["at"] == "garden"


def test_wrong_item_is_never_consumed(result):
    """用错道具要是会消耗掉，玩家就可能卡死在再也解不开的局面里。"""
    assert result["wrongItem"] == {"why": "wrong_item", "stillHas": True}


def test_hidden_actions_stay_hidden(result):
    assert result["hiddenAtStart"] is False


def test_prev_undoes_one_step(result):
    assert result["prevWorks"] is True


def test_combine_uses_up_ingredients(result):
    assert result["combine"] == {"ok": True, "inventory": ["drugged_meat", "letter"]}


def test_standalone_page_inlines_the_explore_engine(tmp_path, monkeypatch):
    from vn_workflow import s6_build

    monkeypatch.setattr(s6_build, "DIST", tmp_path)
    html = s6_build.build(STORY, verbose=False).read_text(encoding="utf-8")
    head = html.split("const DATA")[0]
    assert "class ExploreEngine" in head and "class ExplorePlayer" in head
    assert "class Engine " not in head, "剧情引擎不该被塞进探索玩法的页面"
    assert "export " not in head
