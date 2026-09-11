"""编译器和闸门的判定要真的会响——这些用例全是「模型会犯的错」。"""

from __future__ import annotations

import pytest

from vn_workflow.inklite import CompileError, compile_text, lint_story
from vn_workflow.inklite.lint import has_errors

GOOD = """
# title: 测试

VAR a = 0

=== start ===
# node: 开场
# bg: bg_x
旁白一句。
甲: 台词一句。
* [往左]
    ~ a += 1
    -> mid
* [往右]
    -> mid

=== mid ===
# node: 中段
{a >= 1: -> good}
-> bad

=== good ===
好结局。
-> END

=== bad ===
坏结局。
-> END
"""


def compile_good():
    return compile_text(GOOD, speakers={"甲": "jia"})


def codes(src: str, **kw) -> set[str]:
    return {i.code for i in lint_story(compile_text(src, **kw))}


def test_compiles_clean():
    story = compile_good()
    assert story["title"] == "测试"
    assert story["start"] == "start"
    assert len(story["nodes"]) == 4
    assert set(story["endings"]) == {"good", "bad"}
    assert not has_errors(lint_story(story))


def test_speaker_and_tags():
    node = compile_good()["nodes"][0]
    assert node["tags"]["bg"] == "bg_x"
    assert node["label"] == "开场"
    assert node["lines"][0].get("speaker") is None
    assert node["lines"][1]["speaker"] == "jia"


def test_choice_effects_and_cond():
    node = compile_good()["nodes"][0]
    assert node["choices"][0]["effects"] == [["a", "+=", 1]]
    assert compile_good()["nodes"][1]["diverts"][0]["cond"] == [["a", ">=", 1]]


@pytest.mark.parametrize(
    ("src", "message"),
    [
        ("正文写在结点外\n", "结点"),
        ("=== a ===\n~ b += 1\n-> END\n", "声明"),
        ("=== a ===\n* [没落点]\n* [也没有]\n", "落点"),
        ("=== a ===\n=== a ===\n-> END\n", "重复"),
        ("=== a ===\n# bogus: x\n-> END\n", "指令"),
    ],
)
def test_compile_errors(src: str, message: str):
    with pytest.raises(CompileError) as exc:
        compile_text(src)
    assert message in str(exc.value)


def test_dead_link():
    assert "dead-link" in codes("=== a ===\n话。\n-> nowhere\n")


def test_orphan():
    src = "=== a ===\n话。\n-> END\n\n=== b ===\n没人来。\n-> END\n"
    assert "orphan" in codes(src)


def test_cycle():
    src = "=== a ===\n话。\n-> b\n\n=== b ===\n话。\n-> a\n"
    assert "cycle" in codes(src)


def test_too_few_choices():
    src = "=== a ===\n话。\n* [唯一] -> b\n\n=== b ===\n话。\n-> END\n"
    assert "too-few-choices" in codes(src)


def test_no_exit():
    assert "no-exit" in codes("=== a ===\n话说完了就没了。\n")


def test_no_ending():
    src = "=== a ===\n话。\n* [左] -> a\n* [右] -> a\n"
    assert "no-ending" in codes(src)


def test_undeclared_var_in_cond():
    src = "=== a ===\n{z >= 1: -> b}\n-> b\n\n=== b ===\n话。\n-> END\n"
    assert "undeclared-var" in codes(src)


def test_no_fallback_when_only_conditional():
    src = "VAR a = 0\n=== a ===\n{a >= 1: -> b}\n\n=== b ===\n话。\n~ a += 1\n-> END\n"
    assert "no-fallback" in codes(src)


def test_unplayable_branch_is_caught():
    """图上连着、但变量永远攒不到那个阈值的分支 —— 玩家看不到的死结局。"""
    src = """
VAR a = 0

=== start ===
话。
* [左]
    ~ a += 1
    -> settle
* [右]
    ~ a += 1
    -> settle

=== settle ===
{a >= 5: -> good}
-> bad

=== good ===
好。
-> END

=== bad ===
坏。
-> END
"""
    issues = lint_story(compile_text(src))
    bad = [i for i in issues if i.code == "unplayable"]
    assert [i.node for i in bad] == ["good"]


def test_playable_branches_are_not_flagged():
    src = """
VAR a = 0

=== start ===
话。
* [左]
    ~ a += 1
    -> settle
* [右]
    -> settle

=== settle ===
{a >= 1: -> good}
-> bad

=== good ===
好。
-> END

=== bad ===
坏。
-> END
"""
    assert not [i for i in lint_story(compile_text(src)) if i.code == "unplayable"]


def test_mystery_shape_gates_evidence_thresholds():
    """推理形态的核心：证据不够就走不到坐实结局，但两条都得走得通。"""
    src = """
VAR clue = 0

=== scene ===
现场。
* [细看] 
    ~ clue += 1
    -> ask
* [略过]
    -> ask

=== ask ===
问话。
* [尖锐]
    ~ clue += 1
    -> accuse
* [客气]
    -> accuse

=== accuse ===
指认。
* [甲] -> verdict
* [乙] -> wrong

=== verdict ===
{clue >= 2: -> solid}
-> weak

=== solid ===
坐实。
-> END

=== weak ===
脱身。
-> END

=== wrong ===
指错。
-> END
"""
    assert not has_errors(lint_story(compile_text(src)))
