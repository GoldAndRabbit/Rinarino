"""流水线各段的判定逻辑：art plan、cast 覆盖、素材闸门、打包。"""

from __future__ import annotations

import json

import pytest

from util.paths import Story
from vn_workflow import s3_gen_art, s3_lint_art, s6_build
from vn_workflow.s2_gen_cast import check_coverage, scan_ink

STORY = Story("americano")

pytestmark = pytest.mark.skipif(not STORY.exists(), reason="示例小说不在")


def test_art_plan_covers_everything_the_script_uses():
    scan = scan_ink(STORY.path("story.ink").read_text(encoding="utf-8"))
    plan = {job.id for job in s3_gen_art.build_plan(STORY)}
    for bid in scan["bg"]:
        assert bid in plan
    for cid in scan["cg"]:
        assert cid in plan
    for name, expressions in scan["sprites"].items():
        for exp in expressions:
            assert f"sprite_{name}_{exp}" in plan


def test_sprite_prompt_carries_the_full_look():
    job = next(j for j in s3_gen_art.build_plan(STORY) if j.id == "sprite_zhi_shy")
    # 外观逐字段进 prompt，这是同人一致性的第一道保证
    assert "焦糖橙色灯芯绒" in job.prompt
    assert "绿幕" in job.prompt
    assert job.seed() == job.seed()  # 同一张图重跑是同一个 seed


def test_cast_covers_the_script():
    scan = scan_ink(STORY.path("story.ink").read_text(encoding="utf-8"))
    assert check_coverage(STORY.read_json("cast.json"), scan) == []


def test_cast_coverage_catches_a_missing_background():
    scan = scan_ink(STORY.path("story.ink").read_text(encoding="utf-8"))
    cast = json.loads(json.dumps(STORY.read_json("cast.json")))
    victim = next(iter(cast["backgrounds"]))
    cast["backgrounds"].pop(victim)
    cast["characters"][0]["desc"].pop("配饰")
    problems = " ".join(check_coverage(cast, scan))
    assert victim in problems
    assert "配饰" in problems


def test_art_lint_is_clean_on_the_shipped_assets():
    assert [i for i in s3_lint_art.run(STORY, verbose=False) if i.level == "error"] == []


def test_build_produces_a_standalone_page(tmp_path, monkeypatch):
    monkeypatch.setattr(s6_build, "DIST", tmp_path)
    out = s6_build.build(STORY, verbose=False)
    html = out.read_text(encoding="utf-8")
    assert "class Engine" in html and "export" not in html.split("const DATA")[0]
    assert STORY.read_json("meta.json")["title"] in html
    assert (tmp_path / "assets" / "americano").is_dir()


def test_placeholder_counts_as_a_hole_not_a_finished_asset(tmp_path, monkeypatch):
    """有生成端时占位图要被顶掉；已经是真素材的则命中缓存。"""
    import asyncio

    from util import image_api

    # 先把设定表读出来：下面的 monkeypatch 打在类上，会一并改掉 STORY 的 dir
    cast_src = STORY.path("cast.json").read_text(encoding="utf-8")
    story = Story("x")
    monkeypatch.setattr(type(story), "dir", property(lambda _self: tmp_path))
    (tmp_path / "assets").mkdir()
    (tmp_path / "cast.json").write_text(cast_src, encoding="utf-8")
    jobs = s3_gen_art.build_plan(story)
    (tmp_path / "assets" / f"{jobs[0].id}.svg").write_bytes(b"<svg/>")
    (tmp_path / "assets" / f"{jobs[1].id}.png").write_bytes(b"x")

    monkeypatch.setattr(image_api, "has_credentials", lambda: True)
    plan = asyncio.run(s3_gen_art.run(story, dry_run=True, verbose=False))
    # 占位的那张仍在待办里，真素材那张不在
    assert plan["todo"] == len(jobs) - 1

    monkeypatch.setattr(image_api, "has_credentials", lambda: False)
    plan = asyncio.run(s3_gen_art.run(story, dry_run=True, verbose=False))
    assert plan["todo"] == len(jobs) - 2  # 没生成端时占位图就算数


def test_ark_key_aliases(monkeypatch):
    from util import image_api, llm_api

    monkeypatch.setattr(llm_api, "load_dotenv", lambda *a, **k: None)
    for name in ("ARK_API_KEY", "ARK_VOLCENGINE_API_KEY", "VOLCENGINE_API_KEY"):
        for other in image_api.ENV_KEY_ALIASES:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(name, "sk-test")
        assert image_api.api_key() == "sk-test", name


def test_pricing_comes_from_config_not_hardcoded():
    from util import pricing

    # 报账用的单价必须能被配置改掉，否则改价就要动代码
    assert pricing.image_unit("doubao-seedream-5-0-260128") == 0.22
    assert pricing.image_unit("doubao-seedream-4-5-251128") == 0.25
    assert pricing.image_unit("不存在的型号") == pricing.image_unit()
    assert pricing.video_unit("doubao-seedance-2-5-260628") > 0
    assert pricing.fmt(3.5) == "¥3.50"


def test_build_web_encodes_images(tmp_path, monkeypatch):
    """母版是存档尺寸，打包要转成上网能用的大小。"""
    from PIL import Image

    monkeypatch.setattr(s6_build, "DIST", tmp_path)
    s6_build.build(STORY, verbose=False)
    out = tmp_path / "assets" / "americano"
    masters = [p for p in STORY.assets.iterdir() if p.suffix.lower() == ".png"]
    if not masters:
        pytest.skip("还没有真素材")
    for master in masters:
        web = out / f"{master.stem}.webp"
        assert web.exists(), master.name
        assert web.stat().st_size < master.stat().st_size
        with Image.open(web) as im:
            assert max(im.size) <= max(s6_build.WEB_MAX.values())
            if master.stem.startswith("sprite_"):
                assert im.mode in ("RGBA", "LA"), "立绘的透明通道不能在打包时丢掉"


def test_expression_pose_reaches_the_prompt():
    """表情分「表情 / 动作」两段，两段都得进 prompt——否则六张立绘只有脸在动。"""
    cast = STORY.read_json("cast.json")
    spec = cast["characters"][1]["expressions"]["angry"]
    assert isinstance(spec, dict) and spec["动作"], "设定表里要给动作"
    job = next(j for j in s3_gen_art.build_plan(STORY) if j.id.endswith("_zhi_angry"))
    assert spec["表情"][:8] in job.prompt
    assert spec["动作"][:8] in job.prompt
    # 各表情的动作必须互不相同
    poses = {e["动作"] for e in cast["characters"][1]["expressions"].values()}
    assert len(poses) == len(cast["characters"][1]["expressions"])


def test_sprite_prompt_pins_down_the_background():
    """模型很爱自作主张加一片带色地面，加了 flood fill 就抠不掉。

    写实取向下还多一条：人像的构图惯例是裁到半身，「全身」得连不要什么一起说死。
    """
    job = next(j for j in s3_gen_art.build_plan(STORY) if j.kind == "sprite")
    for must in ("绿幕", "没有地面", "没有接触阴影", "不是半身像"):
        assert must in job.prompt


def test_scene_art_matches_the_player_frame():
    """出图比例必须和播放框一致，否则 object-fit 要么裁人要么拉变形。"""
    from util import image_api

    css = (s6_build.FRONTEND / "css" / "app.css").read_text(encoding="utf-8")
    assert "aspect-ratio: 4 / 5" in css
    for kind in ("bg", "cg"):
        w, h = (int(v) for v in image_api.size_for(kind).split("x"))
        assert abs(w / h - 4 / 5) < 0.01, kind
        assert w * h >= 3_686_400, f"{kind} 低于 Seedream 5.0 的 3.7MP 下限"


def test_first_character_is_the_global_style_anchor():
    """三个角色各画各的会飘成三种画风，摆进同一张 CG 就穿帮。"""
    plan = s3_gen_art.build_plan(STORY)
    sprites = [j for j in plan if j.kind == "sprite"]
    cast = STORY.read_json("cast.json")
    anchor = f"sprite_{cast['characters'][0]['key']}_normal"

    assert next(j for j in sprites if j.id == anchor).refs == [], "风格锚自己不参考任何图"
    for ch in cast["characters"][1:]:
        normal = next(j for j in sprites if j.id == f"sprite_{ch['key']}_normal")
        assert normal.refs == [anchor], f"{ch['name']} 的 normal 要参考风格锚"
    for job in sprites:
        if job.expression != "normal":
            assert job.refs == [f"sprite_{job.character}_normal"]


def test_waves_resolve_the_whole_dependency_chain():
    """依赖链有三层（锚 → 各角色 normal → 各表情），排程不能写死拨数。"""
    plan = s3_gen_art.build_plan(STORY)
    by_id = {j.id: j for j in plan}
    depth = {}

    def d(jid: str, seen: frozenset[str] = frozenset()) -> int:
        assert jid not in seen, f"{jid} 的参考图成环了"
        if jid in depth:
            return depth[jid]
        job = by_id[jid]
        depth[jid] = 0 if not job.refs else 1 + max(d(r, seen | {jid}) for r in job.refs)
        return depth[jid]

    assert max(d(j.id) for j in plan) == 2, "立绘应该正好三层"


def test_prompt_templates_render_and_catch_typos():
    """提示词模板：变量缺一个或多一个都要当场报错，不能安静地渲染出个半成品。"""
    from util import prompts

    rendered = prompts.render("story.user", shape_spec="<形态>", brief="<设定>")
    assert "<形态>" in rendered and "<设定>" in rendered
    assert "{{" not in rendered

    # 每个模板的变量要么在 vars.yaml 里有默认值，要么由调用处传——
    # 这里只验「system 类模板能靠默认值独立渲染」，它们没有调用处变量
    for name in ("story.system", "cast.system"):
        assert "{{" not in prompts.render(name)

    with pytest.raises(SystemExit):
        prompts.render("story.user", shape_spec="只给了一个")
    with pytest.raises(SystemExit):
        prompts.render("story.user", shape_spec="x", brief="y", shpae_spec="拼错了")
