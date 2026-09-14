// 探索解谜的屏幕：画面、台词、行动、背包。只读引擎——所有改动都走引擎的方法，
// 改完调 onChange，由宿主决定重画什么（宿主 WebUI 还要顺手重画调试面板）。
//
// DOM 自己吐（SCREEN），宿主 WebUI 和打包出来的单文件页给一个空容器就行。
//
// 交互照着密室逃脱的老规矩：先从背包里「拿出」一样东西，然后
//   点另一样道具 = 组合 · 点标了 ✦ 的行动 = 用在那里 · 点「检查」= 凑近看

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const ORDER = ['act', 'use', 'code', 'go'];
const ICON = { act: '◎', use: '✦', code: '#', go: '→' };

export const SCREEN = `
  <div class="xp-art">
    <img class="xp-bg" id="xp-bg" alt="" hidden>
    <div class="xp-placeholder" id="xp-placeholder"></div>
    <div class="xp-toast" id="xp-toast" hidden></div>
  </div>
  <div class="xp-dialogue" id="xp-dialogue">
    <div class="xp-speaker" id="xp-speaker"></div>
    <p class="xp-text" id="xp-text"></p>
    <span class="xp-caret" id="xp-caret">▾</span>
  </div>
  <div class="xp-actions" id="xp-actions"></div>
  <div class="xp-bag" id="xp-bag"></div>
  <div class="xp-ending" id="xp-ending" hidden></div>
`;

export class ExplorePlayer {
  constructor(root, screen = null) {
    if (screen) {
      screen.classList.add('screen', 'xp');
      screen.innerHTML = SCREEN;
    }
    const q = (id) => root.querySelector(`#${id}`);
    this.el = {
      bg: q('xp-bg'),
      placeholder: q('xp-placeholder'),
      toast: q('xp-toast'),
      dialogue: q('xp-dialogue'),
      speaker: q('xp-speaker'),
      text: q('xp-text'),
      caret: q('xp-caret'),
      actions: q('xp-actions'),
      bag: q('xp-bag'),
      ending: q('xp-ending'),
      prev: q('btn-prev'),
      next: q('btn-next'),
    };
    this.engine = null;
    this.selected = null; // 拿在手里的道具
    this.coding = null; // 正在输密码的那个行动 index
    this.seenEvents = null;
    this.onChange = () => {};

    this.el.dialogue.addEventListener('click', () => this.advance());
    this.el.actions.addEventListener('click', (ev) => this.clickAction(ev));
    this.el.actions.addEventListener('submit', (ev) => this.submitCode(ev));
    this.el.bag.addEventListener('click', (ev) => this.clickBag(ev));
  }

  bind(payload) {
    this.payload = payload;
    this.assets = payload.assets || {};
    this.meta = payload.meta || {};
    this.cast = new Map((payload.cast?.characters || []).map((c) => [c.key, c]));
    this.selected = null;
    this.coding = null;
  }

  // --- 输入 -----------------------------------------------------------------

  changed() {
    // 手里的东西用掉了（或者回退到还没拿到的时候），就别再「拿着」它
    if (this.selected && !this.engine.state.inventory.includes(this.selected)) this.selected = null;
    this.onChange();
  }

  advance() {
    if (this.engine?.canNext) {
      this.engine.next();
      this.changed();
    }
  }

  clickAction(ev) {
    if (ev.target.closest('[data-cancel]')) {
      this.coding = null;
      this.draw(this.engine);
      return;
    }
    const btn = ev.target.closest('button[data-act]');
    if (!btn) return;
    const index = Number(btn.dataset.act);
    const action = this.engine.actions.find((a) => a.index === index);
    if (!action) return;
    if (action.kind === 'code' && !action.locked) {
      this.coding = index;
      this.draw(this.engine);
      this.el.actions.querySelector('input')?.focus();
      return;
    }
    const result = this.engine.choose(index, action.kind === 'use' ? this.selected : null);
    if (result.ok && action.kind === 'use') this.selected = null;
    this.changed();
  }

  submitCode(ev) {
    const form = ev.target.closest('form[data-code]');
    if (!form) return;
    ev.preventDefault();
    const input = form.querySelector('input');
    const result = this.engine.choose(Number(form.dataset.code), input.value);
    if (result.ok) this.coding = null;
    this.changed();
  }

  clickBag(ev) {
    const tool = ev.target.closest('[data-bag]');
    if (tool) {
      if (tool.dataset.bag === 'examine' && this.selected) this.engine.examine(this.selected);
      if (tool.dataset.bag === 'put') this.selected = null;
      this.changed();
      return;
    }
    const chip = ev.target.closest('[data-item]');
    if (!chip || this.engine.canNext || this.engine.ending) return;
    const id = chip.dataset.item;
    if (this.selected && this.selected !== id) {
      this.engine.combine(this.selected, id);
      this.selected = null;
    } else {
      this.selected = this.selected === id ? null : id;
    }
    this.changed();
  }

  // --- 画 -------------------------------------------------------------------

  draw(engine) {
    this.engine = engine;
    this.drawArt(engine);
    this.drawText(engine);
    this.drawActions(engine);
    this.drawBag(engine);
    this.drawEnding(engine);
    this.playEvents(engine);
    if (this.el.prev) this.el.prev.disabled = !engine.canPrev;
    if (this.el.next) this.el.next.disabled = !engine.canNext;
  }

  drawArt(engine) {
    const id = engine.scene.cg || engine.scene.bg;
    const url = id && this.assets[id];
    this.el.bg.hidden = !url;
    if (url && this.el.bg.getAttribute('src') !== url) this.el.bg.setAttribute('src', url);
    // 没画的地方先用文字占位，玩法照样能跑通；配上图之后这块自动让位
    this.el.placeholder.hidden = Boolean(url);
    const node = engine.node;
    this.el.placeholder.innerHTML = `<b>${esc(node?.label || '')}</b>${id ? `<small>${esc(id)} · 还没画</small>` : ''}`;
  }

  drawText(engine) {
    const msg = engine.message;
    const narrator = this.meta.ui?.narrator || '旁白';
    this.el.caret.hidden = !msg;
    this.el.dialogue.classList.toggle('is-waiting', Boolean(msg));
    if (msg) {
      this.el.speaker.textContent = msg.speaker ? this.cast.get(msg.speaker)?.name || msg.speaker : narrator;
      this.el.text.textContent = msg.text;
      return;
    }
    this.el.speaker.textContent = engine.node?.label || '';
    this.el.text.textContent = this.selected
      ? `手里拿着「${engine.itemName(this.selected)}」。点另一件道具组合，点标 ✦ 的地方使用。`
      : engine.ending
        ? ''
        : '要做什么？';
  }

  drawActions(engine) {
    if (engine.canNext || engine.ending) {
      this.el.actions.innerHTML = engine.canNext ? '<p class="xp-hint">点台词继续</p>' : '';
      return;
    }
    const acts = [...engine.actions].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind));
    this.el.actions.innerHTML = acts
      .map((a) => {
        if (a.kind === 'code' && this.coding === a.index) {
          const tries = engine.state.attempts[a.id];
          return `<form class="xp-code" data-code="${a.index}">
            <label>${esc(a.text)}</label>
            <input autocomplete="off" inputmode="${/^\d+$/.test(String(a.code)) ? 'numeric' : 'text'}"
              maxlength="${String(a.code).length + 4}" placeholder="${esc(a.prompt || '输入密码')}">
            <button class="xp-code-ok">确定</button>
            <button type="button" class="xp-code-cancel" data-cancel>取消</button>
            ${tries ? `<small>试错 ${tries} 次</small>` : ''}
          </form>`;
        }
        const holding = a.kind === 'use' && this.selected ? `（用「${esc(engine.itemName(this.selected))}」）` : '';
        return `<button class="xp-act is-${a.kind}${a.locked ? ' is-locked' : ''}" data-act="${a.index}">
          <i>${ICON[a.kind] || '·'}</i><span>${esc(a.text)}${holding}</span>
          ${a.locked ? `<small>${esc(a.reason)}</small>` : ''}
        </button>`;
      })
      .join('');
  }

  drawBag(engine) {
    const inv = engine.state.inventory;
    const chips = inv
      .map((id) => {
        const item = engine.items[id] || {};
        const icon = item.icon && this.assets[item.icon];
        return `<button class="xp-item${id === this.selected ? ' is-selected' : ''}" data-item="${esc(id)}"
          title="${esc(item.desc || '')}">${icon ? `<img src="${esc(icon)}" alt="">` : ''}${esc(engine.itemName(id))}</button>`;
      })
      .join('');
    const tools = this.selected
      ? `<span class="xp-bag-tools">
          <button data-bag="examine">检查</button><button data-bag="put">放回</button>
        </span>`
      : '';
    this.el.bag.innerHTML = `<div class="xp-bag-h">背包 <b>${inv.length}</b>${tools}</div>
      <div class="xp-items">${chips || '<span class="xp-empty">空的</span>'}</div>`;
  }

  drawEnding(engine) {
    if (!engine.finished) {
      this.el.ending.hidden = true;
      return;
    }
    const kind = { true: '真结局', normal: '结局', bad: '坏结局', broken: '剧本出错' }[engine.ending.kind] || '结局';
    this.el.ending.hidden = false;
    this.el.ending.innerHTML = `<h3>${esc(engine.ending.title)}</h3><p>${esc(kind)} · ${esc(engine.ending.id)}</p>`;
  }

  /** Event 里没有状态的那几种（提示条、音效）只在「新」的一步里演一次。 */
  playEvents(engine) {
    if (engine.last === this.seenEvents) return;
    this.seenEvents = engine.last;
    const toast = engine.last.filter((e) => e.type === 'toast').pop();
    if (toast) {
      this.el.toast.textContent = toast.text;
      this.el.toast.hidden = false;
      this.el.toast.classList.remove('is-on');
      void this.el.toast.offsetWidth; // 重新触发动画
      this.el.toast.classList.add('is-on');
    }
    for (const ev of engine.last) {
      if (ev.type === 'sfx' && this.assets[ev.id]) new Audio(this.assets[ev.id]).play().catch(() => {});
    }
  }
}
