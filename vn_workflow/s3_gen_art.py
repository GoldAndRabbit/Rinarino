"""第 3 段：背景 / 定场图 / 事件 CG / 立绘。

四类素材分开重跑（--only bg|est|cg|sprite），命名即缓存：
assets/<id>.png 存在就跳过，--force 才重画。每张图**真正用过的 prompt**
落在 assets/prompts.json 里，所以重跑一张和当初那张是同一套输入。

立绘的一致性靠两件事：
  1. 同一个角色的所有表情共享一份「外观段」（cast.json 的 desc 逐字段拼进 prompt）
  2. 先画 normal 作为**参考图**，其余表情把它当 reference_image 喂回去

没配 ARK_API_KEY 时退回占位图（SVG），文件名、清单、prompts.json 全都照常落盘，
后面几段和播放端拿到的结构完全一样，配上 key 跑一次 --force 就换成真素材。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from util import image_api, pricing
from util.paths import Story

Kind = Literal["bg", "est", "cg", "sprite"]
KINDS: tuple[Kind, ...] = ("bg", "est", "cg", "sprite")


@dataclass
class ArtJob:
    id: str
    kind: Kind
    prompt: str
    character: str = ""
    expression: str = ""
    # 依赖的其它图（先画它们，再把它们当参考图喂回去）：
    # 表情图参考本角色的 normal；CG / 定场图参考画面里出现的每个角色的 normal
    refs: list[str] = field(default_factory=list)
    tags: dict[str, Any] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return f"{self.id}.png"

    def seed(self) -> int:
        return int(hashlib.sha256(self.id.encode()).hexdigest()[:8], 16)


# 背景 / 定场图是**空镜**：人物是后面叠上去的立绘，背景里再画一个人，
# 立绘一站上去就成了「身后藏着个陌生人」。模型很爱往空屋子里塞个人添气氛，
# 所以这句要写死、写具体（只说「无人」它会画个背影当没听见）。
_EMPTY_STAGE = (
    "空镜：画面里一个人也没有，没有人物、没有背影、没有半身入画的路人、"
    "没有玻璃或水面里的人影，只有空间、家具和陈设"
)


def _compose(art: dict[str, Any], subject: str, *, kind: Kind, extra: str = "") -> str:
    """统一的 prompt 骨架：风格 → 主体 → 光线 / 构图 → 负面。

    骨架里**不准出现画风词**，只管画幅、构图、去背这些和取向无关的硬要求。
    画风只从 art.style 来——之前骨架里写着「写实场景」「没有线稿和描边」，
    一换取向，骨架和 style 就在同一句 prompt 里互相拆台。
    """
    parts = [
        art.get("style", ""),
        subject.strip(),
        extra.strip(),
        art.get("palette", ""),
        # 立绘不拼 lighting：那是**场景**的光（「咖啡馆 2700K 暖黄 + 窗外 5500K 冷光」）。
        # 二次元取向下它只是气氛词，写实取向下模型会当真去搭一个房间——
        # 实测立绘被画成办公室实景，边缘一点白都没有，flood fill 直接抠不动。
        art.get("lighting", "") if kind != "sprite" else "",
    ]
    if kind == "sprite":
        parts.append(
            # 人像的构图惯例是裁到半身，所以「全身」要连**不要什么**一起说死，
            # 只写「全身立绘」会得到一张齐腰截断的胸像。
            "全身立绘，9:16 竖构图，居中，从头顶到鞋子完整入画，两只鞋都完整可见，"
            "不是半身像、不是胸像、不是特写，不在腰部或膝盖裁切，"
            # 去背的硬要求。背景是**绿幕**不是白底：白底下「背景」和「白衣服」在像素上
            # 是同一种东西，白衬衫会被打成筛子、白球鞋会被啃穿、米色西装夹住的背景抠不掉
            # （判据全试过，三组区间都重叠，见 util.image_api.CHROMA 那段）。
            # 换成衣服上不可能出现的饱和绿，抠图就退化成一个减法。
            # 另外**不能说「无缝背景纸」**——摄影里那东西自带一条地面扫尾，
            # 模型会照着画出地面和接触阴影。
            "影棚人像棚拍，背景是完全均匀的纯正绿幕 #00B140 色度键背景，"
            "从头顶到脚下是同一片饱和绿，没有地面、没有地平线、没有接触阴影、没有影子，"
            "鞋底以下也是同一片绿，画面里没有房间、没有家具、没有窗户、没有街景、"
            "没有前景遮挡物，不要渐变，"
            # 绿幕会把绿光反到浅色衣服上，说清楚免得整件衣服被染绿——
            # 去溢色只能救边缘，救不了大面积染色
            "人物身上和衣服上不带任何绿色调，不要绿色反光，"
            "高精度细节：五官清晰锐利，发丝一根根分明，衣料织纹与褶皱清楚，"
            # 景深糊的是手和脚——立绘要整张合焦，虚化只给场景
            "人物从头到脚全部合焦，不要景深虚化"
        )
    elif kind == "est":
        parts.append("16:9 横构图，定场镜头，浅景深，画面中不出现文字，" + _EMPTY_STAGE)
    elif kind == "bg":
        # 背景是拿来垫立绘的舞台：画里但凡有个人，立绘一站上去就变成「背后藏了个人」；
        # 浅景深则正好把前面那张合焦的立绘从背景里推出来
        parts.append("4:5 竖构图，浅景深，主体居中且完整入画，画面中不出现文字，" + _EMPTY_STAGE)
    else:
        # 播放框是 4:5，出图就按 4:5——主体居中、留出上下余量，才不会被裁到
        parts.append("4:5 竖构图，浅景深，主体居中且完整入画，画面中不出现文字")
    # 负面词两段：画风层的（avoid_style，换取向时整段换）+ 这部作品自己的忌讳（avoid）。
    # 两段各写各的，难免撞词（「塑料感」两边都有），拼的时候去个重。
    avoid: list[str] = []
    for chunk in (art.get("avoid_style"), art.get("avoid")):
        for word in str(chunk or "").split("、"):
            if (word := word.strip()) and word not in avoid:
                avoid.append(word)
    if avoid:
        parts.append(f"避免：{'、'.join(avoid)}")
    return "，".join(p.strip().strip("，") for p in parts if p and p.strip())


def look_of(ch: dict[str, Any]) -> str:
    """角色的「外观段」：逐字段拼成一串。同人一致性的第一道保证——
    立绘和 CG 拿的是同一份文字，模型才不会画成两个人。"""
    return "；".join(f"{k}：{v}" for k, v in (ch.get("desc") or {}).items())


def cast_in(desc: str, characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """这段画面描述里点名了哪几个角色。"""
    return [c for c in characters if c.get("name") and c["name"] in desc]


def build_plan(story: Story) -> list[ArtJob]:
    """从 cast.json 推出这部小说需要哪些图。这是 s3 唯一的真源。"""
    cast = story.read_json("cast.json")
    art = cast.get("art_direction", {})
    characters: list[dict[str, Any]] = cast.get("characters", [])
    jobs: list[ArtJob] = []

    def scene_job(sid: str, desc: str, kind: Kind) -> ArtJob:
        """背景 / 定场图 / CG。画面里点了名的角色，外观段一并拼进来，
        并把他们的 normal 立绘当参考图——只靠一句「林晚抱着一本书」，
        模型会自由发挥出另一个人。"""
        present = cast_in(desc, characters)
        extra = "".join(f"画面中的{c['name']}：{look_of(c)}。" for c in present)
        return ArtJob(
            id=sid,
            kind=kind,
            prompt=_compose(art, desc, kind=kind, extra=extra),
            refs=[f"sprite_{c['key']}_normal" for c in present],
        )

    for bid, desc in (cast.get("backgrounds") or {}).items():
        jobs.append(scene_job(bid, desc, "bg"))
    for eid, desc in (cast.get("est") or {}).items():
        jobs.append(scene_job(eid, desc, "est"))
    for cid, desc in (cast.get("cgs") or {}).items():
        jobs.append(scene_job(cid, desc, "cg"))

    # 第一个角色的 normal 是**全局风格锚**：它先画，之后每个角色的 normal 都拿它
    # 当参考图。不这么做的话三个角色各画各的，一个出成照片写实、另两个出成插画，
    # 摆在同一张 CG 里就穿帮了。表情图再参考本角色自己的 normal。
    anchor = f"sprite_{characters[0]['key']}_normal" if characters else ""

    for ch in characters:
        key = ch["key"]
        look = look_of(ch)
        expressions: dict[str, Any] = ch.get("expressions") or {}
        # normal 先画，作为其余表情的参考图
        ordered = ["normal", *[e for e in expressions if e != "normal"]]
        for exp in ordered:
            if exp not in expressions:
                continue
            spec = expressions[exp]
            # 表情可以只写一句话，也可以写成 {表情, 动作}。
            # 六张只有脸在动、姿势一模一样，看着就很廉价——所以动作要单独给。
            if isinstance(spec, dict):
                face = spec.get("表情") or spec.get("face") or ""
                pose = spec.get("动作") or spec.get("pose") or ""
            else:
                face, pose = str(spec), ""
            detail = f"当前表情：{face}" + (f"。当前动作与体态：{pose}" if pose else "")
            subject = f"一名女性角色。{look}。{detail}"
            jobs.append(
                ArtJob(
                    id=f"sprite_{key}_{exp}",
                    kind="sprite",
                    prompt=_compose(art, subject, kind="sprite"),
                    character=key,
                    expression=exp,
                    refs=(
                        []
                        if f"sprite_{key}_{exp}" == anchor
                        else [anchor]
                        if exp == "normal"
                        else [f"sprite_{key}_normal"]
                    ),
                )
            )
    return jobs


# --- 占位图：没 key 时的退路 ----------------------------------------------------

_PALETTE = {
    "bg": ("#2b3a4a", "#6b7f86"),
    "est": ("#3a3326", "#c8a06a"),
    "cg": ("#3b2f3a", "#c8762f"),
    "sprite": ("#f3f1ec", "#cfd6d9"),
}


def placeholder_svg(job: ArtJob, *, label: str) -> bytes:
    dark, light = _PALETTE[job.kind]
    w, h = (900, 1600) if job.kind == "sprite" else (1600, 900)
    if job.kind == "sprite":
        body = (
            f'<ellipse cx="{w // 2}" cy="{int(h * 0.20)}" rx="{int(w * 0.15)}" '
            f'ry="{int(h * 0.10)}" fill="{light}"/>'
            f'<path d="M{int(w * 0.28)} {h} L{int(w * 0.34)} {int(h * 0.32)} '
            f"Q{w // 2} {int(h * 0.26)} {int(w * 0.66)} {int(h * 0.32)} "
            f'L{int(w * 0.72)} {h} Z" fill="{light}"/>'
        )
        text_fill = "#5b6a72"
    else:
        body = (
            f'<circle cx="{int(w * 0.78)}" cy="{int(h * 0.28)}" r="{int(h * 0.16)}" '
            f'fill="{light}" opacity="0.35"/>'
            f'<path d="M0 {int(h * 0.72)} Q{w // 4} {int(h * 0.60)} {w // 2} {int(h * 0.70)} '
            f'T{w} {int(h * 0.66)} L{w} {h} L0 {h} Z" fill="{light}" opacity="0.45"/>'
        )
        text_fill = "#e8eef1"
    head = f'viewBox="0 0 {w} {h}" width="{w}" height="{h}"'
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" {head}>
  <rect width="{w}" height="{h}" fill="{dark}"/>
  {body}
  <text x="{w // 2}" y="{int(h * 0.52)}" fill="{text_fill}" font-size="{int(h * 0.045)}"
        font-family="system-ui, -apple-system, 'PingFang SC', sans-serif"
        text-anchor="middle" opacity="0.9">{label}</text>
  <text x="{w // 2}" y="{int(h * 0.52) + int(h * 0.055)}" fill="{text_fill}"
        font-size="{int(h * 0.024)}" font-family="ui-monospace, SFMono-Regular, monospace"
        text-anchor="middle" opacity="0.55">{job.id} · placeholder</text>
</svg>"""
    return svg.encode("utf-8")


async def render(
    job: ArtJob, story: Story, *, use_api: bool, refs: dict[str, Path], model: str | None = None
) -> Path:
    """画一张，落盘，返回实际路径（真素材 .png，占位图 .svg）。"""
    story.ensure()
    if not use_api:
        label = job.expression and f"{job.character} · {job.expression}" or job.id
        out = story.asset(f"{job.id}.svg")
        out.write_bytes(placeholder_svg(job, label=label))
        return out

    ref_images = [
        image_api.to_data_uri(p.read_bytes())
        for rid in job.refs
        if (p := refs.get(rid)) is not None and p.suffix == ".png"
    ] or None
    png = await image_api.generate_png(
        job.prompt, kind=job.kind, seed=job.seed(), ref_images=ref_images, model=model
    )
    if job.kind == "sprite":
        # 绿幕原图先留一份再抠：抠完绿就没了，以后调算法只能靠它，
        # 否则改一次去背就要把所有立绘重新花钱生一遍
        story.raw.mkdir(parents=True, exist_ok=True)
        (story.raw / job.filename).write_bytes(png)
        png = image_api.cutout(png)
    out = story.asset(job.filename)
    out.write_bytes(png)
    # 真素材落地，同名占位图就不该再留着
    story.asset(f"{job.id}.svg").unlink(missing_ok=True)
    return out


def recut_all(story: Story, *, verbose: bool = True) -> int:
    """拿 raw/ 里的绿幕原图把所有立绘重抠一遍。改了去背算法之后走这个，不花钱。"""
    if not story.raw.is_dir():
        if verbose:
            print(f"[recut] {story.name} 没有 raw/，这些立绘是换绿幕之前生成的，只能重画")
        return 0
    n = 0
    for src in sorted(story.raw.glob("sprite_*.png")):
        original = src.read_bytes()
        png = image_api.cutout(original)
        story.asset(src.name).write_bytes(png)
        n += 1
        if verbose:
            print(f"  ✓ {src.stem}  绿残留 {image_api.green_residue(png, original):.2%}")
    if verbose:
        print(f"[recut] {story.name} 重抠了 {n} 张")
    return n


def existing(story: Story, job: ArtJob) -> Path | None:
    for suffix in (".png", ".svg", ".jpg", ".webp"):
        p = story.asset(f"{job.id}{suffix}")
        if p.exists():
            return p
    return None


async def run(
    story: Story,
    *,
    only: set[Kind] | None = None,
    force: bool = False,
    dry_run: bool = False,
    concurrency: int = 4,
    model: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    jobs = [j for j in build_plan(story) if not only or j.kind in only]
    use_api = image_api.has_credentials()

    def stale(job: ArtJob) -> bool:
        """占位图是个待补的洞，不是完成品——有生成端的时候要顶掉它。"""
        p = existing(story, job)
        return p is None or (use_api and p.suffix.lower() == ".svg")

    todo = [j for j in jobs if force or stale(j)]

    if verbose:
        active = model or image_api.ark_config()["image_model"]
        cost = (
            f"，约 {pricing.fmt(len(todo) * pricing.image_unit(active))}（{active}）"
            if use_api
            else "，占位图不花钱"
        )
        print(f"[art_plan] 需要 {len(jobs)} 张，其中 {len(todo)} 张要生成{cost}")
        if not use_api:
            print(f"[art_plan] 没有 {image_api.credential_hint()}，本段退回占位图")
    if dry_run:
        return {"planned": len(jobs), "todo": len(todo), "generated": 0, "api": use_api}

    manifest_path = story.asset("prompts.json")
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    refs: dict[str, Path] = {}
    for job in jobs:
        if (p := existing(story, job)) is not None:
            refs[job.id] = p

    # 分两拨：先画不依赖别人的（背景 / CG / 每个角色的 normal），
    # 再画拿 normal 当参考图的表情图。拨内并发，拨间串行——依赖顺序不能乱。
    def ready(job: ArtJob) -> bool:
        return all(refs.get(r, Path(".svg")).suffix == ".png" for r in job.refs)

    gate = asyncio.Semaphore(concurrency)
    done = 0

    failed: list[tuple[str, str]] = []

    async def one(job: ArtJob) -> tuple[ArtJob, Path | None]:
        async with gate:
            try:
                return job, await render(job, story, use_api=use_api, refs=refs, model=model)
            except Exception as exc:  # noqa: BLE001
                # 这一段是花钱的：一张图挂掉绝不能拖垮整批，否则已经画好的那些
                # 会跟着丢，重跑又是一笔钱。所以什么异常都接住，记下来最后一起报，
                # 剩下的照画。没画成的那几张下次重跑时自然还在待办里（命名即缓存）。
                failed.append((job.id, f"{type(exc).__name__}: {exc}"))
                return job, None

    # 一拨一拨往前推：每拨只做「参考图已经就位」的，拨内并发。
    # 依赖链有三层（风格锚 → 各角色 normal → 各表情），所以不能写死拨数。
    pending = list(todo)
    while pending:
        wave = [j for j in pending if ready(j)]
        if not wave:
            # 剩下的都在等一张没画成的参考图，直接画，大不了少个参考
            wave = list(pending)
        pending = [j for j in pending if j not in wave]
        for coro in asyncio.as_completed([one(j) for j in wave]):
            job, path = await coro
            if path is None:
                continue
            refs[job.id] = path
            manifest[job.id] = {
                "kind": job.kind,
                "file": path.name,
                "prompt": job.prompt,
                "seed": job.seed(),
                "source": "ark" if use_api else "placeholder",
                "model": (model or image_api.ark_config()["image_model"]) if use_api else "",
                **(
                    {"character": job.character, "expression": job.expression}
                    if job.character
                    else {}
                ),
                **({"refs": job.refs} if job.refs else {}),
            }
            done += 1
            if verbose:
                print(f"  ✓ [{job.kind}] {path.name}  ({done}/{len(todo)})")
    if failed and verbose:
        print(f"\n[art] {len(todo) - len(failed)} 张成功，{len(failed)} 张没画成：")
        for aid, why in failed:
            print(f"  ✗ {aid}  {why[:140]}")
        print("[art] 再跑一次 --only <类别> 就会只补这几张（已经画好的命中缓存）")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "planned": len(jobs),
        "todo": len(todo),
        "generated": done,
        "blocked": [aid for aid, _ in failed],
        "api": use_api,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="s3_gen_art")
    ap.add_argument("--name", required=True)
    ap.add_argument("--only", action="append", choices=list(KINDS), default=[])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-j", "--concurrency", type=int, default=4, help="并发张数")
    ap.add_argument("--recut", action="store_true", help="不生图，拿 raw/ 的原图重抠一遍")
    ap.add_argument("--model", default=None, help="覆盖 config 里的 image_model")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    story = Story(args.name)
    if not story.exists():
        print(f"没有这部小说：{story.dir}", file=sys.stderr)
        return 1
    if args.recut:
        recut_all(story)
        return 0
    result = asyncio.run(
        run(
            story,
            only=set(args.only) or None,
            force=args.force,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
            model=args.model,
        )
    )
    # 有没画成的就给非零退出码：旧文件还在，不喊的话看起来和成功一模一样
    return 1 if result.get("blocked") else 0


if __name__ == "__main__":
    raise SystemExit(main())
