// 可解性搜索在 Web Worker 里跑。Stanley 复刻要把一万多个状态走一遍、好几秒，
// 放在主线程会把整个页面卡住——点不动按钮、切不了标签。
import { solve } from '../explore/engine.js';

self.onmessage = (ev) => {
  self.postMessage(solve(ev.data));
};
