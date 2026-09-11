// 播放器：把 Engine 的状态画成一块「屏幕」。只读引擎，不改引擎。
//
// 屏幕的 DOM 也由它自己吐出来（SCREEN），所以宿主 WebUI 和 s6 打包出来的
// 单文件页只要给一个空容器就行，两边的播放界面从此是同一份东西。
// 外面的壳（标题、按钮、小说列表、分支图）各自管各自的。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

export function spriteId(key, expression = 'normal') {
  return `sprite_${key}_${expression}`;
}

/** 播放区的 DOM。样式在同目录的 play.css，两边共用。 */
export const SCREEN = `
  <div class="screen-art">
    <img class="art-bg" id="art-bg" alt="">
    <div class="art-sprites" id="art-sprites"></div>
    <!-- 只有选项浮在画面上（半透明），台词框仍然在画面下面，不挡画 -->
    <div class="choices" id="choices" hidden></div>
  </div>
  <div class="dialogue" id="dialogue">
    <div class="speaker" id="speaker">旁白</div>
    <p class="dialogue-text" id="dialogue-text"></p>
    <span class="dialogue-caret" id="dialogue-caret">▾</span>
  </div>
  <div class="ending" id="ending" hidden></div>
`;

export class Player {
  /**
   * @param root  找元素的根（document 或任意容器）
   * @param screen  播放区容器：给了就往里塞 SCREEN，不给就当宿主已经摆好了
   */
  constructor(root, screen = null) {
    if (screen) {
      screen.classList.add('screen');
      screen.innerHTML = SCREEN;
    }
    this.el = {
      bg: root.querySelector('#art-bg'),
      sprites: root.querySelector('#art-sprites'),
      dialogue: root.querySelector('#dialogue'),
      speaker: root.querySelector('#speaker'),
      text: root.querySelector('#dialogue-text'),
      caret: root.querySelector('#dialogue-caret'),
      choices: root.querySelector('#choices'),
      ending: root.querySelector('#ending'),
      vars: root.querySelector('#vars'),
      prev: root.querySelector('#btn-prev'),
      next: root.querySelector('#btn-next'),
    };
    this.onChoose = () => {};
    this.el.choices.addEventListener('click', (ev) => {
      const btn = ev.target.closest('button[data-choice]');
      if (btn && !btn.disabled) this.onChoose(Number(btn.dataset.choice));
    });
  }

  bind(payload) {
    this.payload = payload;
    this.assets = payload.assets || {};
    this.cast = new Map((payload.cast?.characters || []).map((c) => [c.key, c]));
    this.meta = payload.meta || {};
  }

  draw(engine) {
    this.drawArt(engine);
    this.drawText(engine);
    this.drawChoices(engine);
    this.drawEnding(engine);
    this.drawVars(engine);
    this.el.prev.disabled = !engine.canPrev;
    this.el.next.disabled = !engine.canNext;
  }

  drawArt(engine) {
    const { scene } = engine;
    const url = this.assets[scene.cg] || this.assets[scene.bg];
    if (url && this.el.bg.getAttribute('src') !== url) this.el.bg.setAttribute('src', url);
    // CG 是整幅画，人物已经在画里了，这时候不再叠立绘
    const sprites = scene.cg ? [] : scene.sprites || [];
    const speaker = engine.line?.speaker;
    // 让 CSS 知道台上有几个人，好按人数定高（超过 4 个按 4 个排）
    this.el.sprites.dataset.n = String(Math.min(4, Math.max(1, sprites.length)));
    this.el.sprites.innerHTML = sprites
      .map(({ key, expression }) => {
        const src = this.assets[spriteId(key, expression)] || this.assets[spriteId(key)];
        if (!src) return '';
        const dim = speaker && speaker !== key ? ' class="is-dim"' : '';
        return `<img${dim} src="${esc(src)}" alt="${esc(this.cast.get(key)?.name || key)}">`;
      })
      .join('');
  }

  drawText(engine) {
    const line = engine.line;
    const narrator = this.meta.ui?.narrator || '旁白';
    if (!line) {
      this.el.speaker.textContent = engine.awaitingChoice ? '选择' : narrator;
      this.el.text.textContent = engine.awaitingChoice ? '' : this.el.text.textContent;
      this.el.caret.hidden = true;
      return;
    }
    const name = line.speaker ? this.cast.get(line.speaker)?.name || line.speaker : narrator;
    this.el.speaker.textContent = name;
    const color = line.speaker ? this.cast.get(line.speaker)?.color : '';
    this.el.speaker.style.background = color ? `${color}33` : '';
    this.el.text.textContent = line.text;
    this.el.caret.hidden = !engine.canNext;
  }

  drawChoices(engine) {
    const open = engine.choices;
    const locked = engine.lockedChoices;
    if (!open.length && !locked.length) {
      this.el.choices.hidden = true;
      this.el.choices.innerHTML = '';
      return;
    }
    this.el.choices.hidden = false;
    this.el.choices.innerHTML = [
      ...open.map(
        (c) => `<button class="choice" data-choice="${c.index}">${esc(c.text)}</button>`
      ),
      ...locked.map(
        (c) =>
          `<button class="choice" disabled><span class="choice-lock">条件未满足</span>${esc(c.text)}</button>`
      ),
    ].join('');
  }

  drawEnding(engine) {
    if (!engine.finished) {
      this.el.ending.hidden = true;
      return;
    }
    const info = (this.meta.endings || {})[engine.ending] || {};
    const who = info.character ? this.cast.get(info.character)?.name : '';
    const kind = info.kind === 'true' ? '真结局' : info.kind === 'pass' ? '擦肩结局' : '结局';
    this.el.ending.hidden = false;
    this.el.ending.innerHTML = `<h3>${esc(info.title || '完')}</h3>
      <p>${esc([kind, who].filter(Boolean).join(' · '))} — ${esc(engine.ending || '')}</p>`;
  }

  drawVars(engine) {
    const spec = this.meta.vars || {};
    const chips = Object.entries(engine.vars)
      .filter(([name]) => !spec[name]?.hidden)
      .map(([name, value]) => {
        const label = spec[name]?.label || name;
        const color = this.cast.get(name)?.color || '#c9c8c3';
        return `<span class="var-chip"><i class="var-dot" style="background:${esc(color)}"></i>${esc(
          label
        )} <b>${esc(value)}</b></span>`;
      });
    this.el.vars.innerHTML = chips.join('');
  }
}
