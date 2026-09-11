// story.json 的运行时。纯状态机，不碰 DOM。
//
// 一步 = 一行台词。行走完了才看出口：条件跳转 → 选项 → 直接跳转。
// 选项体（选完之后那几句）当成一个临时帧压上来，放完再跳到落点，
// 于是「选项也能有台词」不需要在图里多造结点。

const TERMINALS = new Set(['END', 'DONE']);

const OPS = {
  '>=': (a, b) => a >= b,
  '<=': (a, b) => a <= b,
  '==': (a, b) => a === b,
  '!=': (a, b) => a !== b,
  '>': (a, b) => a > b,
  '<': (a, b) => a < b,
};

const APPLY = {
  '=': (_cur, v) => v,
  '+=': (cur, v) => cur + v,
  '-=': (cur, v) => cur - v,
};

export function parseSprites(raw) {
  if (!raw) return [];
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const [key, expression = 'normal'] = s.split(':').map((x) => x.trim());
      return { key, expression };
    });
}

export class Engine {
  constructor(story) {
    this.story = story;
    this.byId = new Map((story.nodes || []).map((n) => [n.id, n]));
    this.reset();
  }

  reset() {
    this.vars = { ...(this.story.vars || {}) };
    this.scene = { bg: null, cg: null, sprites: [], amb: null, music: null, video: null };
    this.visited = [];
    this.taken = [];
    this.frame = null;
    this.finished = false;
    this.ending = null;
    this.history = [];
    this.enterNode(this.story.start);
    return this;
  }

  // --- 状态读取 -------------------------------------------------------------

  get node() {
    return this.frame ? this.byId.get(this.frame.node) : null;
  }

  get line() {
    if (!this.frame) return null;
    return this.frame.lines[this.frame.i] || null;
  }

  /** 行放完了、又有能选的选项 → 该玩家动手了。 */
  get choices() {
    if (!this.frame || this.finished) return [];
    if (this.frame.i < this.frame.lines.length) return [];
    if (this.frame.target) return [];
    const node = this.node;
    if (!node || !node.choices) return [];
    return node.choices
      .map((c, index) => ({ ...c, index }))
      .filter((c) => this.test(c.cond));
  }

  /** 条件不成立而被挡住的选项，UI 上灰着显示，让玩家知道这儿有东西。 */
  get lockedChoices() {
    if (!this.frame || this.finished) return [];
    if (this.frame.i < this.frame.lines.length || this.frame.target) return [];
    const node = this.node;
    if (!node || !node.choices) return [];
    return node.choices.map((c, index) => ({ ...c, index })).filter((c) => !this.test(c.cond));
  }

  get awaitingChoice() {
    return this.choices.length > 0;
  }

  get canNext() {
    return !this.finished && !this.awaitingChoice;
  }

  // --- 求值 -----------------------------------------------------------------

  test(cond) {
    if (!cond || !cond.length) return true;
    return cond.every(([name, op, value]) => (OPS[op] || (() => false))(this.vars[name] ?? 0, value));
  }

  applyEffects(effects) {
    for (const [name, op, value] of effects || []) {
      this.vars[name] = (APPLY[op] || APPLY['='])(this.vars[name] ?? 0, value);
    }
  }

  applyTags(tags) {
    if (!tags) return;
    for (const [key, raw] of Object.entries(tags)) {
      if (key === 'bg') {
        this.scene.bg = raw;
        this.scene.cg = null;
      } else if (key === 'cg') {
        this.scene.cg = raw;
      } else if (key === 'sprite') {
        this.scene.sprites = parseSprites(raw);
      } else if (key === 'clear') {
        for (const what of raw.split(',').map((s) => s.trim())) {
          if (what === 'sprite') this.scene.sprites = [];
          else if (what in this.scene) this.scene[what] = null;
        }
      } else if (key in this.scene) {
        this.scene[key] = raw;
      }
    }
  }

  // --- 推进 -----------------------------------------------------------------

  enterNode(id) {
    if (!id || TERMINALS.has(id)) {
      this.finished = true;
      this.frame = this.frame && { ...this.frame, i: this.frame.lines.length, target: null };
      return;
    }
    const node = this.byId.get(id);
    if (!node) {
      this.finished = true;
      return;
    }
    this.visited.push(id);
    this.applyTags(node.tags);
    this.applyEffects(node.effects);
    this.frame = { node: id, lines: node.lines || [], i: -1, target: null };
    this.step();
  }

  /** 内部推进一格，不记快照。 */
  step() {
    if (this.finished || !this.frame) return;
    this.frame.i += 1;
    const line = this.frame.lines[this.frame.i];
    if (line) {
      this.applyTags(line.tags);
      return;
    }
    if (this.frame.target) {
      const target = this.frame.target;
      this.frame.target = null;
      this.enterNode(target);
      return;
    }
    const node = this.node;
    if (!node) {
      this.finished = true;
      return;
    }
    for (const divert of node.diverts || []) {
      if (this.test(divert.cond)) {
        this.enterNode(divert.target);
        return;
      }
    }
    if (this.choices.length) return; // 等玩家选
    if (node.next) {
      if (TERMINALS.has(node.next)) {
        this.ending = node.id;
        this.finished = true;
        return;
      }
      this.enterNode(node.next);
      return;
    }
    this.finished = true;
  }

  next() {
    if (!this.canNext) return this;
    this.pushHistory();
    this.step();
    return this;
  }

  choose(index) {
    const choice = this.choices.find((c) => c.index === index);
    if (!choice) return this;
    this.pushHistory();
    this.taken.push([this.frame.node, index]);
    this.applyEffects(choice.effects);
    this.frame = {
      node: this.frame.node,
      lines: choice.lines || [],
      i: -1,
      target: choice.target,
    };
    this.step();
    return this;
  }

  // --- 回退：整份状态压栈，剧本小，直接深拷贝最省心 -----------------------------

  snapshot() {
    return JSON.parse(
      JSON.stringify({
        vars: this.vars,
        scene: this.scene,
        visited: this.visited,
        taken: this.taken,
        frame: this.frame,
        finished: this.finished,
        ending: this.ending,
      })
    );
  }

  restore(snap) {
    Object.assign(this, JSON.parse(JSON.stringify(snap)));
    return this;
  }

  pushHistory() {
    this.history.push(this.snapshot());
    if (this.history.length > 400) this.history.shift();
  }

  get canPrev() {
    return this.history.length > 0;
  }

  prev() {
    const snap = this.history.pop();
    if (snap) this.restore(snap);
    return this;
  }
}
