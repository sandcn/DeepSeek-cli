/* ═══════════════════════════════════════════════════════════════
   useStore — Preact Hook：订阅 store 状态变化
   让任何组件都能响应式获取 store 中的状态
   ═══════════════════════════════════════════════════════════════ */
import { useState, useEffect, useRef } from '../lib/hooks.module.js';
import { getState, subscribe } from './store.js';

/**
 * useStore — 订阅 store 状态变化，返回当前状态快照。
 * 组件每次渲染都会获取最新状态。
 *
 * @param {Function} [selector] - 可选的选择器函数 (state) => subset
 * @returns {any} 状态快照或选择器返回值
 *
 * @example
 *   const { tokens, connected } = useStore();
 *   const tools = useStore(s => s.tools);
 */
export function useStore(selector) {
  const selectorRef = useRef(selector);
  selectorRef.current = selector;

  const [snapshot, setSnapshot] = useState(
    () => selector ? selector(getState()) : getState(),
  );

  useEffect(() => {
    const unsub = subscribe((state) => {
      const next = selectorRef.current ? selectorRef.current(state) : state;
      setSnapshot(next);
    });
    return unsub;
  }, []);  // ← 依赖数组为空，selectorRef 维持引用稳定

  return snapshot;
}
