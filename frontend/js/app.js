// 宿主：拉小说列表 → 选一部 → 装引擎 → 三栏各自渲染。
// 播放过程零请求：整部小说（剧本 + 素材索引 + 设定表）一次取完。

import * as cast from './cast.js';
import { Engine } from '../engine/engine.js';
import * as graph from './graph.js';
import { Player } from '../engine/player.js';

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const state = {
  list: [],
  payload: null,
  engine: null,
  tab: 'main',
  music: false,
};

const audio = new Audio();
audio.loop = true;
audio.volume = 0.35;

const player = new Player(document, $('#screen'));

async function json(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

// --- 左栏 -------------------------------------------------------------------

function renderRail() {
  const rail = $('#rail');
  if (!state.list.length) {
    rail.innerHTML = '<div class="rail-empty">还没有小说。<br>跑一次 pipeline 就有了。</div>';
    return;
  }
  rail.innerHTML = state.list
    .map(
      (s) => `<button class="rail-item${s.name === state.payload?.name ? ' is-active' : ''}"
        data-story="${esc(s.name)}">${esc(s.title)}<small>${esc(s.nodes)} 结点 · ${esc(
          s.endings
        )} 结局</small></button>`
    )
    .join('');
}

// --- 右栏 -------------------------------------------------------------------

function renderTabs() {
  const characters = state.payload?.cast?.characters || [];
  $('#tabs').innerHTML = [
    { key: 'main', label: '主线剧情' },
    ...characters.map((c) => ({ key: c.key, label: c.name })),
  ]
    .map(
      (t) =>
        `<button class="tab${t.key === state.tab ? ' is-active' : ''}" data-tab="${esc(
          t.key
        )}">${esc(t.label)}</button>`
    )
    .join('');
}

function takenEdges(engine) {
  const set = new Set(engine.taken.map(([node, index]) => {
    const target = engine.byId.get(node)?.choices?.[index]?.target;
    return `${node}→${target}#${index}`;
  }));
  // 无选项的直接跳转 / 条件跳转：按走过的结点序列补上
  for (let i = 0; i < engine.visited.length - 1; i += 1) {
    set.add(`${engine.visited[i]}→${engine.visited[i + 1]}#d`);
  }
  return set;
}

/** 分支图下面挂一排场景：这部小说画过的背景和定场图，鼠标移上去原地放大看细节。 */
function renderScenes(payload) {
  const assets = payload.assets || {};
  const desc = { ...(payload.cast?.backgrounds || {}), ...(payload.cast?.est || {}) };
  const ids = Object.keys(assets)
    .filter((k) => k.startsWith('bg_') || k.startsWith('est_'))
    .sort();
  if (!ids.length) return '';
  return `<section class="scenes">
    <h3 class="scenes-head">场景 <span>${ids.length} 张</span></h3>
    <div class="scene-grid">${ids
      .map(
        (id) => `<figure class="scene" tabindex="0">
          <span class="scene-shot"><img src="${esc(assets[id])}" alt="${esc(id)}" loading="lazy"></span>
          <figcaption>
            <b>${esc(id.replace(/^(bg|est)_/, ''))}</b>
            <span>${esc(desc[id] || '')}</span>
          </figcaption>
        </figure>`
      )
      .join('')}</div>
  </section>`;
}

function renderPanel() {
  const body = $('#tabbody');
  if (!state.payload) {
    body.innerHTML = '<p class="empty">载入中…</p>';
    return;
  }
  if (state.tab === 'main') {
    const engine = state.engine;
    const story = state.payload.story;
    body.innerHTML = `<div class="graph-head">
        <b>${story.nodes.length}</b> 个结点
        <span>·</span> <b>${(story.endings || []).length}</b> 个结局
        <span>·</span> 走过 <b>${new Set(engine.visited).size}</b> 个
      </div>
      <div class="graph-scroll"><div class="graph">${graph.render(story, {
        visited: new Set(engine.visited),
        current: engine.frame?.node,
        taken: takenEdges(engine),
      })}</div></div>
      ${renderScenes(state.payload)}`;
    return;
  }
  const character = (state.payload.cast?.characters || []).find((c) => c.key === state.tab);
  body.innerHTML = character
    ? cast.render(character, { assets: state.payload.assets, prompts: state.payload.prompts })
    : '<p class="empty">没有这个角色。</p>';
}

// --- 中栏 -------------------------------------------------------------------

function syncMusic() {
  const key = state.engine?.scene?.music;
  const url = key ? state.payload?.assets?.[key] : null;
  const btn = $('#btn-music');
  const ui = state.payload?.meta?.ui || {};
  btn.disabled = !url;
  btn.setAttribute('aria-pressed', String(state.music && Boolean(url)));
  btn.textContent = `♪ ${state.music && url ? ui.music_on || '音乐 开' : ui.music_off || '音乐 关'}`;
  if (!url || !state.music) {
    audio.pause();
    return;
  }
  if (!audio.src.endsWith(url)) audio.src = url;
  audio.play().catch(() => {});
}

function draw() {
  player.draw(state.engine);
  renderPanel();
  syncMusic();
}

async function open(name) {
  state.payload = await json(`/api/stories/${encodeURIComponent(name)}`);
  state.engine = new Engine(state.payload.story);
  state.tab = 'main';
  player.bind(state.payload);
  const meta = state.payload.meta || {};
  $('#stage-kicker').textContent = meta.subtitle || '';
  $('#stage-title').textContent = meta.title || name;
  $('#topbar-note').textContent = meta.tagline || '';
  const ui = meta.ui || {};
  $('#btn-prev').textContent = `← ${ui.prev || '上一步'}`;
  $('#btn-next').textContent = `${ui.next || '下一步'} →`;
  $('#btn-restart').textContent = ui.restart || '重开';
  renderRail();
  renderTabs();
  draw();
}

// --- 事件 -------------------------------------------------------------------

$('#rail').addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-story]');
  if (btn) open(btn.dataset.story);
});

$('#tabs').addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-tab]');
  if (!btn) return;
  state.tab = btn.dataset.tab;
  renderTabs();
  renderPanel();
});

player.onChoose = (index) => {
  state.engine.choose(index);
  draw();
};

$('#btn-next').addEventListener('click', () => {
  state.engine.next();
  draw();
});
$('#btn-prev').addEventListener('click', () => {
  state.engine.prev();
  draw();
});
$('#btn-restart').addEventListener('click', () => {
  state.engine.reset();
  draw();
});
$('#btn-music').addEventListener('click', () => {
  state.music = !state.music;
  syncMusic();
});
$('#screen').addEventListener('click', (ev) => {
  if (ev.target.closest('.choices, .ending')) return;
  if (state.engine?.canNext) {
    state.engine.next();
    draw();
  }
});

document.addEventListener('keydown', (ev) => {
  if (ev.target.matches('input, textarea')) return;
  if (ev.key === 'ArrowRight' || ev.key === ' ' || ev.key === 'Enter') {
    if (state.engine?.canNext) {
      ev.preventDefault();
      state.engine.next();
      draw();
    }
  } else if (ev.key === 'ArrowLeft') {
    state.engine?.prev();
    draw();
  } else if (/^[1-9]$/.test(ev.key)) {
    const choice = state.engine?.choices?.[Number(ev.key) - 1];
    if (choice) {
      state.engine.choose(choice.index);
      draw();
    }
  }
});

// --- 启动 -------------------------------------------------------------------

(async function boot() {
  try {
    state.list = await json('/api/stories');
    renderRail();
    if (state.list.length) await open(state.list[0].name);
    else $('#stage-title').textContent = '还没有小说';
  } catch (err) {
    $('#stage-title').textContent = '后端没连上';
    $('#topbar-note').textContent = String(err.message || err);
  }
})();
