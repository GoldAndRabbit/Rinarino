// 悬停放大：右栏里任何带 data-zoom 的元素，鼠标移上去就在旁边浮一张大图。
//
// 不用原地 transform: scale —— 右栏是 overflow:auto 的滚动容器，原地放大的图会被
// 容器边缘裁掉，靠边那几张根本看不全。浮层挂在 body 上、position: fixed，谁也裁不到它。
//
//   <img data-zoom="/assets/x/sprite_a.webp" data-zoom-cap="normal" data-zoom-kind="sprite">

const GAP = 16;
const MAX_H = 860;

export function install(root) {
  const layer = document.createElement('div');
  layer.className = 'zoom';
  layer.hidden = true;
  layer.innerHTML = '<img alt=""><div class="zoom-cap"></div>';
  document.body.appendChild(layer);
  const img = layer.querySelector('img');
  const cap = layer.querySelector('.zoom-cap');
  let current = null;

  function place(target) {
    const r = target.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const ratio =
      img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : r.width / r.height || 0.5625;
    // 先按视口高度给满，再看左右哪边空得多；放不下就按宽度收
    const room = Math.max(vw - r.right, r.left) - GAP * 2;
    let h = Math.min(vh - GAP * 2, MAX_H);
    let w = h * ratio;
    if (w > room) {
      w = Math.max(room, 160);
      h = w / ratio;
    }
    const left = vw - r.right >= r.left ? r.right + GAP : r.left - GAP - w;
    const top = Math.min(Math.max(GAP, r.top + r.height / 2 - h / 2), vh - GAP - h);
    Object.assign(layer.style, { left: `${left}px`, top: `${top}px`, width: `${w}px`, height: `${h}px` });
  }

  function show(target) {
    const src = target.dataset.zoom;
    if (!src) return;
    current = target;
    // 立绘是透明底，垫棋盘格才看得出抠得干不干净；场景图垫深色
    layer.classList.toggle('is-sprite', target.dataset.zoomKind === 'sprite');
    cap.textContent = target.dataset.zoomCap || '';
    cap.hidden = !cap.textContent;
    if (img.getAttribute('src') !== src) {
      img.onload = () => current === target && place(target);
      img.src = src;
    }
    place(target);
    layer.hidden = false;
  }

  function hide() {
    current = null;
    layer.hidden = true;
  }

  root.addEventListener('mouseover', (ev) => {
    const target = ev.target.closest('[data-zoom]');
    if (target && target !== current) show(target);
  });
  root.addEventListener('mouseout', (ev) => {
    const target = ev.target.closest('[data-zoom]');
    if (target && !target.contains(ev.relatedTarget)) hide();
  });
  root.addEventListener('focusin', (ev) => {
    const target = ev.target.closest('[data-zoom]');
    if (target) show(target);
  });
  root.addEventListener('focusout', hide);
  // 滚动时浮层对不上原图的位置了，直接收起比跟着跑更不晃眼
  root.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
  return { hide };
}
