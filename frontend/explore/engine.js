// 探索解谜引擎：剧情是一张**有状态的图**，不是树。纯状态机，不碰 DOM——
// 宿主 WebUI、打包出来的单文件页、测试共用这一份。
//
// 剧本 JSON 里只有五个概念，引擎也只认这五个：
//
//   Node       一个地点或一段场面      前厅、地下室门口、结局
//   Condition  现在能不能发生         {key, operator, value} · {has} · {visited} · {done} · {all|any|not}
//   Effect     发生以后世界变成什么样   set · inc · add_item · remove_item
//   Event      具体执行什么动作        narrate · say · bg · cg · music · sfx · toast
//   State      玩家现在是什么情况      flags · 背包 · 去过哪 · 做过什么
//
// 结点之间随便连，谁依赖谁全靠 State + Condition 表达：「先找到密码、再拿到钥匙才能开
// 保险箱」不需要把两条路径各复制一遍，也就长不出几百层嵌套的剧情树。
//
// Effect 和 Event 分开是故意的：Effect 改的东西进 State——能回退、能被条件读到；
// Event 只管呈现（台词、换背景、音效），放完就没了，回退时也不会重放。
//
// 行动（choice）分四种，对应密室逃脱的四个手势：
//   go    前往另一个结点          act   调查一处（拿东西、看细节）
//   use   把手里的道具用在这里     code  输入密码
// 另外背包里的道具能「检查」（可能看出线索）和两两「组合」。

const OPS = {
  eq: (a, b) => a === b,
  ne: (a, b) => a !== b,
  gt: (a, b) => a > b,
  gte: (a, b) => a >= b,
  lt: (a, b) => a < b,
  lte: (a, b) => a <= b,
};
const OP_TEXT = { eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤' };

export const EFFECT_TYPES = ['set', 'inc', 'add_item', 'remove_item'];
export const EVENT_TYPES = ['narrate', 'say', 'bg', 'cg', 'music', 'sfx', 'toast'];
export const CHOICE_KINDS = ['go', 'act', 'use', 'code'];

const clone = (v) => JSON.parse(JSON.stringify(v));

export class ExploreEngine {
  constructor(story) {
    this.story = story;
    this.byId = new Map((story.nodes || []).map((n) => [n.id, n]));
    this.items = story.items || {};
    this.recipes = story.recipes || [];
    this.reset();
  }

  reset() {
    const init = this.story.state || {};
    this.state = {
      flags: { ...(init.flags || {}) },
      inventory: [...(init.inventory || [])],
      visited: [],
      done: [], // 成功做过的行动 id，条件 {done} 读它，once 的行动靠它藏起来
      attempts: {}, // 密码锁输错的次数
    };
    this.scene = { bg: null, cg: null, music: null };
    this.queue = []; // 待读的台词 {text, speaker?}：读完之前不能行动
    this.last = []; // 这一步触发的 Event。宿主拿去放音效、弹提示；每次改动换一个新数组
    this.history = [];
    this.current = null;
    this.ending = null;
    this.enter(this.story.start);
    return this;
  }

  // --- 读 -------------------------------------------------------------------

  get node() {
    return this.byId.get(this.current) || null;
  }

  get message() {
    return this.queue[0] || null;
  }

  get canNext() {
    return this.queue.length > 0;
  }

  /** 结局的话也读完了才算真正结束。 */
  get finished() {
    return Boolean(this.ending) && !this.queue.length;
  }

  get canPrev() {
    return this.history.length > 0;
  }

  itemName(id) {
    return this.items[id]?.name || id;
  }

  nodeLabel(id) {
    return this.byId.get(id)?.label || id;
  }

  choiceId(node, choice, index) {
    return choice.id || `${node.id}#${index}`;
  }

  /**
   * 当前能做的事。条件不满足的：标了 hidden 的藏起来（玩家还不该知道有这回事），
   * 其余灰着显示并给出原因（锁着的门要让人看见它锁着）。
   */
  get actions() {
    const node = this.node;
    if (!node || this.ending) return [];
    return (node.choices || [])
      .map((c, index) => {
        const id = this.choiceId(node, c, index);
        const ok = this.test(c.condition);
        return {
          ...c,
          id,
          index,
          kind: c.kind || (c.next ? 'go' : 'act'),
          locked: !ok,
          reason: ok ? '' : c.locked_text || this.explain(c.condition),
        };
      })
      .filter((c) => !(c.once && this.state.done.includes(c.id)))
      .filter((c) => !(c.locked && c.hidden));
  }

  // --- Condition ------------------------------------------------------------

  test(cond) {
    if (!cond) return true;
    if (Array.isArray(cond)) return cond.every((c) => this.test(c));
    if (cond.all) return cond.all.every((c) => this.test(c));
    if (cond.any) return cond.any.some((c) => this.test(c));
    if (cond.not) return !this.test(cond.not);
    if (cond.has) return this.state.inventory.includes(cond.has);
    if (cond.visited) return this.state.visited.includes(cond.visited);
    if (cond.done) return this.state.done.includes(cond.done);
    const op = OPS[cond.operator || 'eq'];
    if (!op) return false;
    const cur = this.state.flags[cond.key];
    // 没设过的 flag：和布尔比按 false 算，和数字比按 0 算——剧本里就不用给每个 flag 写初值
    const value = typeof cond.value === 'boolean' ? Boolean(cur) : cur ?? 0;
    return op(value, cond.value);
  }

  /** 把条件翻成人话：锁着的行动上显示它，调试面板也用它。 */
  explain(cond) {
    if (!cond) return '';
    const join = (list, word) => list.map((c) => this.explain(c)).join(word);
    if (Array.isArray(cond)) return join(cond, '，且');
    if (cond.all) return join(cond.all, '，且');
    if (cond.any) return join(cond.any, '，或');
    if (cond.not) {
      const inner = cond.not;
      if (inner.has) return `没有「${this.itemName(inner.has)}」`;
      return `不是（${this.explain(inner)}）`;
    }
    if (cond.has) return `需要「${this.itemName(cond.has)}」`;
    if (cond.visited) return `去过「${this.nodeLabel(cond.visited)}」`;
    if (cond.done) return `做过「${cond.done}」`;
    const label = this.story.flags?.[cond.key]?.label || cond.key;
    if (typeof cond.value === 'boolean' && (cond.operator || 'eq') === 'eq') {
      return cond.value ? label : `还没有${label}`;
    }
    return `${label} ${OP_TEXT[cond.operator || 'eq'] || cond.operator} ${JSON.stringify(cond.value)}`;
  }

  // --- Effect / Event -------------------------------------------------------

  apply(effects = []) {
    const s = this.state;
    for (const e of effects) {
      if (e.type === 'set') s.flags[e.key] = e.value;
      else if (e.type === 'inc') s.flags[e.key] = (Number(s.flags[e.key]) || 0) + (e.value ?? 1);
      else if (e.type === 'add_item') {
        if (!s.inventory.includes(e.item)) {
          s.inventory.push(e.item);
          this.emit({ type: 'toast', text: `获得「${this.itemName(e.item)}」` });
        }
      } else if (e.type === 'remove_item') s.inventory = s.inventory.filter((i) => i !== e.item);
      // 不认识的 type 在这里静默跳过，由 validate() 在加载时报出来——运行时崩掉就是玩家卡死
    }
  }

  emit(ev) {
    this.last.push(ev);
    if (ev.type === 'narrate') this.queue.push({ text: ev.text });
    else if (ev.type === 'say') this.queue.push({ text: ev.text, speaker: ev.speaker });
    else if (ev.type === 'bg') {
      this.scene.bg = ev.id;
      this.scene.cg = null;
    } else if (ev.type === 'cg') this.scene.cg = ev.id;
    else if (ev.type === 'music') this.scene.music = ev.id;
    // sfx / toast 没有状态，宿主从 last 里拿去演
  }

  /** 一个「块」= {effects, events}：先改世界，再演出来。 */
  run(block) {
    if (!block) return;
    this.apply(block.effects);
    for (const ev of block.events || []) this.emit(ev);
  }

  /** 结点的描述可以随状态变：[{condition, text}, …, {text}]，取第一条满足的。 */
  textOf(node) {
    if (!Array.isArray(node.text)) return node.text || '';
    return node.text.find((t) => this.test(t.condition))?.text || '';
  }

  /** 背景也能随状态变：[{condition, id}, …, {id}]（地下室点灯前后是两张图）。 */
  bgOf(node) {
    if (!Array.isArray(node.bg)) return node.bg || null;
    return node.bg.find((b) => this.test(b.condition))?.id || null;
  }

  enter(id) {
    const node = this.byId.get(id);
    if (!node) {
      this.ending = { id, title: '走进了一片空白', kind: 'broken', text: `剧本里没有「${id}」这个结点。` };
      return;
    }
    const first = !this.state.visited.includes(id);
    this.current = id;
    if (first) this.state.visited.push(id);
    this.scene.cg = null;
    const bg = this.bgOf(node);
    if (bg) this.scene.bg = bg;
    const text = this.textOf(node);
    if (text) this.queue.push({ text });
    this.run(node.enter);
    if (first) this.run(node.first);
    if (node.kind === 'ending') {
      this.ending = { id, title: node.ending?.title || node.label || id, kind: node.ending?.kind || 'ending' };
    }
  }

  // --- 写：每一个会改状态的操作都先压一份快照，所以都能「上一步」 ----------------

  begin() {
    this.history.push(this.snapshot());
    if (this.history.length > 400) this.history.shift();
    this.last = [];
  }

  next() {
    if (!this.canNext) return this;
    this.begin();
    this.queue.shift();
    return this;
  }

  /**
   * 做一个行动。code 类带上输入的密码，use 类带上手里拿的道具。
   * 返回 {ok, why}：why ∈ busy | missing | locked | wrong_code | wrong_item | no_item
   */
  choose(index, input = null) {
    if (this.canNext || this.ending) return { ok: false, why: 'busy' };
    const c = this.actions.find((a) => a.index === index);
    if (!c) return { ok: false, why: 'missing' };
    this.begin();
    if (c.locked) {
      this.emit({ type: 'narrate', text: c.locked_text || `现在还不行：${c.reason}。` });
      return { ok: false, why: 'locked' };
    }
    if (c.kind === 'code' && String(input ?? '').trim() !== String(c.code)) {
      this.state.attempts[c.id] = (this.state.attempts[c.id] || 0) + 1;
      this.run(c.fail || { events: [{ type: 'narrate', text: '不对。' }] });
      return { ok: false, why: 'wrong_code' };
    }
    if (c.kind === 'use') {
      if (!input || !this.state.inventory.includes(input)) {
        this.emit({ type: 'narrate', text: c.hint || '要用什么？先从背包里拿出一样东西。' });
        return { ok: false, why: 'no_item' };
      }
      if (input !== c.use) {
        // 用错东西只是没反应，**绝不**消耗道具——消耗了就可能把玩家卡死在解不开的局面里
        this.emit({ type: 'narrate', text: c.fail_text || `「${this.itemName(input)}」用在这里没有反应。` });
        return { ok: false, why: 'wrong_item' };
      }
      if (c.consume) this.apply([{ type: 'remove_item', item: c.use }]);
    }
    if (!this.state.done.includes(c.id)) this.state.done.push(c.id);
    this.run(c);
    if (c.next) this.enter(c.next);
    return { ok: true };
  }

  /** 检查背包里的一样东西。道具可以带 examine 块：看出线索、改 flag。 */
  examine(itemId) {
    if (this.canNext || this.ending || !this.state.inventory.includes(itemId)) return false;
    this.begin();
    const item = this.items[itemId] || {};
    this.queue.push({ text: item.desc || `「${this.itemName(itemId)}」。` });
    if (item.examine && this.test(item.examine.condition)) this.run(item.examine);
    return true;
  }

  /** 两样东西组合。配方在 story.recipes，材料默认用掉，keep 里列的留下。 */
  combine(a, b) {
    const inv = this.state.inventory;
    if (this.canNext || this.ending || a === b || !inv.includes(a) || !inv.includes(b)) return false;
    this.begin();
    const recipe = this.recipes.find((r) => r.items.length === 2 && r.items.includes(a) && r.items.includes(b));
    if (!recipe || !this.test(recipe.condition)) {
      this.emit({ type: 'narrate', text: `「${this.itemName(a)}」和「${this.itemName(b)}」凑不到一块。` });
      return false;
    }
    const keep = new Set(recipe.keep || []);
    this.apply(recipe.items.filter((i) => !keep.has(i)).map((item) => ({ type: 'remove_item', item })));
    this.apply([{ type: 'add_item', item: recipe.result }]);
    this.run(recipe);
    return true;
  }

  /** 调试用：不管条件，直接把玩家放进某个结点。状态保留，能「上一步」退回来。 */
  jump(id) {
    this.begin();
    this.queue = [];
    this.ending = null;
    this.enter(id);
    return this;
  }

  // --- 快照 ------------------------------------------------------------------

  snapshot() {
    return clone({
      state: this.state,
      scene: this.scene,
      queue: this.queue,
      current: this.current,
      ending: this.ending,
    });
  }

  restore(snap) {
    const s = clone(snap);
    this.state = s.state;
    this.scene = s.scene;
    this.queue = s.queue;
    this.current = s.current;
    this.ending = s.ending;
    this.last = [];
    return this;
  }

  prev() {
    const snap = this.history.pop();
    if (snap) this.restore(snap);
    return this;
  }
}

// --- 静态检查 ---------------------------------------------------------------

function walkConditions(cond, visit) {
  if (!cond) return;
  if (Array.isArray(cond)) return cond.forEach((c) => walkConditions(c, visit));
  for (const k of ['all', 'any']) if (cond[k]) cond[k].forEach((c) => walkConditions(c, visit));
  if (cond.not) walkConditions(cond.not, visit);
  visit(cond);
}

/**
 * 加载时把剧本过一遍：引用了不存在的结点 / 道具、拼错的 type、到不了的结点、
 * 拿不到的道具。返回问题列表，空的就是干净。运行时引擎对这些一律宽容，所以得在这儿拦。
 */
export function validate(story) {
  const problems = [];
  const nodes = new Map((story.nodes || []).map((n) => [n.id, n]));
  const items = story.items || {};
  const choiceIds = new Set();
  const obtainable = new Set(story.state?.inventory || []);
  const at = (where) => (msg) => problems.push(`${where}：${msg}`);

  if (!nodes.has(story.start)) problems.push(`start 指向不存在的结点「${story.start}」`);

  const checkCond = (cond, say) =>
    walkConditions(cond, (c) => {
      if (c.all || c.any || c.not) return;
      if (c.has && !items[c.has]) say(`条件里的道具「${c.has}」没有定义`);
      if (c.visited && !nodes.has(c.visited)) say(`条件里的结点「${c.visited}」不存在`);
      if (c.key && c.operator && !OPS[c.operator]) say(`不认识的 operator「${c.operator}」`);
      if (!c.has && !c.visited && !c.done && !c.key) say(`看不懂的条件 ${JSON.stringify(c)}`);
    });
  const checkBlock = (block, say) => {
    for (const e of block?.effects || []) {
      if (!EFFECT_TYPES.includes(e.type)) say(`不认识的 effect「${e.type}」`);
      if ((e.type === 'add_item' || e.type === 'remove_item') && !items[e.item]) say(`effect 里的道具「${e.item}」没有定义`);
      if (e.type === 'add_item') obtainable.add(e.item);
    }
    for (const ev of block?.events || []) {
      if (!EVENT_TYPES.includes(ev.type)) say(`不认识的 event「${ev.type}」`);
    }
  };

  const doneRefs = [];
  for (const node of nodes.values()) {
    const say = at(`结点 ${node.id}`);
    checkBlock(node.enter, say);
    checkBlock(node.first, say);
    if (Array.isArray(node.text)) node.text.forEach((t) => checkCond(t.condition, say));
    if (Array.isArray(node.bg)) node.bg.forEach((b) => checkCond(b.condition, say));
    (node.choices || []).forEach((c, i) => {
      const cs = at(`${node.id} 的行动「${c.text}」`);
      const kind = c.kind || (c.next ? 'go' : 'act');
      choiceIds.add(c.id || `${node.id}#${i}`);
      if (!CHOICE_KINDS.includes(kind)) cs(`不认识的 kind「${kind}」`);
      if (c.next && !nodes.has(c.next)) cs(`next 指向不存在的结点「${c.next}」`);
      if (kind === 'use' && !items[c.use]) cs(`use 的道具「${c.use}」没有定义`);
      if (kind === 'code' && (c.code === undefined || c.code === '')) cs('code 类行动没写 code');
      checkCond(c.condition, cs);
      walkConditions(c.condition, (x) => x.done && doneRefs.push([x.done, cs]));
      checkBlock(c, cs);
      checkBlock(c.fail, cs);
    });
  }
  for (const [id, item] of Object.entries(items)) {
    const say = at(`道具 ${id}`);
    if (item.examine) {
      checkCond(item.examine.condition, say);
      checkBlock(item.examine, say);
    }
  }
  for (const r of story.recipes || []) {
    const say = at(`配方 ${r.items?.join('+')}`);
    if (r.items?.length !== 2) say('配方只支持两样东西组合');
    for (const i of r.items || []) if (!items[i]) say(`材料「${i}」没有定义`);
    if (!items[r.result]) say(`产物「${r.result}」没有定义`);
    else obtainable.add(r.result);
    checkBlock(r, say);
  }
  for (const [ref, say] of doneRefs) if (!choiceIds.has(ref)) say(`条件里的行动「${ref}」不存在`);

  // 不看条件、只顺着 next 走一遍：连路都没有的结点，写得再好玩家也见不到
  const reach = new Set();
  const stack = [story.start];
  while (stack.length) {
    const id = stack.pop();
    if (reach.has(id) || !nodes.has(id)) continue;
    reach.add(id);
    for (const c of nodes.get(id).choices || []) if (c.next) stack.push(c.next);
  }
  for (const id of nodes.keys()) if (!reach.has(id)) problems.push(`结点 ${id}：从 start 走不到`);
  for (const id of Object.keys(items)) {
    if (!obtainable.has(id)) problems.push(`道具 ${id}：没有任何地方能拿到`);
  }
  for (const id of story.endings || []) {
    if (nodes.get(id)?.kind !== 'ending') problems.push(`endings 里的「${id}」不是 kind: ending 的结点`);
  }
  return problems;
}

// --- 可解性 ------------------------------------------------------------------

/** 当前状态下所有「有意义」的一步：行动用对的输入去做，道具逐个检查，配方逐个组合。 */
function moves(engine) {
  const out = [];
  const node = engine.node;
  for (const a of engine.actions) {
    if (a.locked) continue;
    const label = `${node.label || node.id}：${a.text}`;
    if (a.kind === 'code') out.push({ label: `${label}（${a.code}）`, run: (e) => e.choose(a.index, a.code).ok });
    else if (a.kind === 'use') {
      if (engine.state.inventory.includes(a.use)) {
        out.push({ label: `${label}（用「${engine.itemName(a.use)}」）`, run: (e) => e.choose(a.index, a.use).ok });
      }
    } else out.push({ label, run: (e) => e.choose(a.index).ok });
  }
  for (const id of engine.state.inventory) {
    if (engine.items[id]?.examine) out.push({ label: `检查「${engine.itemName(id)}」`, run: (e) => e.examine(id) });
  }
  for (const r of engine.recipes) {
    const [a, b] = r.items;
    if (engine.state.inventory.includes(a) && engine.state.inventory.includes(b)) {
      out.push({ label: `组合「${engine.itemName(a)}」+「${engine.itemName(b)}」`, run: (e) => e.combine(a, b) });
    }
  }
  return out;
}

/**
 * 状态里哪些部分会影响「以后还能发生什么」。只拿这些做去重的 key——
 * 走过哪几个房间、看过几次大钟这种历史，不被任何条件读到就不影响可达性，
 * 全放进 key 里的话，同一个局面会因为来路不同被当成几万个状态，搜不完。
 */
function relevance(story) {
  const visited = new Set();
  const done = new Set();
  const scan = (cond) =>
    walkConditions(cond, (c) => {
      if (c.visited) visited.add(c.visited);
      if (c.done) done.add(c.done);
    });
  for (const node of story.nodes || []) {
    if (Array.isArray(node.text)) node.text.forEach((t) => scan(t.condition));
    if (Array.isArray(node.bg)) node.bg.forEach((b) => scan(b.condition));
    // 第一次进来才改状态的地方：去没去过决定那段 effect 还会不会发生
    if (node.first?.effects?.length) visited.add(node.id);
    (node.choices || []).forEach((c, i) => {
      scan(c.condition);
      if (c.once) done.add(c.id || `${node.id}#${i}`); // once 的行动做没做过决定它还在不在
    });
  }
  for (const item of Object.values(story.items || {})) scan(item.examine?.condition);
  for (const r of story.recipes || []) scan(r.condition);
  return { visited, done };
}

function stateKey(engine, rel) {
  const s = engine.state;
  return JSON.stringify([
    engine.current,
    engine.ending?.id || null,
    Object.entries(s.flags).sort(),
    [...s.inventory].sort(),
    s.visited.filter((id) => rel.visited.has(id)).sort(),
    s.done.filter((id) => rel.done.has(id)).sort(),
  ]);
}

/**
 * 在状态空间里做广度优先搜索：每个结局能不能走到、最短要几步。
 * 密室逃脱最怕的是「卡死」——道具用掉了、门再也开不了。人工排查靠不住，让机器把路全走一遍。
 */
export function solve(story, { limit = 50000 } = {}) {
  const engine = new ExploreEngine(story);
  engine.queue = [];
  const rel = relevance(story);
  const seen = new Set([stateKey(engine, rel)]);
  const frontier = [{ snap: engine.snapshot(), path: [] }];
  const endings = {};
  let explored = 0;
  let deadEnds = 0;
  while (frontier.length && explored < limit) {
    const { snap, path } = frontier.shift();
    explored += 1;
    engine.restore(snap);
    if (engine.ending) {
      if (!endings[engine.ending.id]) endings[engine.ending.id] = path;
      continue;
    }
    let progressed = false;
    for (const move of moves(engine)) {
      engine.restore(snap);
      engine.history = [];
      if (!move.run(engine)) continue;
      engine.queue = [];
      const key = stateKey(engine, rel);
      progressed = true;
      if (seen.has(key)) continue;
      seen.add(key);
      frontier.push({ snap: engine.snapshot(), path: [...path, move.label] });
    }
    if (!progressed) deadEnds += 1;
  }
  const missing = (story.endings || []).filter((id) => !endings[id]);
  return { endings, missing, explored, deadEnds, exhausted: frontier.length === 0 };
}
