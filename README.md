# Rinarino

视觉互动小说：一句设定进去，一个能玩的分支剧情出来。

```bash
uv sync
uv run uvicorn backend.app.main:app --reload --port 8811
# → http://127.0.0.1:8811
```

左栏选小说，中栏播，右栏是横向分支图和角色页（走过的路径高亮，
每张立绘下面挂着它真正用过的 prompt）。

## 布局

```
util/           与业务无关的 transport：deepseek / Seedream / Seedance / 目录约定
vn_workflow/    生成端：一句设定 → 素材，只往 vn/stories/<名字>/ 写文件
vn/             产物：素材 + 编译好的 story.json，外加 dist/<名字>.html
backend/        FastAPI：把产物端给前端，一行都不 import 生成端
frontend/       原生 JS，无构建步骤
                engine/ 公用播放端（引擎 + 播放器 + 播放区样式），单文件页也用这一份
                js/ css/ 宿主 WebUI：小说列表 + 分支图 + 角色页
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
