# vn_workflow

一句设定进去，一个能玩的分支剧情出来。六段流水线，每段都带缓存，中途断了直接重跑。

```
1 story  →  2 cast  →  3 art        →  4 video  →  5 amb  →  6 build
剧本        设定表      背景/CG/立绘     过场视频     环境声     打包
```

```bash
# 从零起一个故事
uv run python -m vn_workflow.pipeline --name island --shape survival \
    --brief "四个陌生人在一场船难后醒在同一座无人岛上…"

# 只重跑某一类素材
uv run python -m vn_workflow.pipeline --name island --only sprite --force

# 只验不生成
uv run python -m vn_workflow.s1_lint_ink --name island
uv run python -m vn_workflow.s3_lint_art --name island
```

`--only` 可选：六段之外还有 `music`（和 `amb` 二选一）、`bg` / `est` / `cg` / `sprite`
（第 3 段的四类素材分开重跑）、`artlint`（只验不生成）。
`--keep-meta` 重跑 cast 时不覆盖界面文案。

开跑前会把账报出来，免得 `--force` 刷掉了什么心里没数：

```
[art_plan] 需要 33 张，其中 33 张要生成，约 ¥4.62
[video]    需要 1 条，其中 1 条要生成，约 ¥2.40
```

---

## 目录

```
util/                    与业务无关的 transport，三层都能引用
  llm_api.py             文本：deepseek（OpenAI 兼容，默认经百炼）
  image_api.py           生图：火山引擎方舟 Seedream + 立绘去背
  video_api.py           视频：火山引擎方舟 Seedance（异步任务制）
  audio_synth.py         占位音轨的程序化合成（只用标准库）
  paths.py               vn/stories/ 的目录约定

vn_workflow/
  pipeline.py            主控：调度、缓存、闸门、回灌重试
  inklite/               ink 子集的编译器与闸门
  s1_gen_story.py        第 1 段：模型写 ink
  s1_lint_ink.py         第 1 段闸门：语法 / 死节点 / 孤节点 / 成环 / 选项数
  s2_gen_cast.py         第 2 段：外观设定 + 界面文案
  s3_gen_art.py          第 3 段：背景 / 定场图 / 事件 CG / 立绘
  s3_lint_art.py         第 3 段闸门：画幅 / 亮度 / 去背 / 同人一致性
  s4_gen_video.py        第 4 段：过场视频
  s5_gen_ambience.py     第 5 段：环境声
  s5_gen_music.py        第 5 段备选：BGM
  s6_build.py            第 6 段：打包成不依赖后端的 HTML
  prompts_template/      提示词模板（{{变量}} 占位 + vars.yaml 变量表）
  shapes/                剧情形态规格
```

`s<段号>_` 跟 `--only` 的参数名对得上；`gen_` 是生成，`lint_` 是闸门。

## 三层单向依赖

```
vn_workflow/    生成端：一句设定 → 素材
  ↓ 只往 vn/stories/<名字>/ 写文件
vn/             产物：素材 + 编译好的 story.json
  ↓ 产物 dist/<名字>.html
backend/ frontend/   宿主：上架、播放、右栏横向分支图和角色页
```

`backend/` 和 `frontend/` 一行都不 import 生成端。把整个 `vn_workflow/` 删掉，
故事照样能玩——它们共用的只有 `util/paths.py` 那份目录约定。

交接点只有一个目录：

```
vn/stories/americano/
  brief.txt     一句设定（形态 + 设定）
  story.ink     剧本 ← 唯一的真源
  story.json    编译产物：播放端只认这个
  cast.json     外观设定（art_direction / 结构化 desc / cgs / videos）
  meta.json     界面文案（标题 / 变量 label / 场景名 / 结局说明）
  amb.json      环境声与 BGM 的 prompt
  assets/       bg_* cg_* 立绘 amb_* video_* + prompts.json（每张图真正用过的 prompt）
```

---

## 第 1 段 story：剧本

模型写 ink，**两道确定性闸门验，不过就回灌重写**，最多 4 次。

选 ink 而不是「让模型写一段散文」，唯一的理由就是**产物有编译器**。模型写 ink 会犯的错
是有限几类——语法错、某个结点只给了 1 个选项、结局写了但走不到、结点之间成环——
这几类全部可以由程序判定，而且判定结果（第几行、哪个结点）可以原样回灌给模型重写。
这是整条流水线里唯一不需要人看的剧本质检。

剧情形态可插拔：`shapes/*.md` 一个文件一种玩法。目前有三种：

| 形态 | 图的形状 | 变量 |
| --- | --- | --- |
| `romance_branch` | 共同开场 → 三条人物线各走到底 → 每线两个结局 | 一人一个好感值 |
| `mystery` | 现场三选一勘查 → 依次讯问 → 指认三选一 → 按证据分层 | 只有一个 `clue` |
| `survival` | 分歧分叉 → 结算 → 按信任与补给分层 | `trust` / `supply` |

形态不同，连**闸门该查什么**都不同：恋爱形态里「每个 VAR 对应一个角色」是硬规矩，
推理形态里只有一个 clue，照那条查会把好剧本判成错的（这条我一开始就写错了）。
所以 s2 的覆盖检查只查真正的不变式：**剧本里摆上台的人，设定表里都得有**。

### ink 子集

只实现视觉小说真正用得到的那一小撮语法，写出范围就编译不过：

```ink
# title: 最后一卷胶片

VAR zhao = 0                     // 变量只能声明在第一个结点之前

=== opening ===                  // 结点
# node: 开场                      // 分支图上的名字
# bg: bg_street_dusk             // 进场指令：bg / cg / sprite / amb / music / video
# sprite: zhao:cold, zhi:laugh
旁白不带前缀
陆昭: 我来拿我的底片。            // 名字和 cast.json 逐字一致才认得出说话人
~ zhao += 1                      // 赋值，只有 = += -=
{zhao >= 3: -> end_zhao_true}    // 条件跳转，必须写在选项前面
* [把陆昭叫住，问她图纸的事]        // 选项
    ~ zhao += 1                  // 选项体：缩进，可含台词和赋值
    -> zhao_1                    // 选项必须有落点
-> END                           // 没有选项的结点用直接跳转；结局用 -> END
```

编译产物是一张显式的图，于是这些全能纯静态判掉：

| 判定 | 说的是 |
| --- | --- |
| `syntax` | 编译不过，带行号和原文 |
| `dead-link` | 跳向一个不存在的结点 |
| `orphan` | 从起点走不到这个结点 |
| `cycle` | 无选项的跳转成环，玩家会卡死 |
| `no-exit` | 既没有选项也没有跳转 |
| `no-fallback` | 只有条件跳转，条件都不成立时无路可走 |
| `too-few-choices` | 分支剧情里只给了 1 个选项 |
| `no-ending` | 没有任何可达的 `-> END` |
| `unplayable` | 图上连着，但没有任何一组选择能走到——阈值和变量能攒到的范围对不上 |
| `undeclared-var` | 条件里读了没声明的变量 |

`unplayable` 是把所有选项组合真的走一遍算出来的（变量一起算），不是看图上有没有边。
`{好感 >= 3: -> 真结局}` 这种，如果任何路径都攒不出低于 3 的好感，擦肩结局就是死的——
边在图上连着，玩家却永远看不到。《三杯冰美式》第一版就踩了这个，两个结局是死的。

## 第 2 段 cast：设定表

读剧本，产出 `cast.json`（给生图看）和 `meta.json`（给人看）。分两个文件是因为
界面文案会被反复手改，所以 `--keep-meta` 重跑 cast 时不覆盖它。

这一段也有闸门，判的是**覆盖关系**：`backgrounds` / `cgs` 的 key 必须覆盖且只覆盖剧本里
`# bg:` `# cg:` 出现过的 id，`characters[].key` 必须和 VAR 一一对应，`desc` 的八个字段
（身份 / 面部 / 四肢 / 上衣 / 下装 / 鞋 / 配饰 / 气质）一个都不能少。不对就回灌重写。

## 第 2.5 段 restyle：只换画风

改了 `vars.yaml` 的 `house_style` 之后，已有的作品不会跟着变——`cast.json` 是产物。
但**不要**用 `--only cast --force` 去追：`cast.json` 里同时住着画风和这部作品本身
（角色长相、表情动作、背景与 CG 的分镜），`--force` 会把两样一起重写。实测重跑一次，
角色换了张脸、站姿变成坐姿、「三人同桌」被改写成「桌边无人」——那不是换画风，
是换了部作品。

`restyle` 只动 `art_direction`：`style` 拿 `house_style` 逐字覆盖，`avoid` 并进
反向画风词（旧的一条不删——那些是这部作品自己的忌讳）。它是**点名才跑**的一段，
全量流程里不出现，否则每次重跑都会把手改过的 `style` 冲掉。

```bash
uv run python -m vn_workflow.pipeline --name americano --only restyle --only art --force
```

## 第 3 段 art：素材

四类素材分开重跑，命名即缓存：`assets/<id>.png` 存在就跳过，`--force` 才重画。
每张图**真正用过的 prompt** 落在 `assets/prompts.json` 里，所以重跑一张和当初那张
是同一套输入（连 seed 都是从 id 算出来的，不随机）。

人物一致性靠三件事，**立绘和 CG 都要走**：

1. 同一个角色在任何画面里都共享一份「外观段」——`cast.json` 的 `desc` 逐字段拼进 prompt
2. **第一个角色的 `normal` 立绘是全局风格锚**：它先画，之后每个角色的 `normal` 都拿它
   当 `reference_image`。不这么做的话三个角色各画各的，很容易一个出成照片写实、
   另两个出成插画，摆进同一张 CG 就穿帮
3. 每个角色的表情图再参考本角色自己的 `normal`

于是依赖链是三层——**风格锚 → 各角色 normal → 各表情**——排程按拓扑一拨一拨推，
拨内并发，不能写死拨数。

项目默认的取向是**写实 + 精致**（照片级写实、电影剧照质感），一句话写在
`prompts_template/vars.yaml` 的 `house_style` 里，改它等于一次性改掉所有新作品。

`style` 里要**把风格化程度钉死**，而不是只写「写实」两个字——模型对它反应很弱。
对「鼻子画不画鼻孔」「皮肤画不画毛孔」「睫毛是成组的粗线还是一根根」这种具体指令
有反应，对「精美」「高质量」没有。写实这一侧同样要躲开反向指令：prompt 里任何地方
留着「线稿」「厚涂」「场景插画」，都会把整张图拽回二次元，所以 `s3` 拼骨架时
用的是「写实场景」「没有线稿和描边」。

把人往写实推的词（「皮肤有毛孔」「有细纹」）要**统一写在 `house_style` 里**，
别零散写进个别角色的 `desc`：一个角色写了、别人没写，这个角色就会单独飘出去。
这条和取向无关——飘出去的从来不是写实本身，是不一致。

景深不进 `house_style`，按图的种类分开给：场景浅景深，立绘从头到脚全部合焦。
全局给一句浅景深，糊掉的是立绘的手和脚。

`expressions` 每一项分「表情」和「动作」两段。只给表情的话，同一个角色的六张立绘
会是同一个姿势换六张脸，看着很廉价；动作单独给，模型才会改重心、手臂和身体朝向。
（旧写法——直接写一句话——仍然认，只是没有动作变化。）

CG 和定场图会被扫一遍：描述里点了名的角色，外观段一并拼进 prompt，并把那几个角色的
`normal` 立绘一起当参考图。只写一句「林晚抱着一本书靠着书架坐在地上」是不够的——
模型会自由发挥出另一个人，正片和立绘就成了两个角色。所以 `s3` 的排程分两拨：
先画不依赖别人的（背景 / 每个角色的 normal），再画要拿它们当参考图的（表情图 / 有人的 CG）。

Seedream 不出 alpha，所以立绘一律让它画在纯白底上，再用**边缘连通域** flood fill 抠背——
只做连通域是关键：人物身上的白衬衫不挨着画面边缘，不会被一起抠掉。

纯白底这件事在写实取向下会被反咬一口，有两条是踩过坑才定下来的：

- **立绘不拼 `lighting`**。`lighting` 描述的是**场景**的光（「咖啡馆 2700K 暖黄 + 窗外
  5500K 散射冷光」）。二次元取向下它只是气氛词；写实取向下模型会当真去把那个房间搭出来。
  实测一张立绘被画成了带窗户和公文包的办公室实景，边缘一点白都没有，flood fill 直接抠不动，
  `s3_lint` 报「边缘只有 0% 是透明的」。
- **只说「纯白背景」不够**，模型会把它理解成「一间白墙的房子」。要把拍法整个指定死：
  影棚棚拍、纯白 #FFFFFF 无缝背景纸、背景完全过曝为纯白、画面里没有房间 / 家具 / 窗户 /
  街景 / 前景遮挡物。

去背坏掉的图**不必整部重跑**：命名即缓存，删掉那几个 png 再跑一次 `--only sprite`
（不加 `--force`）就只补这几张。

## 第 4 段 video：过场视频

火山引擎 Seedance，异步任务制（提交拿 task id，再轮询）。视频比别的贵得多，
所以缓存最严，而且默认拿已经生成好的背景 / CG 当参考图——过场和正片的画风必须是
同一套，靠文字描述对不齐，靠参考图才对得齐。

## 第 5 段 amb / music：声音

`amb.json` 里每个 id 一段 prompt，那是给音频模型看的真源。二选一：一部作品要么靠
环境声撑气氛，要么给它写曲子，两样都堆上去会打架。

## 第 6 段 build：打包

产物 `vn/dist/<名字>.html`，双击就能玩，不需要后端也不需要联网。`--inline` 把素材
也塞进 HTML，换来单文件可分发。内联的播放端就是 `frontend/engine/` 那一份源码
（`engine.js` 剧情引擎 + `player.js` 播放器 + `play.css` 播放区样式），
和宿主 WebUI 用的是同一份，不另写一套。单文件页自己只多出一层壳：标题、按钮、外框。

---

## 没配 key 的时候

| 段 | 没 key 的行为 |
| --- | --- |
| 1 story / 2 cast | 跑不了（需要 `ALIYUN_BAILIAN_API_KEY`） |
| 3 art | 退回占位图（SVG），文件名 / 清单 / prompts.json 全都照常落盘 |
| 4 video | 整段跳过，播放端遇到缺失的 video 直接不放 |
| 5 amb / music | 程序化合成一段能无缝循环的占位音 |
| 6 build | 照常 |

没有 key 的时候 `americano` 也是**能完整播完的**，只是素材是占位的。

占位图（`.svg`）算**待补的洞，不算完成品**：一旦有了生成端，`--only art` 会自动把它们
顶掉换成真的 `.png`，不需要 `--force`；真素材落地后同名的占位图会被删掉。已经是真素材的
那些则照常命中缓存——所以中途断了重跑，只会补没画完的那几张。

### key 去哪儿找

```
$RINARINO_ENV_FILE  →  ./.env  →  ../pitchasso/.env  →  ../uhwoo/.env
```

按顺序，先到先得；真实环境变量始终优先。邻仓那两条是**只读的借用**——key 不复制一份
到本仓，源头轮换了这边跟着变。这张表在 `config/llm_api.yaml` 的 `env_files` 里。
`ARK_API_KEY` 另认两个别名：`ARK_VOLCENGINE_API_KEY` / `VOLCENGINE_API_KEY`。
