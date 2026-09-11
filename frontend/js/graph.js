// 横向分支图。story.json 已经是一张显式的图，这里只负责排版和画。
//
// 列 = 从起点算起的最长距离（剧情深度），行 = 同一列内的发现顺序，
// 于是三条人物线天然占三行。同源同落点的平行边（两个选项跳同一个结点）
// 会向上下岔开，否则两条线会完全重叠，选项文案也就没地方放了。

const COL_W = 208;
const ROW_H = 108;
const PAD_X = 40;
const PAD_Y = 44;
const R = 8;
const CHOICE_FS = 11.5;
const TERMINALS = new Set(['END', 'DONE']);

function outgoing(node) {
  const out = [];
  for (const d of node.diverts || []) out.push({ to: d.target, label: '', kind: 'divert' });
  (node.choices || []).forEach((c, i) => {
    out.push({ to: c.target, label: c.text, kind: 'choice', index: i });
  });
  if (node.next) out.push({ to: node.next, label: '', kind: 'next' });
  return out.filter((e) => !TERMINALS.has(e.to));
}

export function layout(story) {
  const byId = new Map((story.nodes || []).map((n) => [n.id, n]));
  const depth = new Map();
  const order = [];

  // 最长路径定深度：保证一条边永远从左指向右，不会倒着画
  const seen = new Set();
  (function walk(id, d) {
    if (!byId.has(id)) return;
    if ((depth.get(id) ?? -1) >= d) return;
    depth.set(id, d);
    if (!seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
    for (const e of outgoing(byId.get(id))) walk(e.to, d + 1);
  })(story.start, 0);

  const rows = new Map();
  const perCol = new Map();
  for (const id of order) {
    const col = depth.get(id) ?? 0;
    const row = perCol.get(col) ?? 0;
    perCol.set(col, row + 1);
    rows.set(id, row);
  }

  const pos = new Map();
  for (const id of order) {
    pos.set(id, {
      x: PAD_X + (depth.get(id) ?? 0) * COL_W,
      y: PAD_Y + (rows.get(id) ?? 0) * ROW_H,
    });
  }

  // 平行边分组：同一对 (from → to) 有几条，就上下岔开几条
  const edges = [];
  const groups = new Map();
  for (const id of order) {
    for (const e of outgoing(byId.get(id))) {
      if (!pos.has(e.to)) continue;
      const key = `${id}→${e.to}`;
      const bucket = groups.get(key) || [];
      bucket.push({ ...e, from: id });
      groups.set(key, bucket);
    }
  }
  for (const bucket of groups.values()) {
    bucket.forEach((e, i) => {
      edges.push({ ...e, bow: (i - (bucket.length - 1) / 2) * 30 });
    });
  }

  const width = PAD_X * 2 + (Math.max(0, ...depth.values()) + 1) * COL_W;
  const height = PAD_Y * 2 + Math.max(1, ...perCol.values()) * ROW_H;
  return { pos, edges, order, byId, width, height };
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
// 估宽度：汉字按一个字宽、ASCII 按 0.56 个，够用来画胶囊底
const textWidth = (s, fs) =>
  [...s].reduce((sum, c) => sum + (c.charCodeAt(0) < 256 ? 0.56 : 1), 0) * fs;

function edgePath(a, b, bow) {
  const mx = (a.x + b.x) / 2;
  return `M${a.x + R} ${a.y} C${mx} ${a.y + bow} ${mx} ${b.y + bow} ${b.x - R} ${b.y}`;
}

/** takenSet: "from→to#index" 集合，表示这条边被玩家走过。 */
export function render(story, { visited = new Set(), current = null, taken = new Set() } = {}) {
  const { pos, edges, order, byId, width, height } = layout(story);
  const parts = [];
  // 线全部先画完，胶囊再统一盖上去——不然后画的线会横穿前面画好的胶囊
  const labels = [];

  for (const e of edges) {
    const a = pos.get(e.from);
    const b = pos.get(e.to);
    if (!a || !b) continue;
    const isTaken = taken.has(`${e.from}→${e.to}#${e.index ?? 'd'}`);
    parts.push(`<path class="g-edge${isTaken ? ' is-taken' : ''}" d="${edgePath(a, b, e.bow)}"/>`);
    if (e.label) {
      // 选项文案直接压在线上就糊成一团了（线、文案、结点名三层叠在一起），
      // 所以垫一块和底色同色的胶囊：谁压着谁一目了然，也不用去算避让
      const text = clip(e.label, 16);
      const lx = a.x + (b.x - a.x) * 0.5;
      const ly = (a.y + b.y) / 2 + e.bow * 0.78;
      const w = textWidth(text, CHOICE_FS) + 14;
      const h = CHOICE_FS + 9;
      labels.push(
        `<g class="g-choicebox${isTaken ? ' is-taken' : ''}">
          <rect x="${lx - w / 2}" y="${ly - h / 2}" width="${w}" height="${h}" rx="${h / 2}"/>
          <text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="central"
            >${esc(text)}<title>${esc(e.label)}</title></text>
        </g>`
      );
    }
  }

  parts.push(...labels);

  for (const id of order) {
    const p = pos.get(id);
    const node = byId.get(id);
    const isVisited = visited.has(id);
    const isCurrent = id === current;
    const cls = isCurrent ? 'is-current' : isVisited ? 'is-visited' : '';
    const name = node.label || id;
    parts.push(
      `<text class="g-label${isVisited ? ' is-visited' : ''}" x="${p.x}" y="${p.y - 18}"
        text-anchor="middle">${esc(clip(name, 10))}<title>${esc(name)}</title></text>`,
      `<circle class="g-node ${cls}" cx="${p.x}" cy="${p.y}" r="${R}"/>`,
      `<circle class="g-hit" data-node="${esc(id)}" cx="${p.x}" cy="${p.y}" r="16"><title>${esc(id)}</title></circle>`
    );
  }

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${parts.join('')}</svg>`;
}
