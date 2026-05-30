/* ═══════════════════════════════════════════════════════════════
   utils/core.js — 核心工具函数（纯函数，不依赖 DOM 或其他模块）
   ═══════════════════════════════════════════════════════════════ */

/**
 * 可靠的任务调度器（优化版）
 *
 * 前台：requestAnimationFrame — 与浏览器帧同步，流畅渲染
 * 后台：setTimeout(0) — 轻量且可靠，避免 MessageChannel 高频创建开销
 * 空闲时：requestIdleCallback — 低优先级任务执行，不阻塞交互
 *
 * @param {Function} fn - 要执行的任务
 * @param {Object} [options] - 可选配置
 * @param {boolean} [options.idle=false] - 是否使用空闲调度（低优先级）
 */
function scheduleTask(fn, options) {
  if (options && options.idle && 'requestIdleCallback' in window) {
    requestIdleCallback(() => fn(), { timeout: 300 });
    return;
  }
  if (document.visibilityState === 'hidden') {
    setTimeout(fn, 0);
  } else {
    requestAnimationFrame(fn);
  }
}

/**
 * RAF 批处理器 — 将连续调用合并为单次 RAF 回调
 */
function createBatchRenderer() {
  let pending = false;
  let rafId = null;
  const queue = [];
  
  function flush() {
    rafId = null;
    pending = false;
    const tasks = queue.slice();
    queue.length = 0;
    for (const task of tasks) {
      try { task(); } catch (e) { console.warn('batch task error:', e); }
    }
  }
  
  return {
    schedule(fn) {
      queue.push(fn);
      if (!pending) {
        pending = true;
        rafId = requestAnimationFrame(flush);
      }
    },
    flushNow() {
      if (pending) {
        if (rafId !== null) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
        pending = false;
        flush();
      }
    },
    clear() {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      queue.length = 0;
      pending = false;
    },
  };
}

function escapeHtml(str) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
  };
  return String(str).replace(/[&<>"']/g, function(m) { return map[m]; });
}

function escapeAttr(str) {
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatTokens(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}

function _formatGenTokens(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}

function formatSpeed(s) {
  return s > 0 ? s.toFixed(0) + ' tok/s' : '';
}

/** 粗略估算字符串对应的 token 数（约 4 字符 = 1 token） */
function _estimateTokens(text) {
  return Math.round((text || '').length / 4);
}

/**
 * BackgroundTitleManager — 后台未读消息计数 & 页面标题管理
 *
 * 功能：
 *  - 页面在后台时新消息到达 → 递增计数，标题显示 "(N) Chat"
 *  - 页面回到前台 → 重置计数，恢复原始标题
 *
 * 使用方式：
 *  在 assistant 消息处理器中（handleContentChunk / handleReasoningChunk）：
 *    window._bgTitleManager.notifyNewMessage();
 *
 *  在 visibilitychange 处理器中（重置）：
 *    window._bgTitleManager.reset();
 */
class BackgroundTitleManager {
  constructor() {
    this._unreadCount = 0;
    this._originalTitle = document.title || 'Chat';
    this._isHidden = document.visibilityState === 'hidden';

    // 监听可见性变化
    document.addEventListener('visibilitychange', () => {
      this._isHidden = document.visibilityState === 'hidden';
      if (!this._isHidden) {
        // 回到前台 → 重置计数
        this.reset();
      }
    });
  }

  /** 新消息通知（仅在后台时计数并更新标题） */
  notifyNewMessage() {
    if (!this._isHidden) return;
    this._unreadCount++;
    this._updateTitle();
  }

  /** 重置计数并恢复原始标题 */
  reset() {
    if (this._unreadCount > 0) {
      this._unreadCount = 0;
      // ★ 2026-05-18 修复：使用当前 document.title 作为原始标题，
      //   避免运行时标题被修改后 reset() 恢复回旧标题。
      this._originalTitle = document.title || 'Chat';
      document.title = this._originalTitle;
    }
  }

  /** 获取当前未读数 */
  getUnreadCount() {
    return this._unreadCount;
  }

  /** 更新标题为 "(N) 原始标题" */
  _updateTitle() {
    document.title = `(${this._unreadCount}) ${this._originalTitle}`;
  }
}

/** 标题锚点链接 */
function _addHeadingAnchors(container) {
  container.querySelectorAll('h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]').forEach(heading => {
    if (heading.querySelector('.heading-anchor')) return;
    const anchor = document.createElement('a');
    anchor.className = 'heading-anchor';
    anchor.href = '#' + heading.id;
    anchor.textContent = '#';
    anchor.setAttribute('aria-label', '跳转到此段落');
    anchor.title = '跳转到此段落';
    heading.appendChild(anchor);
  });
}

/* ── 全局导出（供其他模块使用）──────────────────────────── */
if (!window.scheduleTask) window.scheduleTask = scheduleTask;
if (!window.createBatchRenderer) window.createBatchRenderer = createBatchRenderer;

// ── BackgroundTitleManager 全局单例（后台未读消息计数） ──
if (!window._bgTitleManager) {
  window._bgTitleManager = new BackgroundTitleManager();
}


