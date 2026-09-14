# 探索解谜玩法：剧本怎么写

第二种玩法，参照《Stanley 博士的家》那一类密室逃脱：在一栋房子里走来走去，
调查、捡东西、把道具用在对的地方、找到密码开门，最后揭开真相。

一句话分工：**HTML/CSS 负责呈现，JS 负责运行剧情，JSON 负责存储剧本，State 负责记录玩家状态。**

```
frontend/explore/engine.js   引擎：纯状态机，不碰 DOM；validate() 查剧本，solve() 证明解得开
frontend/explore/player.js   屏幕：画面、台词、行动、背包
frontend/explore/play.css    屏幕样式
frontend/js/explore-panel.js 调试面板：状态图、State、可解性、单个结点的全部规则
vn/stories/<名字>/story.json 剧本（写着 "engine": "explore" 的就走这一套）
vn_workflow_v2/              工作流：lint（validate + solve）→ art（背景 / CG）→ build（单文件页）
```

宿主 WebUI、`vn_workflow_v2` 打包出来的单文件页、静态站用的都是同一份引擎，编剧只写 `story.json`。

示例两部：原创的《十一点四十七分》（`vn/stories/clockhouse`），和《Stanley博士的家》第一代的复刻
（`vn/stories/stanley`）。复刻还原了原作的房间、谜题链和剧情结构，文字是重写的；
原作版权归 James Li（雪夜公爵），复刻只用来在本地验证引擎，**不要公开发布**。

## 剧情不是树，是有状态的图

```
            [门廊]
              │
            [前厅] ─────────┬──────────┬──────────┐
           /    \          │          │          │
      [书房门]  [厨房]     [二楼]     [矮门]      大钟
    密码 1147     │          │      齿轮钥匙      发条钥匙
        │     [后花园]    [卧室]       │
      [书房]  猎犬睡着?    铜钥匙→衣柜 [地下室]
     铜钥匙→抽屉  │                   火柴→点亮
               [温室]
```

结点随便连，谁依赖谁全靠 **State + Condition** 表达。「书房要密码、密码的线索在卧室日记里、
去温室要先让狗睡着、安眠药在卧室而肉在厨房」——这些依赖不需要把路径各复制一遍，
所以不会长出几百层嵌套的剧情树。

## 五个概念

| 概念 | 职责 | 写法 |
|---|---|---|
| **Node** | 一个地点或一段场面 | `{id, kind, label, bg, text, enter, first, choices}` |
| **Condition** | 现在能不能发生 | `{key, operator, value}` · `{has}` · `{visited}` · `{done}` · `{all}` `{any}` `{not}` |
| **Effect** | 发生以后世界变成什么样 | `set` · `inc` · `add_item` · `remove_item` · `stash` · `unstash` |
| **Event** | 具体执行什么动作 | `narrate` · `say` · `bg` · `cg` · `music` · `sfx` · `toast` |
| **State** | 玩家现在是什么情况 | flags · 背包 · 去过哪 · 做过什么 |

**Effect 和 Event 是分开的**：Effect 改的东西进 State——能回退、能被条件读到；
Event 只管呈现，放完就没了，回退时也不会重放。一个块 `{effects, events}` 先改世界再演出来。

## 一个行动长这样

```json
{
  "id": "open_basement",
  "kind": "use",
  "use": "teal_key",
  "consume": true,
  "once": true,
  "text": "齿轮形状的锁孔",
  "effects": [{ "type": "set", "key": "basement_open", "value": true }],
  "events": [{ "type": "narrate", "text": "锁开了。一股机油味从下面涌上来。" }],
  "next": "basement"
}
```

行动分四种，对应密室逃脱的四个手势：

| kind | 玩家做什么 | 额外字段 |
|---|---|---|
| `go` | 前往另一个结点（有 `next` 时的默认值） | — |
| `act` | 调查一处：看细节、拿东西（没 `next` 时的默认值） | — |
| `use` | 从背包里拿出道具，用在这里 | `use` 要的道具 · `consume` 用掉 · `fail_text` 用错时说什么 · `hint` 空手时说什么 |
| `code` | 输入密码 | `code` 正确答案 · `prompt` 输入框提示 · `fail` 输错时的块 |

通用字段：`condition` 条件 · `hidden` 条件不满足时整个藏起来（默认是灰着显示并给出原因）·
`locked_text` 锁着时的说法 · `once` 做过就消失。

## 几条设计规矩

- **用错道具绝不消耗。** 引擎里写死了：`use` 的输入不对只会说「没有反应」。
  消耗掉的话，玩家可能卡死在再也解不开的局面里。
- **锁着的门要让人看见。** 默认不藏，灰着并写明缺什么（「猎犬挡在温室门口」）；
  只有玩家还不该知道有这回事的（地下室点亮之前的「工作台」）才标 `hidden`。
- **描述和背景可以随状态变**：`text` 和 `bg` 都能写成 `[{condition, …}, …, {…}]`，取第一条满足的。
  地下室点灯前后是两句话、两张图。
- **没设过的 flag 不用写初值**：和布尔比按 `false` 算，和数字比按 `0` 算。
- **一次性的东西写 `once`**，别靠 flag 手动藏：可解性搜索靠它判断「这个行动还在不在」。

## 道具与配方

```json
"items": {
  "diary": {
    "name": "日记",
    "desc": "顾博士的日记，前面大半本记的都是齿轮和发条的尺寸。",
    "examine": {
      "effects": [{ "type": "set", "key": "diary_read", "value": true }],
      "events": [{ "type": "narrate", "text": "最后一页只有一行字：「钟停在哪一刻，门就开在哪一刻。」" }]
    }
  }
},
"recipes": [
  { "items": ["meat", "pills"], "result": "drugged_meat", "events": [...] }
]
```

在背包里「检查」道具会念 `desc`，再执行 `examine` 块（可以带 `condition`：
知道真相之后再看邀请函，看出来的东西不一样）。两件道具「组合」按 `recipes` 匹配，
材料默认用掉，`keep` 里列的留下。

## 东西被搜走：stash / unstash

```json
{ "type": "stash", "name": "toolbox" }     // 背包整个收进「toolbox」，玩家两手空空
{ "type": "unstash", "name": "toolbox" }   // 原样拿回来
```

Stanley 第一代的蓝色房间就是这个：一开门被打晕，醒来东西全没了，得去粉色里屋的工具箱里找回来。
只用 `remove_item` 写不出来——被搜走的是**当时背包里有什么**，剧本写的时候并不知道。

## 写完之后：validate 和 solve

调试面板的状态图下面有两张卡，也是 `tests/test_explore_js.py` 在跑的东西：

- **validate**：引用了不存在的结点或道具、拼错的 type、从起点走不到的结点、哪儿都拿不到的道具。
  运行时引擎对这些一律宽容（崩掉就是玩家卡死），所以必须在加载时拦住。
- **solve**：在状态空间里做广度优先搜索——用对的道具、输对的密码、检查每样东西、组合每个配方——
  证明**每个结局都走得到**，并给出最短路径。道具被用掉、门再也开不了这种卡死，在这儿就会暴露。

  去重只看「会影响以后还能发生什么」的那部分状态：flags、背包、被条件读到的「去过哪」、
  `once` 或被条件读到的「做过什么」。走过哪几个房间这种历史全放进去的话，
  同一个局面会因为来路不同被当成几万个状态——第一版就是这么搜不完的。

  来回走路也不一步一步搜：从当前位置顺着「不改状态的路」（没有 effect 的 go、进门不改状态的房间）
  能走到的所有房间算一个区域，搜索的一步是「走到区域里某个房间，做一件会改状态的事」。
  所以最短路径数的是**真正做了几件事**，不含来回走路；房间一多，状态数能差上百倍。

  纯拾取（只往背包里加东西、没有条件读「没有这样东西」、去得了也回得来）早做晚做都一样，
  一出现就直接捡掉，不当成分支。改 flag 的不算：检查旧照片会关掉「修好了」那个结局。

  实测：《十一点四十七分》276 个状态 0.1 秒，Stanley 复刻 16005 个状态约 7 秒。
  调试面板里的可解性放在 Web Worker 里算，先显示「计算中」，算完再补上，不卡页面。

## 工作流：vn_workflow_v2

```bash
uv run python -m vn_workflow_v2.lint --name stanley --path    # 闸门，--path 把每个结局的最短路径打出来
uv run python -m vn_workflow_v2.pipeline --name stanley       # lint → art → build
uv run python -m vn_workflow_v2.pipeline --name stanley --only art --dry-run   # 只报账
```

lint 不过，后面的段一律不跑：解不开的剧本配上美术也是白花钱。art 直接复用 `vn_workflow` 第 3 段，
build 和第 6 段共用页面壳和素材编码。`vn_workflow` 的 build 遇到探索剧本会直接报错，不会悄悄打出一个播不了的页面。

## 美术

背景 id 写在结点的 `bg` 和 Event 里，描述写进 `cast.json` 的 `backgrounds` / `cgs`，
和剧情玩法共用第 3 段的生图管线。没画的地方屏幕会用文字占位，玩法照样能从头玩到尾，不用等美术。
