/* ═══════════════════════════════════════════════════════════════
   app.js — Preact 应用入口（ES Module）
   渐进式迁移桥梁：Preact 组件渲染到现有 DOM 中，
   与现有的 Vanilla JS 代码共存，逐步接管 UI 渲染。
   ═══════════════════════════════════════════════════════════════ */
import { h, render } from './lib/preact.module.js';
import htm from './lib/htm.module.js';
import { setGlobalRenderMarkdown } from './md-engine.js';
import { AgentCardList } from './components/AgentCard.js';
import {
  addTool, updateTool, removeTool, clearTools, getTools,
  addAgent, updateAgent, removeAgent, clearAgents, getAgents,
  setDispatchState, resetDispatchState, getDispatchState,
  addMessage, updateMessage, removeMessage, getMessages,
  addMessagesBatch,
  getState, subscribe,
  setNotificationsPaused,
  resetAll,
} from './state/store.js';

let html;
try {
  html = htm.bind(h);
} catch (e) {
  console.error('[app] htm.bind(h) 失败:', e);
  html = function() { return ''; };
}

/* ═══════════════════════════════════════════════════════════════
   全局 Bridge — 为 handlers.js（非 module 脚本）暴露 store 方法
   handlers.js 在 connected 后通过 window.__store 访问
   ═══════════════════════════════════════════════════════════════ */
window.__store = {
  addTool, updateTool, removeTool, clearTools, getTools,
  addAgent, updateAgent, removeAgent, clearAgents, getAgents,
  setDispatchState, resetDispatchState, getDispatchState,
  addMessage, updateMessage, removeMessage, getMessages,
  addMessagesBatch,
  setNotificationsPaused,
  resetAll,
  getState, subscribe,
};

/* ── 在 DOM 加载完成后初始化 ────────────────────────────── */
function init() {
  // ★ UC 浏览器兼容：如果不支持 importmap（Preact 未正常加载），跳过 Preact 渲染
  //   vanilla DOM handlers.js 仍能正常工作
  if (typeof render !== 'function') {
    console.warn('[app] Preact 不可用（importmap 不受支持），使用 vanilla DOM 模式');
    // 仍然需要设置 renderMarkdown
    setGlobalRenderMarkdown();
    // 重新渲染历史消息气泡（纯 DOM 操作，不依赖 Preact）
    if (typeof window.renderMarkdown === 'function') {
      const messagesEl = document.getElementById('messages');
      if (messagesEl) {
        const sections = messagesEl.querySelectorAll('[data-raw]');
        let reRenderCount = 0;
        sections.forEach(section => {
          const raw = section.getAttribute('data-raw');
          if (raw) {
            const md = window.renderMarkdown(raw);
            if (md !== section.innerHTML) {
              section.innerHTML = md;
              reRenderCount++;
            }
          }
        });
        if (reRenderCount > 0) {
          console.log('[app] 重新渲染了', reRenderCount, '个历史消息气泡');
        }
      }
    }
    return;
  }

  // ── 将 react-markdown 引擎挂到 window.renderMarkdown（替代 fallback） ──
  setGlobalRenderMarkdown();

  // ── 渲染 AgentCardList 到 #preact-agents-container ──────
  const agentsContainer = document.getElementById('preact-agents-container');
  if (agentsContainer) {
    try {
      render(html`<${AgentCardList} />`, agentsContainer);
    } catch (e) {
      console.error('[Preact] AgentCardList 渲染失败:', e);
    }
  }

  // ★ ToolRowList 和 MessageList 不再通过 Preact 渲染到 DOM
  //   工具气泡和消息气泡完全由 vanilla DOM（handlers.js）管理，
  //   避免 Preact 与 vanilla DOM 双路径导致的闪烁/全屏/顺序错乱。
  //   Store 同步保留，供 editmsg 等非渲染功能使用。

  // ── 重新渲染已有的历史消息气泡 ─────────────────────────
  // session_initialized 事件可能在 init() 之前触发，
  // 导致历史消息使用 fallback 渲染（转义文本）。
  // 现在 window.renderMarkdown 已正确设置，通过 data-raw 重新渲染。
  if (typeof window.renderMarkdown === 'function') {
    const messagesEl = document.getElementById('messages');
    if (messagesEl) {
      const sections = messagesEl.querySelectorAll('[data-raw]');
      let reRenderCount = 0;
      sections.forEach(section => {
        const raw = section.getAttribute('data-raw');
        if (raw) {
          const md = window.renderMarkdown(raw);
          if (md !== section.innerHTML) {
            section.innerHTML = md;
            reRenderCount++;
          }
        }
      });
      if (reRenderCount > 0) {
        console.log('[app] 重新渲染了', reRenderCount, '个历史消息气泡');
      }
    }
  }
}

// 直接执行初始化
try {
  init();
} catch (e) {
  console.error('[app] init() 执行失败:', e);
}

/* ── 全局未捕获错误处理（防止 UC 浏览器页面崩溃） ── */
window.addEventListener('error', function(e) {
  // 过滤掉 importmap 相关的模块加载错误（UC 浏览器不支持时正常预期）
  if (e.message && (
    e.message.includes('importmap') ||
    e.message.includes('Import map') ||
    e.message.includes('module') ||
    e.message.includes('preact')
  )) {
    e.preventDefault();
    console.warn('[compat] 已拦截模块加载错误（UC 浏览器兼容模式）');
    return;
  }
  console.error('[global] 未捕获错误:', e.message || e);
});
