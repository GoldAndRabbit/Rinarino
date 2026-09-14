// 宿主：拉小说列表 → 选一部 → 装引擎 → 三栏各自渲染。
// 播放过程零请求：整部小说（剧本 + 素材索引 + 设定表）一次取完。
//
// 右栏是调试面板，两排导航：「剧情」看全局（主线剧情图 / 场景素材 / 事件 CG），
// 「角色」看个人。主线剧情图点结点进「这一幕」，面板里所有图片悬停放大。

import * as cast from './cast.js';
import { Engine } from '../engine/engine.js';
import { ExploreEngine } from '../explore/engine.js';
import * as explorePanel from './explore-panel.js';
import { ExplorePlayer } from '../explore/player.js';
import * as gallery from './gallery.js';
import * as graph from './graph.js';
import { Player } from '../engine/player.js';
import * as scene from './scene.js';
import * as zoom from './zoom.js';

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

const STORY_TABS = [
  { key: 'graph', label: '主线剧情图' },
  { key: 'scenes', label: '场景素材' },
  { key: 'cgs', label: '事件 CG' },
];

const state = {
  list: [],
  payload: null,
  engine: null,
  tab: 'graph', // graph | scenes | cgs | char:<key>
  focus: null, // 主线剧情图里正在看的那一幕
  kind: 'vn', // vn | explore：两种玩法各用各的引擎和播放器，调试面板也各画各的
  music: false,
};

const audio = new Audio();
audio.loop = true;
audio.volume = 0.35;

let player = null;
const zoomer = zoom.install($('#tabbody'));

// 可解性在后台算完了：人还停在这部剧本的状态图上，就把面板重画一遍
explorePanel.whenSolved((story) => {
  if (state.kind === 'explore' && state.payload?.story === story && state.tab === 'graph' && !state.focus) {
    renderPanel();
  }
});

// 当前视图写进地址栏：刷新不丢，调试时也能直接把某一幕的链接发给别人
//   #story=americano&tab=char:zhi   #story=americano&tab=graph&node=opening
function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  return { story: params.get('story'), tab: params.get('tab'), node: params.get('node') };
}

function writeHash() {
  if (!state.payload) return;
  const params = new URLSearchParams({ story: state.payload.name, tab: state.tab });
  if (state.focus) params.set('node', state.focus);
  history.replaceState(null, '', `#${params}`);
}

/** 两种玩法共用一块屏幕容器，换了玩法就换一个播放器挂上去。 */
function mountPlayer(kind) {
  if (player && state.kind === kind) return;
  state.kind = kind;
  const screen = $('#screen');
  screen.className = '';
  if (kind === 'explore') {
    player = new ExplorePlayer(document, screen);
    player.onChange = draw;
  } else {
    player = new Player(document, screen);
    player.onChoose = (index) => {
      state.engine.choose(index);
      draw();
    };
  }
}

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
  const tab = (key, label) =>
    `<button class="tab${key === state.tab ? ' is-active' : ''}" data-tab="${esc(key)}">${esc(label)}</button>`;
  $('#tabs').innerHTML = `
    <div class="tabrow"><span class="tabrow-h">剧情</span>${STORY_TABS.map((t) =>
      tab(t.key, t.key === 'graph' && state.kind === 'explore' ? '状态图' : t.label)
    ).join('')}</div>
    <div class="tabrow"><span class="tabrow-h">角色</span>${
      characters.map((c) => tab(`char:${c.key}`, c.name)).join('') ||
      '<span class="tabrow-empty">这部没有角色设定</span>'
    }</div>`;
}

function takenEdges(engine) {
  const set = new Set(
    engine.taken.map(([node, index]) => {
      const target = engine.byId.get(node)?.choices?.[index]?.target;
      return `${node}→${target}#${index}`;
    })
  );
  // 无选项的直接跳转 / 条件跳转：按走过的结点序列补上
  for (let i = 0; i < engine.visited.length - 1; i += 1) {
    set.add(`${engine.visited[i]}→${engine.visited[i + 1]}#d`);
  }
  return set;
}

function renderGraph() {
  const engine = state.engine;
  const story = state.payload.story;
  return `<div class="graph-head">
      <b>${story.nodes.length}</b> 个结点
      <span>·</span> <b>${(story.endings || []).length}</b> 个结局
      <span>·</span> 走过 <b>${new Set(engine.visited).size}</b> 个
      <span class="graph-hint">点结点看这一幕</span>
    </div>
    <div class="graph-scroll"><div class="graph">${graph.render(story, {
      visited: new Set(engine.visited),
      current: engine.frame?.node,
      taken: takenEdges(engine),
    })}</div></div>`;
}

function renderPanel() {
  zoomer.hide();
  const body = $('#tabbody');
  const payload = state.payload;
  if (!payload) {
    body.innerHTML = '<p class="empty">载入中…</p>';
    return;
  }
  if (state.tab === 'graph' && state.kind === 'explore') {
    body.innerHTML = state.focus
      ? explorePanel.renderNode(payload, state.engine, state.focus)
      : explorePanel.renderMap(payload, state.engine);
  } else if (state.tab === 'graph') {
    body.innerHTML = state.focus
      ? scene.render(payload.story, state.focus, {
          assets: payload.assets,
          cast: payload.cast,
          meta: payload.meta,
          order: graph.layout(payload.story).order,
          playing: state.engine.frame?.node,
        })
      : renderGraph();
  } else if (state.tab === 'scenes') {
    body.innerHTML = gallery.renderScenes(payload);
  } else if (state.tab === 'cgs') {
    body.innerHTML = gallery.renderCgs(payload);
  } else {
    const key = state.tab.replace(/^char:/, '');
    const character = (payload.cast?.characters || []).find((c) => c.key === key);
    body.innerHTML = character
      ? cast.render(character, { assets: payload.assets, prompts: payload.prompts, cast: payload.cast })
      : '<p class="empty">没有这个角色。</p>';
  }
  writeHash();
}

function focusScene(id) {
  if (!state.payload?.story?.nodes?.some((n) => n.id === id)) return;
  state.focus = id;
  renderPanel();
  $('#tabbody').scrollTop = 0;
}

function leaveScene() {
  state.focus = null;
  renderPanel();
}

/** 调试用：不管变量对不对得上，直接把播放器切到这一幕。能「上一步」退回来。 */
function jumpTo(id) {
  const engine = state.engine;
  if (state.kind === 'explore') {
    engine.jump(id);
  } else {
    engine.pushHistory();
    engine.finished = false;
    engine.ending = null;
    engine.enterNode(id);
  }
  draw();
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
  // 只有剧情图跟着播放进度变；素材页和角色页每步都重画的话，展开的 prompt 会被收起来
  if (state.tab === 'graph') renderPanel();
  syncMusic();
}

async function open(name, view = {}) {
  state.payload = await json(`/api/story/${encodeURIComponent(name)}.json`);
  const kind = state.payload.story?.engine === 'explore' ? 'explore' : 'vn';
  mountPlayer(kind);
  state.engine = kind === 'explore' ? new ExploreEngine(state.payload.story) : new Engine(state.payload.story);
  $('#vars').innerHTML = '';
  const known = new Set(['graph', 'scenes', 'cgs', ...(state.payload.cast?.characters || []).map((c) => `char:${c.key}`)]);
  state.tab = known.has(view.tab) ? view.tab : 'graph';
  state.focus = state.tab === 'graph' && state.payload.story.nodes.some((n) => n.id === view.node) ? view.node : null;
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
  player.draw(state.engine);
  renderPanel();
  syncMusic();
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
  if (state.tab !== 'graph') state.focus = null;
  renderTabs();
  renderPanel();
  $('#tabbody').scrollTop = 0;
});

$('#tabbody').addEventListener('click', (ev) => {
  const node = ev.target.closest('[data-node]');
  if (node) return focusScene(node.dataset.node);
  const go = ev.target.closest('[data-scene-go]');
  if (go) return focusScene(go.dataset.sceneGo);
  if (ev.target.closest('[data-scene-back]')) return leaveScene();
  const jump = ev.target.closest('[data-scene-jump]');
  if (jump) return jumpTo(jump.dataset.sceneJump);
  return undefined;
});

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
  // 探索玩法的屏幕自己管点击：点台词、点行动、点背包各有各的意思
  if (state.kind !== 'vn') return;
  if (ev.target.closest('.choices, .ending')) return;
  if (state.engine?.canNext) {
    state.engine.next();
    draw();
  }
});

document.addEventListener('keydown', (ev) => {
  if (ev.target.matches('input, textarea')) return;
  if (ev.key === 'Escape' && state.focus) {
    leaveScene();
  } else if (ev.key === 'ArrowRight' || ev.key === ' ' || ev.key === 'Enter') {
    if (ev.target.closest('button, summary')) return; // 按钮自己的回车/空格别被抢走
    if (state.engine?.canNext) {
      ev.preventDefault();
      state.engine.next();
      draw();
    }
  } else if (ev.key === 'ArrowLeft') {
    state.engine?.prev();
    draw();
  } else if (state.kind === 'vn' && /^[1-9]$/.test(ev.key)) {
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
    state.list = await json('/api/stories.json');
    renderRail();
    const view = readHash();
    const first = state.list.find((s) => s.name === view.story) || state.list[0];
    if (first) await open(first.name, view);
    else $('#stage-title').textContent = '还没有小说';
  } catch (err) {
    $('#stage-title').textContent = '后端没连上';
    $('#topbar-note').textContent = String(err.message || err);
  }
})();
