// 「场景素材」「事件 CG」两页。缩略图悬停放大（见 zoom.js）；每张下面挂着
// 「用在哪几幕」和它真正用过的生成 prompt——剧本里没人用的图标出来，那是白花的钱。

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const IMAGE = /\.(png|webp|jpe?g|svg)(\?|$)/i;

/** 素材 id → 用到它的结点名。结点进场、行内、选项台词里的 tags 都算。 */
export function usage(story) {
  const out = new Map();
  for (const node of story.nodes || []) {
    const lines = [...(node.lines || []), ...(node.choices || []).flatMap((c) => c.lines || [])];
    for (const tags of [node.tags, ...lines.map((l) => l.tags)]) {
      for (const key of ['bg', 'cg', 'video']) {
        const id = tags?.[key];
        if (!id) continue;
        const names = out.get(id) || [];
        const name = node.label || node.id;
        if (!names.includes(name)) names.push(name);
        out.set(id, names);
      }
    }
  }
  return out;
}

export function card(id, { url, desc = '', prompt = '', used = null, wide = false, usedNote = '' }) {
  return `<figure class="gl-card">
    <span class="gl-shot${wide ? ' is-wide' : ''}" tabindex="0" data-zoom="${esc(url)}" data-zoom-cap="${esc(id)}">
      <img src="${esc(url)}" alt="${esc(id)}" loading="lazy">
    </span>
    <figcaption>
      <b>${esc(id.replace(/^(bg|est|cg)_/, ''))}</b>
      <code>${esc(id)}</code>
      ${desc ? `<p title="${esc(desc)}">${esc(desc)}</p>` : ''}
      ${
        used === null
          ? ''
          : `<div class="gl-used">${
              used.length ? `用在 ${esc(used.join('、'))}` : `<span class="is-orphan">${esc(usedNote || '剧本里没用到')}</span>`
            }</div>`
      }
      ${prompt ? `<details class="prompt-fold"><summary>生成 prompt</summary><pre>${esc(prompt)}</pre></details>` : ''}
    </figcaption>
  </figure>`;
}

function page(title, ids, note, cards) {
  if (!ids.length) return `<p class="empty">这部小说还没有${esc(title)}。</p>`;
  return `<h3 class="sec-h">${esc(title)} <span>${ids.length} 张 · ${esc(note)}</span></h3>
    <div class="gl-grid">${cards}</div>`;
}

export function renderScenes({ assets = {}, cast = {}, prompts = {}, story = {} }) {
  const desc = { ...(cast.backgrounds || {}), ...(cast.est || {}) };
  const used = usage(story);
  // 定场图排前面：它是一部小说的第一眼
  const ids = Object.keys(assets)
    .filter((id) => /^(est|bg)_/.test(id) && IMAGE.test(assets[id]))
    .sort((a, b) => Number(a.startsWith('bg_')) - Number(b.startsWith('bg_')) || a.localeCompare(b));
  return page(
    '场景素材',
    ids,
    '背景和定场图都是无人空镜，人物是播放时叠上去的立绘',
    ids
      .map((id) =>
        card(id, {
          url: assets[id],
          desc: desc[id],
          prompt: prompts[id]?.prompt,
          wide: id.startsWith('est_'),
          // 定场图不走 # bg:，它给过场视频当参考图，所以不算「没用到」
          used: id.startsWith('est_') ? null : used.get(id) || [],
        })
      )
      .join('')
  );
}

export function renderCgs({ assets = {}, cast = {}, prompts = {}, story = {} }) {
  const used = usage(story);
  const ids = Object.keys(assets)
    .filter((id) => id.startsWith('cg_') && IMAGE.test(assets[id]))
    .sort();
  return page(
    '事件 CG',
    ids,
    '关键瞬间的整张完成稿，照着立绘生成，长相和服装应当和立绘一致',
    ids
      .map((id) =>
        card(id, { url: assets[id], desc: cast.cgs?.[id], prompt: prompts[id]?.prompt, used: used.get(id) || [] })
      )
      .join('')
  );
}
