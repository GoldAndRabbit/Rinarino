// 「这一幕」：点分支图上的一个结点，把它摊开成一张卡——进场摆了谁、每一步谁在说话、
// 用的是哪张立绘、玩家在哪儿做决定、每个选项通到哪儿、改了哪些变量。
//
// 全部从 story.json + cast.json 静态推出来，不跑引擎：看的是「这一幕写成了什么样」，
// 不是「这一局走成了什么样」。所以没写 # sprite: 的结点，进场只能标「沿用上一幕」——
// 上一幕台上是谁，取决于玩家从哪条路走过来。

import { parseSprites } from '../engine/engine.js';
import { spriteId } from '../engine/player.js';

const TERMINALS = new Set(['END', 'DONE']);
const OP_TEXT = { '>=': '≥', '<=': '≤', '==': '=', '!=': '≠', '>': '>', '<': '<' };

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
// 剧本里的选项有的自带「」有的没有，统一成带的
const quote = (s) => (/^「/.test(s) ? s : `「${s}」`);

function varLabel(meta, name) {
  return meta?.vars?.[name]?.label || name;
}

function effectText(meta, [name, op, value]) {
  const delta = op === '+=' ? `+${value}` : op === '-=' ? `−${value}` : `= ${value}`;
  return `${varLabel(meta, name)} ${delta}`;
}

function condText(meta, cond) {
  return (cond || [])
    .map(([name, op, value]) => `${varLabel(meta, name)} ${OP_TEXT[op] || op} ${value}`)
    .join('，且 ');
}

/** 整部剧本里被判定读到的变量：选项条件和条件跳转里出现过的。 */
function testedVars(story) {
  const out = new Set();
  for (const node of story.nodes || []) {
    for (const d of node.diverts || []) for (const [name] of d.cond || []) out.add(name);
    for (const c of node.choices || []) for (const [name] of c.cond || []) out.add(name);
  }
  return out;
}

function classify(node, label) {
  const choices = node.choices || [];
  const targets = [...new Set(choices.map((c) => c.target))];
  if (node.diverts?.length) return { kind: '条件分流', sub: `按变量走 ${node.diverts.length} 个出口` };
  if (choices.length && targets.length === 1) {
    return { kind: '伪分支', sub: `${choices.length} 个选项最后都回到「${label(targets[0])}」` };
  }
  if (choices.length) return { kind: '分支', sub: `${choices.length} 个选项通往 ${targets.length} 个去处` };
  if (!node.next || TERMINALS.has(node.next)) return { kind: '结局', sub: '放完这一幕，故事停在这儿' };
  return { kind: '直行', sub: `放完直接进「${label(node.next)}」` };
}

function sprite(ctx, key, expression, { badge = '', caption = null } = {}) {
  const url = ctx.assets[spriteId(key, expression)];
  const name = ctx.people.get(key)?.name || key;
  const img = url
    ? `<img class="checker" src="${esc(url)}" alt="${esc(name)} ${esc(expression)}" loading="lazy" tabindex="0"
        data-zoom="${esc(url)}" data-zoom-kind="sprite" data-zoom-cap="${esc(name)} · ${esc(expression)}">`
    : '<span class="sd-sprite-missing">没图</span>';
  return `<figure class="sd-sprite">${img}<figcaption>${esc(caption ?? expression)}${
    badge ? `<i>${esc(badge)}</i>` : ''
  }</figcaption></figure>`;
}

/** 逐行排卡片，顺带推着台上的立绘往下走。stage 为 null 表示「不知道」（沿用上一幕）。 */
function steps(ctx, lines, stage) {
  let current = stage;
  const html = lines
    .map((line, i) => {
      let badge = current ? '沿用' : '沿用上一幕';
      if (line.tags?.sprite) {
        current = parseSprites(line.tags.sprite);
        badge = '换';
      }
      const who = line.speaker;
      const person = who ? ctx.people.get(who) : null;
      let art = '';
      if (person) {
        const on = current?.find((s) => s.key === who);
        art = sprite(ctx, who, on?.expression || 'normal', { badge: current && !on ? '不在台上' : badge });
      }
      const chips = ['bg', 'cg']
        .filter((k) => line.tags?.[k])
        .map((k) => `<span class="sd-tag is-accent">${k} ${esc(line.tags[k])}</span>`)
        .join('');
      return `<article class="sd-step${person ? '' : ' is-narration'}">
        ${art}
        <div class="sd-step-body">
          <div class="sd-step-meta">step ${i + 1}</div>
          <div class="sd-speaker">${esc(person?.name || who || '旁白')}</div>
          <span class="sd-tag">演出</span>${chips}
          <p>${esc(line.text)}</p>
        </div>
      </article>`;
    })
    .join('');
  return { html, stage: current };
}

function summary(ctx, choices, kind) {
  if (kind !== '伪分支') return '';
  const texts = [...new Set(choices.flatMap((c) => (c.effects || []).map((e) => effectText(ctx.meta, e))))];
  if (!texts.length) return '<p class="sd-summary">去向相同，也不改任何变量——这几个选项只是换了句台词。</p>';
  const names = [...new Set(choices.flatMap((c) => (c.effects || []).map(([n]) => n)))];
  const unread = names.filter((n) => !ctx.tested.has(n)).map((n) => varLabel(ctx.meta, n));
  // 伪分支唯一的意义就是改变量；后面要是没人读这个变量，这几个选项等于白写
  let tail = '它会在后面的判定里被念到。';
  if (unread.length === names.length) tail = '但后面没有任何判定读这些变量——这几个选项实际上没有区别。';
  else if (unread.length) tail = `其中「${unread.join('、')}」后面没有判定读它，改了等于没改。`;
  return `<p class="sd-summary">去向相同，差别只在变量上：${esc(texts.join('，'))}。${esc(tail)}</p>`;
}

function branches(ctx, node, stage, kind) {
  const choices = node.choices || [];
  const rows = choices
    .map((c, i) => {
      const effects = (c.effects || []).map((e) => effectText(ctx.meta, e));
      const body = steps(ctx, c.lines || [], stage);
      // 选项后面最后开口的那个人，用他那一刻的表情当这一行的立绘
      const last = [...(c.lines || [])].reverse().find((l) => ctx.people.has(l.speaker))?.speaker;
      const art = last ? sprite(ctx, last, body.stage?.find((s) => s.key === last)?.expression || 'normal') : '';
      return `<div class="sd-branch">
        <div class="sd-branch-main">
          <div class="sd-branch-name">${kind === '伪分支' ? '伪分支' : '选项'}${i + 1}
            <span class="sd-branch-text">${esc(quote(c.text))}</span></div>
          <div class="sd-branch-to">→ ${ctx.go(c.target)}${effects
            .map((t) => ` <span class="sd-effect">${esc(t)}</span>`)
            .join('')}</div>
          ${c.cond?.length ? `<div class="sd-cond">要求 ${esc(condText(ctx.meta, c.cond))}，不满足时灰着</div>` : ''}
          ${c.lines?.length ? `<div class="sd-branch-lines">${body.html}</div>` : ''}
        </div>
        ${art}
      </div>`;
    })
    .join('');
  return rows + summary(ctx, choices, kind);
}

export function render(story, id, { assets = {}, cast = {}, meta = {}, order = [], playing = null } = {}) {
  const byId = new Map((story.nodes || []).map((n) => [n.id, n]));
  const node = byId.get(id);
  if (!node) return '<p class="empty">没有这一幕。</p>';
  const label = (nid) => (TERMINALS.has(nid) ? '结束' : byId.get(nid)?.label || nid);
  const ctx = {
    assets,
    meta,
    people: new Map((cast.characters || []).map((c) => [c.key, c])),
    tested: testedVars(story),
    go: (nid) =>
      byId.has(nid)
        ? `<button class="sd-link" data-scene-go="${esc(nid)}">${esc(label(nid))}</button>`
        : `<span class="sd-end">${esc(label(nid))}</span>`,
  };

  const { kind, sub } = classify(node, label);
  const entry = node.tags?.sprite ? parseSprites(node.tags.sprite) : null;
  const lines = node.lines || [];
  const choices = node.choices || [];
  const played = steps(ctx, lines, entry);
  const at = order.indexOf(id);
  const prev = at > 0 ? order[at - 1] : null;
  const next = at >= 0 && at < order.length - 1 ? order[at + 1] : null;

  const bgId = node.tags?.bg;
  const bg =
    bgId && assets[bgId]
      ? `<figure class="sd-bg"><img src="${esc(assets[bgId])}" alt="${esc(bgId)}" loading="lazy" tabindex="0"
          data-zoom="${esc(assets[bgId])}" data-zoom-cap="${esc(bgId)}"><figcaption>${esc(bgId)}</figcaption></figure>`
      : '';
  const onStage = entry
    ? entry
        .map((s) => sprite(ctx, s.key, s.expression, { caption: `${ctx.people.get(s.key)?.name || s.key} · ${s.expression}` }))
        .join('')
    : '<p class="sd-note">没写 # sprite:，沿用上一幕留在台上的人——是谁取决于玩家从哪条路走过来。</p>';

  const diverts = node.diverts?.length
    ? `<section class="sd-block">
        <h4 class="sd-h">条件分流 <small>从上往下试，第一个满足的生效</small></h4>
        ${node.diverts
          .map((d, i) => `<div class="sd-divert"><span>${i + 1}</span><b>${esc(condText(meta, d.cond) || '无条件')}</b>
            → ${ctx.go(d.target)}</div>`)
          .join('')}
        ${node.next ? `<div class="sd-divert"><span>否则</span>→ ${ctx.go(node.next)}</div>` : ''}
      </section>`
    : '';
  const exit =
    !choices.length && !node.diverts?.length
      ? `<section class="sd-block"><h4 class="sd-h">出口</h4>
          <div class="sd-divert"><span>→</span>${ctx.go(node.next || 'END')}</div></section>`
      : '';

  return `<article class="sd">
    <header class="sd-head">
      <div class="sd-title">
        <h3>${esc(node.label || id)}</h3>
        <span class="sd-sub">${esc(kind)} · ${esc(sub)}</span>
        ${playing === id ? '<span class="sd-live">正在播</span>' : ''}
      </div>
      <nav class="sd-nav">
        <button class="ctl" ${prev ? `data-scene-go="${esc(prev)}"` : 'disabled'}>← 上一幕</button>
        <button class="ctl" ${next ? `data-scene-go="${esc(next)}"` : 'disabled'}>下一幕 →</button>
        <button class="ctl" data-scene-back>返回</button>
        <button class="ctl ctl-primary" data-scene-jump="${esc(id)}">跳到这一幕</button>
      </nav>
    </header>
    <div class="sd-id">${esc(id)}</div>

    <section class="sd-block">
      <h4 class="sd-h">进场 <small>这一幕一开始摆出来的样子，和选项无关</small></h4>
      <div class="sd-entry">${bg}${onStage}</div>
    </section>

    <section class="sd-block">
      <h4 class="sd-h">逐步演出
        <span class="sd-count">演出 ${lines.length} 步</span>
        ${choices.length ? `<span class="sd-count is-accent">交互 1 处 · ${choices.length} 个选项</span>` : ''}
        <small>灰色的是演出，玩家只点「下一步」；描蓝边的那一块才要他做决定</small>
      </h4>
      ${played.html || '<p class="sd-note">这一幕没有台词。</p>'}
      ${
        choices.length
          ? `<div class="sd-interact">
              <div class="sd-interact-h">交互 <span class="sd-tag">玩家在这里做选择</span></div>
              <div class="sd-options">${choices.map((c) => `<span class="sd-option">${esc(quote(c.text))}</span>`).join('')}</div>
            </div>`
          : ''
      }
    </section>

    ${
      choices.length
        ? `<section class="sd-block">
            <h4 class="sd-h">选完之后 <small>每个选项接着放的台词、改的变量、通到哪一幕</small></h4>
            ${branches(ctx, node, played.stage, kind)}
          </section>`
        : ''
    }
    ${diverts}${exit}
  </article>`;
}
