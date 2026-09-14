// 探索解谜的调试面板：状态图 + 玩家状态 + 可解性，点结点看这一处的全部规则。
//
// 剧情引擎那张分支图是「从左往右走到底」的树形；探索解谜是地图，处处都能走回头路，
// 套那套排版会在环上无限递归。所以另排：按离起点的最短距离分列，
// 来回都能走的两个地方只画一条线，单向的画箭头，要条件才能走的画虚线、标上缺什么。

import { solve, validate } from '../explore/engine.js';

const COL_W = 150;
const ROW_H = 78;
const PAD_X = 50;
const PAD_Y = 66; // 顶上多留一截：同一行上的长边要从那一行的结点名字上面拱过去
const R = 8;

const KIND_LABEL = { go: '前往', act: '调查', use: '使用道具', code: '密码' };
const NODE_KIND = { room: '地点', view: '近景', scene: '场面', ending: '结局' };

const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const textWidth = (s, fs) => [...s].reduce((sum, c) => sum + (c.charCodeAt(0) < 256 ? 0.58 : 1), 0) * fs;

// validate 很快，当场算。solve 要把状态空间走一遍（Stanley 复刻好几秒），放主线程会卡死页面，
// 所以丢给 Web Worker：先画「计算中」，算完通知宿主重画。一部剧本各算一次就够
const problemsOf = new WeakMap();
const solvedOf = new WeakMap();
let onSolved = () => {};

/** 宿主登记：某部剧本的可解性在后台算完了。 */
export function whenSolved(fn) {
  onSolved = fn;
}

function problemsFor(story) {
  if (!problemsOf.has(story)) problemsOf.set(story, validate(story));
  return problemsOf.get(story);
}

/** 算好了就返回结果，还在算返回 null（第一次调用时顺手开工）。 */
function solvedFor(story) {
  if (solvedOf.has(story)) return solvedOf.get(story);
  solvedOf.set(story, null);
  const finish = (result) => {
    solvedOf.set(story, result);
    onSolved(story);
  };
  try {
    const worker = new Worker(new URL('./explore-solve-worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = (ev) => {
      worker.terminate();
      finish(ev.data);
    };
    worker.onerror = () => {
      worker.terminate();
      finish(solve(story));
    };
    worker.postMessage(story);
  } catch {
    finish(solve(story)); // 没有 Worker 的环境只好在主线程算
  }
  return null;
}

export function layout(story) {
  const byId = new Map((story.nodes || []).map((n) => [n.id, n]));
  const depth = new Map([[story.start, 0]]);
  const order = [story.start];
  for (let i = 0; i < order.length; i += 1) {
    for (const c of byId.get(order[i])?.choices || []) {
      if (c.next && byId.has(c.next) && !depth.has(c.next)) {
        depth.set(c.next, depth.get(order[i]) + 1);
        order.push(c.next);
      }
    }
  }
  for (const n of story.nodes || []) {
    if (!depth.has(n.id)) {
      depth.set(n.id, Math.max(0, ...depth.values()) + 1);
      order.push(n.id);
    }
  }
  // 结局统一收到最右边一列：它们没有出口，按最短距离排会挤在枢纽（前厅）旁边，
  // 通往结局的那几条长条件标签就正好压在结局结点上
  const isEnding = (id) => byId.get(id)?.kind === 'ending';
  const lastRoom = Math.max(0, ...order.filter((id) => !isEnding(id)).map((id) => depth.get(id)));
  for (const id of order) if (isEnding(id)) depth.set(id, lastRoom + 1);
  const ranked = [...order.filter((id) => !isEnding(id)), ...order.filter(isEnding)];

  const perCol = new Map();
  const pos = new Map();
  for (const id of ranked) {
    const col = depth.get(id);
    const row = perCol.get(col) || 0;
    perCol.set(col, row + 1);
    pos.set(id, { x: PAD_X + col * COL_W, y: PAD_Y + row * ROW_H });
  }

  // 同一对地方之间的边合成一条：地图上「来」和「回」是同一条走廊
  const pairs = new Map();
  for (const node of story.nodes || []) {
    for (const c of node.choices || []) {
      if (!c.next || !pos.has(c.next) || c.next === node.id) continue;
      const key = [node.id, c.next].sort().join('|');
      const pair = pairs.get(key) || { a: node.id, b: c.next, dirs: new Set(), gates: [] };
      pair.dirs.add(`${node.id}>${c.next}`);
      if (c.condition) pair.gates.push(c);
      pairs.set(key, pair);
    }
  }

  return {
    byId,
    pos,
    order: ranked,
    pairs: [...pairs.values()],
    width: PAD_X * 2 + Math.max(0, ...depth.values()) * COL_W + 40,
    height: PAD_Y * 2 + (Math.max(1, ...perCol.values()) - 1) * ROW_H + 30,
  };
}

/** 一条边的控制点。edgePath 画它、pointAt 在它上面找位置——两边必须是同一条曲线，所以只算一次。 */
function curve(a, b) {
  if (Math.abs(a.x - b.x) < 1) {
    return [
      [a.x + R, a.y],
      [a.x + 56, (a.y + b.y) / 2],
      [b.x + R, b.y],
    ];
  }
  const dir = b.x > a.x ? 1 : -1;
  const mx = (a.x + b.x) / 2;
  const long = Math.abs(a.x - b.x) > COL_W * 1.5;
  // 跨好几列的边往上拱，别从中间那几个结点身上穿过去。起止在同一行的拱得更高：
  // 不然整条线贴着那一行走，一路上的结点名字全压在线上，条件标签也找不到地方放
  const lift = !long ? 0 : Math.abs(a.y - b.y) < 1 ? -62 : -36;
  return [
    [a.x + R * dir, a.y],
    [mx, a.y + lift],
    [mx, b.y + lift],
    [b.x - R * dir, b.y],
  ];
}

function edgePath(a, b) {
  const [[x0, y0], ...rest] = curve(a, b);
  return `M${x0} ${y0} ${rest.length === 2 ? 'Q' : 'C'}${rest.map(([x, y]) => `${x} ${y}`).join(' ')}`;
}

/** 边上第 t 处的坐标（二次或三次贝塞尔）。 */
function pointAt(a, b, t) {
  const pts = curve(a, b);
  const u = 1 - t;
  const w = pts.length === 3 ? [u * u, 2 * u * t, t * t] : [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
  return {
    x: pts.reduce((sum, [x], i) => sum + w[i] * x, 0),
    y: pts.reduce((sum, [, y], i) => sum + w[i] * y, 0),
  };
}

const overlaps = (a, b) => a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2;

/** 每个结点占的地方：圆点和上面的名字。条件标签要躲开这些。 */
function nodeBoxes(L) {
  return L.order.flatMap((id) => {
    const p = L.pos.get(id);
    const w = textWidth(clip(L.byId.get(id).label || id, 6), 12.5);
    return [
      { x1: p.x - R - 3, x2: p.x + R + 3, y1: p.y - R - 3, y2: p.y + R + 3 },
      { x1: p.x - w / 2 - 3, x2: p.x + w / 2 + 3, y1: p.y - 30, y2: p.y - 9 },
    ];
  });
}

function mapSvg(engine, L) {
  const visited = new Set(engine.state.visited);
  const parts = [
    `<defs><marker id="xm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
      orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"/></marker></defs>`,
  ];
  const labels = [];
  const taken = nodeBoxes(L);
  for (const pair of L.pairs) {
    const oneWay = pair.dirs.size === 1;
    let [from, to] = oneWay ? [...pair.dirs][0].split('>') : [pair.a, pair.b];
    if (!oneWay && L.pos.get(from).x > L.pos.get(to).x) [from, to] = [to, from];
    const A = L.pos.get(from);
    const B = L.pos.get(to);
    const gated = pair.gates.length > 0;
    const open = gated && pair.gates.some((c) => engine.test(c.condition));
    const walked = visited.has(pair.a) && visited.has(pair.b);
    const cls = ['xm-edge', walked && 'is-walked', gated && 'is-gated', open && 'is-open'].filter(Boolean).join(' ');
    parts.push(`<path class="${cls}" d="${edgePath(A, B)}"${oneWay ? ' marker-end="url(#xm-arrow)"' : ''}/>`);
    if (gated) {
      const why = engine.explain(pair.gates[0].condition);
      const text = `${open ? '✓' : '🔒'} ${clip(why, 11)}`;
      const w = textWidth(text, 11) + 14;
      // 沿着线挨个试几个位置，挑第一个不压结点、不压别的标签的。固定放正中不行：
      // 通往结局的边横跨整张图，正中是一排房间；固定贴着结局也不行，走顶上那一行的边会压在温室上。
      const tries = L.byId.get(to)?.kind === 'ending' ? [0.85, 0.93, 0.74, 0.62, 0.5] : [0.5, 0.38, 0.62, 0.28, 0.72];
      const at = (t) => {
        const { x, y } = pointAt(A, B, t);
        return { x, y, box: { x1: x - w / 2, x2: x + w / 2, y1: y - 10, y2: y + 10 } };
      };
      const spot = tries.map(at).find((c) => !taken.some((b) => overlaps(b, c.box))) || at(tries[0]);
      taken.push(spot.box);
      const { x: lx, y: ly } = spot;
      labels.push(`<g class="xm-gate${open ? ' is-open' : ''}">
        <rect x="${lx - w / 2}" y="${ly - 9}" width="${w}" height="18" rx="9"/>
        <text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="central">${esc(text)}<title>${esc(why)}</title></text>
      </g>`);
    }
  }
  parts.push(...labels);
  for (const id of L.order) {
    const p = L.pos.get(id);
    const node = L.byId.get(id);
    const isVisited = visited.has(id);
    const cls = engine.current === id ? 'is-current' : isVisited ? 'is-visited' : '';
    parts.push(
      `<text class="g-label${isVisited ? ' is-visited' : ''}" x="${p.x}" y="${p.y - 16}" text-anchor="middle">${esc(
        clip(node.label || id, 6)
      )}</text>`,
      node.kind === 'ending'
        ? `<rect class="xm-end ${cls}" x="${p.x - R}" y="${p.y - R}" width="${R * 2}" height="${R * 2}" rx="3"/>`
        : `<circle class="g-node ${cls}" cx="${p.x}" cy="${p.y}" r="${R}"/>`,
      `<circle class="g-hit" data-node="${esc(id)}" cx="${p.x}" cy="${p.y}" r="16"><title>${esc(id)}</title></circle>`
    );
  }
  return `<svg width="${L.width}" height="${L.height}" viewBox="0 0 ${L.width} ${L.height}">${parts.join('')}</svg>`;
}

function stateCard(story, engine) {
  const spec = story.flags || {};
  const keys = [...new Set([...Object.keys(spec), ...Object.keys(engine.state.flags)])];
  const flags = keys
    .map((k) => {
      const v = engine.state.flags[k];
      const on = v !== undefined && v !== false && v !== 0 && v !== null;
      return `<div class="xm-flag${on ? ' is-on' : ''}"><i>${on ? '●' : '○'}</i><span>${esc(spec[k]?.label || k)}</span>
        <code>${esc(k)}${v === undefined ? '' : ` = ${esc(JSON.stringify(v))}`}</code></div>`;
    })
    .join('');
  const inv =
    engine.state.inventory.map((id) => `<span class="xm-chip">${esc(engine.itemName(id))}</span>`).join('') ||
    '<span class="xm-muted">空</span>';
  const stashes = Object.entries(engine.state.stashes || {})
    .map(
      ([name, items]) => `<div class="xm-sub">被收进「${esc(name)}」</div><div class="xm-chips">${items
        .map((id) => `<span class="xm-chip xm-muted">${esc(engine.itemName(id))}</span>`)
        .join('')}</div>`
    )
    .join('');
  return `<section class="xm-card">
    <h3 class="sec-h">State <span>玩家现在是什么情况——所有条件读的都是这里</span></h3>
    <div class="xm-sub">flags</div>${flags || '<p class="xm-muted">剧本里没有 flag</p>'}
    <div class="xm-sub">背包</div><div class="xm-chips">${inv}</div>${stashes}
    <div class="xm-sub">走过的地方</div>
    <p class="xm-path">${esc(engine.state.visited.map((id) => engine.nodeLabel(id)).join(' → '))}</p>
    <div class="xm-sub">做过的事 · ${engine.state.done.length}</div>
    <p class="xm-path xm-mono">${esc(engine.state.done.join(' · ') || '还没有')}</p>
  </section>`;
}

function solveCard(story, solved) {
  if (!solved) {
    return `<section class="xm-card">
      <h3 class="sec-h">可解性 <span>计算中…</span></h3>
      <p class="xm-note">正在后台把状态空间走一遍。房间多的剧本要几秒，不影响先玩着。</p>
    </section>`;
  }
  const byId = new Map((story.nodes || []).map((n) => [n.id, n]));
  const endings = story.endings || [];
  const rows = endings
    .map((id) => {
      const node = byId.get(id);
      const title = node?.ending?.title || node?.label || id;
      const path = solved.endings[id];
      return path
        ? `<details class="xm-solve"><summary><b>${esc(title)}</b>最少做 ${path.length} 件事</summary>
            <ol>${path.map((step) => `<li>${esc(step)}</li>`).join('')}</ol></details>`
        : `<div class="xm-solve is-bad"><b>${esc(title)}</b>走不到</div>`;
    })
    .join('');
  const verdict = solved.missing.length
    ? `<span class="is-bad">${solved.missing.length} 个结局走不到</span>`
    : `${endings.length} 个结局都走得到`;
  return `<section class="xm-card">
    <h3 class="sec-h">可解性 <span>${verdict} · 搜了 ${solved.explored} 个状态${solved.exhausted ? '' : '（没搜完）'}</span></h3>
    <p class="xm-note">机器把每一种走法都试过：用对的道具、输对的密码、检查每样东西、组合每个配方。
      道具被用掉、门再也打不开这种卡死，在这儿就会暴露。</p>
    ${rows}
  </section>`;
}

export function renderMap(payload, engine) {
  const story = payload.story;
  const problems = problemsFor(story);
  const solved = solvedFor(story);
  const L = layout(story);
  return `<div class="graph-head">
      <b>${story.nodes.length}</b> 个结点
      <span>·</span> <b>${(story.endings || []).length}</b> 个结局
      <span>·</span> 去过 <b>${engine.state.visited.length}</b> 处
      <span class="graph-hint">实线随便走 · 虚线要条件 · 点结点看这一处的规则</span>
    </div>
    <div class="graph-scroll"><div class="graph">${mapSvg(engine, L)}</div></div>
    ${
      problems.length
        ? `<section class="xm-problems"><b>剧本有 ${problems.length} 处问题</b>
            <ul>${problems.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></section>`
        : ''
    }
    <div class="xm-cols">${stateCard(story, engine)}${solveCard(story, solved)}</div>`;
}

// --- 单个结点 -----------------------------------------------------------------

function effectText(engine, e) {
  if (e.type === 'set') return `${engine.story.flags?.[e.key]?.label || e.key} = ${JSON.stringify(e.value)}`;
  if (e.type === 'inc') return `${engine.story.flags?.[e.key]?.label || e.key} +${e.value ?? 1}`;
  if (e.type === 'add_item') return `获得「${engine.itemName(e.item)}」`;
  if (e.type === 'remove_item') return `失去「${engine.itemName(e.item)}」`;
  if (e.type === 'stash') return `背包整个收进「${e.name}」`;
  if (e.type === 'unstash') return `从「${e.name}」找回东西`;
  return `？${e.type}`;
}

function eventText(ev) {
  if (ev.type === 'narrate') return `旁白「${clip(ev.text || '', 40)}」`;
  if (ev.type === 'say') return `${ev.speaker}「${clip(ev.text || '', 40)}」`;
  const what = { bg: '换背景', cg: 'CG', music: '音乐', sfx: '音效', toast: '提示' }[ev.type] || ev.type;
  return `${what} ${ev.id || ev.text || ''}`;
}

function blockLines(engine, block) {
  const fx = (block?.effects || []).map((e) => `<span class="xm-fx">${esc(effectText(engine, e))}</span>`).join('');
  const evs = (block?.events || []).map((ev) => `<span class="xm-ev">${esc(eventText(ev))}</span>`).join('');
  return `${fx ? `<div class="xm-line"><b>Effect</b>${fx}</div>` : ''}${
    evs ? `<div class="xm-line"><b>Event</b>${evs}</div>` : ''
  }`;
}

function choiceCard(engine, node, c, i) {
  const kind = c.kind || (c.next ? 'go' : 'act');
  const id = c.id || `${node.id}#${i}`;
  const ok = engine.test(c.condition);
  const tags = [c.once && '一次性', c.hidden && '不满足时藏起来', engine.state.done.includes(id) && '已做过']
    .filter(Boolean)
    .map((t) => `<span class="sd-tag">${t}</span>`)
    .join('');
  let need = '';
  if (kind === 'use') {
    need = `<div class="xm-line"><b>要用</b>「${esc(engine.itemName(c.use))}」${c.consume ? '，用掉' : '，不消耗'}</div>`;
  } else if (kind === 'code') {
    need = `<div class="xm-line"><b>密码</b><code>${esc(c.code)}</code>${
      engine.state.attempts[id] ? `<span class="xm-muted">已试错 ${engine.state.attempts[id]} 次</span>` : ''
    }</div>`;
  }
  const fail = c.fail_text || (c.fail?.events || []).map(eventText).join(' · ');
  return `<div class="xm-choice${ok ? '' : ' is-blocked'}">
    <div class="xm-choice-h"><span class="xm-kind is-${kind}">${KIND_LABEL[kind] || kind}</span>
      <b>${esc(c.text)}</b><code>${esc(id)}</code>${tags}</div>
    ${
      c.condition
        ? `<div class="xm-line"><b>条件</b><span class="xm-cond ${ok ? 'is-true' : 'is-false'}">${esc(
            engine.explain(c.condition)
          )} ${ok ? '✓' : '✗'}</span></div>`
        : ''
    }
    ${need}${blockLines(engine, c)}
    ${c.next ? `<div class="xm-line"><b>去</b><button class="sd-link" data-scene-go="${esc(c.next)}">${esc(engine.nodeLabel(c.next))}</button></div>` : ''}
    ${fail ? `<div class="xm-line xm-muted"><b>失败时</b>${esc(fail)}</div>` : ''}
  </div>`;
}

export function renderNode(payload, engine, id) {
  const story = payload.story;
  const L = layout(story);
  const node = L.byId.get(id);
  if (!node) return '<p class="empty">没有这一处。</p>';
  const at = L.order.indexOf(id);
  const prev = at > 0 ? L.order[at - 1] : null;
  const next = at < L.order.length - 1 ? L.order[at + 1] : null;

  const variants = (list, field) =>
    list
      .map(
        (v) => `<div class="xm-rule"><span class="xm-cond ${
          v.condition ? (engine.test(v.condition) ? 'is-true' : 'is-false') : ''
        }">${esc(v.condition ? engine.explain(v.condition) : '否则')}</span><p>${esc(v[field])}</p></div>`
      )
      .join('');
  const text = Array.isArray(node.text) ? variants(node.text, 'text') : `<p class="xm-text">${esc(node.text || '')}</p>`;
  const bg = Array.isArray(node.bg) ? variants(node.bg, 'id') : node.bg ? `<p class="xm-text xm-mono">${esc(node.bg)}</p>` : '';

  const incoming = (story.nodes || [])
    .flatMap((n) => (n.choices || []).filter((c) => c.next === id).map((c) => ({ n, c })))
    .map(
      ({ n, c }) => `<div class="xm-line"><button class="sd-link" data-scene-go="${esc(n.id)}">${esc(n.label || n.id)}</button>
        「${esc(c.text)}」${c.condition ? `<span class="xm-cond ${engine.test(c.condition) ? 'is-true' : 'is-false'}">${esc(engine.explain(c.condition))}</span>` : ''}</div>`
    )
    .join('');
  const hooks = [
    ['每次进来', node.enter],
    ['第一次进来', node.first],
  ]
    .filter(([, b]) => b?.effects?.length || b?.events?.length)
    .map(([title, b]) => `<section class="sd-block"><h4 class="sd-h">${title}</h4>${blockLines(engine, b)}</section>`)
    .join('');

  return `<article class="sd">
    <header class="sd-head">
      <div class="sd-title">
        <h3>${esc(node.label || id)}</h3>
        <span class="sd-sub">${esc(NODE_KIND[node.kind] || '结点')} · ${(node.choices || []).length} 个行动</span>
        ${engine.current === id ? '<span class="sd-live">玩家在这儿</span>' : ''}
      </div>
      <nav class="sd-nav">
        <button class="ctl" ${prev ? `data-scene-go="${esc(prev)}"` : 'disabled'}>← 上一处</button>
        <button class="ctl" ${next ? `data-scene-go="${esc(next)}"` : 'disabled'}>下一处 →</button>
        <button class="ctl" data-scene-back>返回</button>
        <button class="ctl ctl-primary" data-scene-jump="${esc(id)}">跳到这里</button>
      </nav>
    </header>
    <div class="sd-id">${esc(id)}</div>
    <section class="sd-block"><h4 class="sd-h">描述 <small>进来时念的话，可以随状态变，取第一条满足的</small></h4>${text}</section>
    ${bg ? `<section class="sd-block"><h4 class="sd-h">背景</h4>${bg}</section>` : ''}
    ${hooks}
    <section class="sd-block">
      <h4 class="sd-h">行动 <small>条件后面的 ✓ ✗ 是按玩家现在的 State 算的</small></h4>
      ${(node.choices || []).map((c, i) => choiceCard(engine, node, c, i)).join('') || '<p class="sd-note">没有行动。</p>'}
    </section>
    <section class="sd-block"><h4 class="sd-h">从哪儿能到这里</h4>${incoming || '<p class="sd-note">起点，或者没有路通到这里。</p>'}</section>
  </article>`;
}
