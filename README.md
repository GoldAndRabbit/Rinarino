# Rinarino

视觉互动小说：一句设定进去，一个能玩的分支剧情出来。

```bash
uv sync
uv run uvicorn backend.app.main:app --reload --port 8811
# → http://127.0.0.1:8811
```

左栏选小说，中栏播，右栏是调试面板，两排导航：

- **剧情** —— 主线剧情图（走过的路径高亮，点结点摊开「这一幕」：进场立绘、逐步演出、
  交互、每个选项的去向和改的变量，能直接跳过去播）/ 场景素材 / 事件 CG
- **角色** —— 全片画风 prompt、设定表、立绘网格、出场的事件 CG

面板里的图全部悬停放大，每张都挂着它真正用过的生成 prompt。当前视图写在地址栏里
（`#story=…&tab=…&node=…`），刷新不丢。

## 两种玩法

- **分支剧情**（默认）—— 视觉小说：台词、立绘、选项、好感度，剧本是 ink 编译出来的 `story.json`
- **探索解谜**（`"engine": "explore"`）—— 参照《Stanley 博士的家》的密室逃脱：
  在房子里走动、调查、把道具用在对的地方、输密码开门。剧情是一张**有状态的图**，
  编剧只写 JSON（Node / Condition / Effect / Event / State 五个概念），
  引擎自带 `validate()` 查剧本、`solve()` 证明每个结局都解得开。工作流在 `vn_workflow_v2/`。
  示例是原创的《十一点四十七分》，和《Stanley博士的家》第一代的复刻（仅供本地研究，别公开发布），
  写法见 [docs/explore.md](docs/explore.md)

两种玩法共用宿主、素材管线、调试面板和打包，调试面板按玩法各画各的（剧情图 / 状态图）。

## 布局

```
util/           与业务无关的 transport：deepseek / Seedream / Seedance / 目录约定
vn_workflow/    生成端：一句设定 → 素材，只往 vn/stories/<名字>/ 写文件
vn_workflow_v2/ 探索解谜的工作流：lint（validate + solve）→ art → build，产物同样落在 vn/stories/
vn/             产物：素材 + 编译好的 story.json，外加 dist/<名字>.html
backend/        FastAPI：把产物端给前端，一行都不 import 生成端
frontend/       原生 JS，无构建步骤
                engine/ 分支剧情的播放端（引擎 + 播放器 + 播放区样式），单文件页也用这一份
                explore/ 探索解谜的播放端，同上
                js/ css/ 宿主 WebUI：小说列表 + 调试面板（剧情图 / 这一幕 / 素材 / 角色）
config/         llm_api.yaml —— 文本 / 生图 / 视频三个 provider 的配置
docs/           vn_workflow.md —— 流水线六段的详细说明
```

## 生成一部新的

```bash
uv run python -m vn_workflow.pipeline --name island --shape survival \
    --brief "四个陌生人在一场船难后醒在同一座无人岛上…"
```

详见 [docs/vn_workflow.md](docs/vn_workflow.md)。

## 鉴权

```bash
ALIYUN_BAILIAN_API_KEY=...   # 文本：deepseek，经百炼兼容模式
ARK_API_KEY=...              # 生图 + 视频：火山引擎方舟
                             # 别名也认：ARK_VOLCENGINE_API_KEY / VOLCENGINE_API_KEY
```

去哪儿找，按顺序，先到先得（真实环境变量始终优先）：

```
$RINARINO_ENV_FILE  →  ./.env  →  ../pitchasso/.env  →  ../uhwoo/.env
```

邻仓那两条是**只读的借用**：key 不复制一份到本仓，源头轮换了这边跟着变。
这张表在 `config/llm_api.yaml` 的 `env_files` 里，想改就改。
`util.llm_api.key_source("ARK_API_KEY")` 能查某把 key 是从哪儿来的（只报出处，不报值）。

没配也能跑：素材退回占位图和合成音，全链路照样通到底。配上 key 之后跑一次
`--only art` 就会把占位图顶掉换成真素材——**占位图算待补的洞，不算完成品**，
所以这里不需要 `--force`。

## 开发

```bash
uv run pytest            # 33 项：编译器、闸门、流水线、后端、前端运行时
uv run ruff check .
```
