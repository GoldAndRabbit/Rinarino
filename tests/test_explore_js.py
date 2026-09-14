"""探索解谜：剧本自洽、每个结局解得开、几条关键的玩法规则、v2 工作流的打包。

和剧情引擎的测试一样，直接 import 浏览器里跑的那一份源码，测的就是玩家拿到的东西。
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from util.paths import ROOT, Story

CLOCKHOUSE = Story("clockhouse")
NODE = shutil.which("node")
EXPLORE_STORIES = [
    s for s in (Story("clockhouse"), Story("stanley")) if s.path("story.json").exists()
]

pytestmark = pytest.mark.skipif(
    NODE is None or not CLOCKHOUSE.path("story.json").exists(), reason="需要 node 和探索示例"
)

RULES = r"""
import fs from 'node:fs';
const { ExploreEngine } = await import(process.argv[2]);
const story = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};

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

const t = new ExploreEngine(story);
drain(t);
t.state.inventory.push('matches');
t.apply([{ type: 'stash', name: 'box' }]);
const emptied = t.state.inventory.length === 0;
t.apply([{ type: 'unstash', name: 'box' }]);
out.stash = { emptied, back: [...t.state.inventory].sort(), left: Object.keys(t.state.stashes) };

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def rules(tmp_path_factory):
    script = tmp_path_factory.mktemp("js") / "rules.mjs"
    script.write_text(RULES, encoding="utf-8")
    proc = subprocess.run(
        [
            NODE,
            str(script),
            (ROOT / "frontend" / "explore" / "engine.js").as_uri(),
            str(CLOCKHOUSE.path("story.json")),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("story", EXPLORE_STORIES, ids=lambda s: s.name)
def test_story_is_consistent_and_solvable(story):
    """密室逃脱最怕卡死：让机器把状态空间走完，每个结局都得走得到。跑的就是 v2 的闸门。"""
    from vn_workflow_v2 import lint

    result = lint.check(story)
    assert result["problems"] == []
    assert result["exhausted"], "状态空间没搜完，结论不可信"
    assert result["missing"] == []
    assert set(result["endings"]) == set(story.read_json("story.json")["endings"])


def test_wrong_code_keeps_you_at_the_lock(rules):
    assert rules["wrongCode"] == {"why": "wrong_code", "at": "study_door", "attempts": 1}


def test_locked_exit_says_what_is_missing(rules):
    gate = rules["lockedGate"]
    assert gate["locked"] and "猎犬" in gate["reason"]
    assert gate["why"] == "locked" and gate["at"] == "garden"


def test_wrong_item_is_never_consumed(rules):
    """用错道具要是会消耗掉，玩家就可能卡死在再也解不开的局面里。"""
    assert rules["wrongItem"] == {"why": "wrong_item", "stillHas": True}


def test_hidden_actions_stay_hidden(rules):
    assert rules["hiddenAtStart"] is False


def test_prev_undoes_one_step(rules):
    assert rules["prevWorks"] is True


def test_combine_uses_up_ingredients(rules):
    assert rules["combine"] == {"ok": True, "inventory": ["drugged_meat", "letter"]}


def test_stash_takes_everything_and_gives_it_back(rules):
    """被打晕、东西被搜走、再从别处找回来——Stanley 第一代的蓝色房间就是这个机制。"""
    assert rules["stash"] == {"emptied": True, "back": ["letter", "matches"], "left": []}


def test_v2_build_inlines_the_explore_engine(tmp_path, monkeypatch):
    from vn_workflow_v2 import build

    monkeypatch.setattr(build, "DIST", tmp_path)
    html = build.build(CLOCKHOUSE, verbose=False).read_text(encoding="utf-8")
    head = html.split("const DATA")[0]
    assert "class ExploreEngine" in head and "class ExplorePlayer" in head
    assert "class Engine " not in head, "剧情引擎不该被塞进探索玩法的页面"
    assert "export " not in head


def test_v1_build_refuses_explore_stories():
    """分支剧情的打包不认探索剧本：两套工作流分开了，别悄悄打出一个播不了的页面。"""
    from vn_workflow import s6_build

    with pytest.raises(SystemExit, match="vn_workflow_v2"):
        s6_build.build(CLOCKHOUSE, verbose=False)
