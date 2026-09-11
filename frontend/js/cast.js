// 角色页：立绘 + 逐字段设定表 + 表情网格。
// 每张表情图下面挂着它**真正用过的 prompt**（来自 assets/prompts.json），
// 所以「这张为什么长这样」和「怎么重跑出同一张」在同一个地方看得到。

import { spriteId } from '../engine/player.js';

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

export function render(character, { assets = {}, prompts = {} } = {}) {
  const expressions = Object.entries(character.expressions || {});
  const portrait = assets[spriteId(character.key)] || assets[spriteId(character.key, expressions[0]?.[0])];

  const fields = Object.entries(character.desc || {})
    .map(
      ([k, v]) => `<div class="cast-row"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`
    )
    .join('');

  const cards = expressions
    .map(([key, spec]) => {
      const id = spriteId(character.key, key);
      const url = assets[id];
      const prompt = prompts[id]?.prompt;
      const source = prompts[id]?.source;
      // 表情既可以是一句话，也可以是 {表情, 动作} 两段 —— 后者要分行显示，
      // 直接塞进模板会变成 [object Object]
      const face = typeof spec === 'string' ? spec : spec?.表情 || spec?.face || '';
      const pose = typeof spec === 'string' ? '' : spec?.动作 || spec?.pose || '';
      return `<article class="exp-card">
        ${url ? `<img src="${esc(url)}" alt="${esc(character.name)} ${esc(key)}" loading="lazy">` : ''}
        <div class="exp-body">
          <div class="exp-key">${esc(key)}</div>
          <p class="exp-desc">${esc(face)}</p>
          ${pose ? `<p class="exp-desc exp-pose"><b>动作</b> ${esc(pose)}</p>` : ''}
          ${
            prompt
              ? `<details class="exp-prompt"><summary>生成 prompt${
                  source === 'placeholder' ? '（占位图）' : ''
                }</summary><pre>${esc(prompt)}</pre></details>`
              : ''
          }
        </div>
      </article>`;
    })
    .join('');

  return `<div class="cast">
    <div>
      <div class="cast-portrait">${
        portrait ? `<img src="${esc(portrait)}" alt="${esc(character.name)}">` : ''
      }</div>
      <h2 class="cast-name">${esc(character.name)}</h2>
      <div class="cast-meta">${esc(character.key)} · ${expressions.length} 张立绘${
        character.role ? ` · ${esc(character.role)}` : ''
      }</div>
    </div>
    <dl class="cast-fields">${fields}</dl>
  </div>
  <div class="exp-grid">${cards}</div>`;
}
