// 角色页：全片画风 → 设定表 → 立绘 → 出场的事件 CG。
//
// 拆成四块是因为它们回答四个不同的问题：整部作品长什么样（画风，改它重定全片）、
// 这个人长什么样（设定表，逐字拼进这个人的每一张图）、实际画出来什么样（立绘）、
// 关键瞬间里还是不是同一个人（CG）。每张图都挂着它真正用过的 prompt（assets/prompts.json），
// 为什么长这样、怎么重跑出同一张，在同一个地方看得到。

import { card } from './gallery.js';
import { spriteId } from '../engine/player.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function spriteCard(character, key, spec, { assets, prompts }) {
  const id = spriteId(character.key, key);
  const url = assets[id];
  // 表情既可以是一句话，也可以是 {表情, 动作} 两段 —— 后者直接塞进模板会变成 [object Object]
  const face = typeof spec === 'string' ? spec : spec?.表情 || spec?.face || '';
  const pose = typeof spec === 'string' ? '' : spec?.动作 || spec?.pose || '';
  const prompt = prompts[id];
  const zoom = url
    ? ` tabindex="0" data-zoom="${esc(url)}" data-zoom-kind="sprite" data-zoom-cap="${esc(character.name)} · ${esc(key)}"`
    : '';
  return `<article class="sp-card">
    <span class="sp-shot checker"${zoom}>${
      url ? `<img src="${esc(url)}" alt="${esc(character.name)} ${esc(key)}" loading="lazy">` : '<i>还没画</i>'
    }</span>
    <div class="sp-key">${esc(key)}</div>
    ${face ? `<p class="sp-desc" title="${esc(face)}">${esc(face)}</p>` : ''}
    ${pose ? `<p class="sp-desc sp-pose" title="${esc(pose)}"><b>动作</b>${esc(pose)}</p>` : ''}
    ${
      prompt?.prompt
        ? `<details class="prompt-fold"><summary>生成 prompt${
            prompt.source === 'placeholder' ? '（占位图）' : ''
          }</summary><pre>${esc(prompt.prompt)}</pre></details>`
        : ''
    }
  </article>`;
}

export function render(character, { assets = {}, prompts = {}, cast = {} } = {}) {
  const art = cast.art_direction || {};
  const expressions = Object.entries(character.expressions || {});
  const fields = Object.entries(character.desc || {})
    .map(([k, v]) => `<div class="ch-row"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
    .join('');

  // CG 描述里点了这个人名字的，就是他出场的 CG——和 s3 拿立绘当参考图的判据是同一个
  const cgIds = Object.entries(cast.cgs || {})
    .filter(([id, desc]) => assets[id] && character.name && String(desc).includes(character.name))
    .map(([id]) => id);

  return `<header class="ch-head">
      ${character.color ? `<i class="ch-dot" style="background:${esc(character.color)}"></i>` : ''}
      <h2>${esc(character.name)}</h2>
      ${character.role ? `<span>${esc(character.role)}</span>` : ''}
      <code>${esc(character.key)} · ${expressions.length} 张立绘</code>
    </header>

    <section class="ch-style">
      <div class="ch-style-h"><b>全片画风</b>
        <span>拼在<strong>每一张</strong>图 prompt 的最前面（立绘、背景、CG 都是）；改它等于重定全片的样子</span></div>
      <p>${esc(art.style || 'cast.json 里没有 art_direction.style')}</p>
      ${art.avoid_style ? `<div class="ch-style-avoid"><b>画风负面词</b>${esc(art.avoid_style)}</div>` : ''}
    </section>

    <dl class="ch-desc">${fields || '<div class="ch-row"><dt>—</dt><dd>没有设定表</dd></div>'}</dl>

    <section class="ch-section">
      <h3 class="sec-h">立绘 <span>${expressions.length} 张 · 设定表逐字拼进每一张；透明底垫了棋盘格，抠没抠干净一眼看得出</span></h3>
      <div class="sp-grid">${expressions.map(([key, spec]) => spriteCard(character, key, spec, { assets, prompts })).join('')}</div>
    </section>

    ${
      cgIds.length
        ? `<section class="ch-section">
            <h3 class="sec-h">出场的事件 CG <span>${cgIds.length} 张 · 关键瞬间的整张完成稿，照着上面的立绘生成，长相和服装应当一致</span></h3>
            <div class="gl-grid">${cgIds
              .map((id) => card(id, { url: assets[id], desc: cast.cgs[id], prompt: prompts[id]?.prompt }))
              .join('')}</div>
          </section>`
        : ''
    }`;
}
