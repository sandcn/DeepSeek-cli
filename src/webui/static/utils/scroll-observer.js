/* ═══════════════════════════════════════════════════════════════
   MutationObserver — 内容变化自动滚动到底部（兜底机制）
   当 messagesEl 内部 DOM 发生任何变化（新增气泡/内容更新/文本变更）
   时自动滚动到底部。如果用户已向上翻阅历史，则不强制滚动，
   等用户回到底部附近后恢复自动滚动。
   作为已有分散 scrollToBottom 调用的统一兜底，确保无遗漏。
   依赖: bubble.js (messagesEl, scrollToBottom)
   ═══════════════════════════════════════════════════════════════ */

/** MutationObserver 实例（全局引用，便于断开） */
let _contentObserver = null;

/** 节流定时器 — 防止快速流式输出时每秒滚动几十次 */
let _observeScrollTimer = null;

/** 用户是否主动向上滚动（非自动滚动触发） */
let _userScrolledUp = false;

/** 用户滚动检测阈值（距底部超过此值视为用户向上翻阅） */
const _SCROLL_UP_THRESHOLD = 150;

/**
 * 用户手动滚动后标记状态并开启自动恢复检测。
 * 当用户回到底部附近时自动恢复自动滚动。
 * 同时控制「滚动到底部」浮动按钮的显示/隐藏。
 */
function _onUserScroll() {
    const el = messagesEl;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    _userScrolledUp = distFromBottom > _SCROLL_UP_THRESHOLD;
    // 浮动按钮显示控制
    const btn = document.getElementById('scroll-bottom-btn');
    if (btn) {
      const show = distFromBottom > 200;
      btn.classList.toggle('hidden', !show);
      btn.classList.toggle('visible', show);
    }
}

/** MutationObserver 回调：内容变化时自动滚动到底部 */
function _onContentMutation(mutations) {
  // 跳过纯属性修改（如 class 切换），只关心实际内容变化
  let hasRealChange = false;
  for (const m of mutations) {
    if (m.type === 'childList' || m.type === 'characterData') {
      hasRealChange = true;
      break;
    }
    // subtree 中文本修改可能表现为 characterData=true
    if (m.type === 'attributes' && (m.attributeName === 'data-raw' || m.attributeName === 'style')) {
      // style/data-raw 变化可能由内容渲染引起，视为内容变化
      hasRealChange = true;
      break;
    }
  }
  if (!hasRealChange) return;

  // 节流：快速变化时合并到一次滚动，每 100ms 最多一次
  if (_observeScrollTimer) return;
  // ★ 100ms 节流替代原 setTimeout(fn, 0)：快速 DOM 变更（如 cmd 大量输出）
  //   聚合为每 100ms 一次滚动，大幅减少强制布局次数。
  _observeScrollTimer = setTimeout(() => {
    _observeScrollTimer = null;
    // ★ 用户已向上翻阅时不强制滚动，等用户回到底部附近后恢复
    if (!_userScrolledUp) {
        scrollToBottom();
    }
  }, 100);
}

/**
 * 启动 MutationObserver — 监听 messagesEl 的所有内容变更
 * 在 addBubble 和 DOM 操作中自动触发，无需手动调用
 */
function _startContentObserver() {
  if (!messagesEl) {
    setTimeout(_startContentObserver, 100);
    return;
  }
  // 已启动则跳过
  if (_contentObserver) return;

  _contentObserver = new MutationObserver(_onContentMutation);
  _contentObserver.observe(messagesEl, {
    childList: true,     // 新增/删除气泡
    subtree: true,       // 气泡内部的内容变更
    characterData: true, // 文本节点变化
    attributes: false,   // 不监听属性变化（减少误触发）
  });
  // ★ 用户滚动事件监听：检测用户是否主动向上翻阅
  messagesEl.addEventListener('scroll', _onUserScroll, { passive: true });
  // ★ 滚动到底部按钮点击事件
  const scrollBtn = document.getElementById('scroll-bottom-btn');
  if (scrollBtn) {
    scrollBtn.addEventListener('click', function() {
      scrollToBottom();
      this.classList.add('hidden');
      this.classList.remove('visible');
      _userScrolledUp = false;
    });
  }
}

/**
 * 断开 MutationObserver（session_initialized 清空 messagesEl 前调用，
 * 避免 observer 持有旧 DOM 引用导致内存泄漏）
 */
function _stopContentObserver() {
  if (_contentObserver) {
    _contentObserver.disconnect();
    _contentObserver = null;
  }
  if (_observeScrollTimer) {
    clearTimeout(_observeScrollTimer);
    _observeScrollTimer = null;
  }
  // ★ 清理用户滚动状态
  if (messagesEl) {
    messagesEl.removeEventListener('scroll', _onUserScroll);
  }
  _userScrolledUp = false;
}

// ── 在脚本加载完成后自动启动 ──
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _startContentObserver);
} else {
  _startContentObserver();
}

// ── 暴露给外部重置/重连场景 ──
window.__scrollObserver = {
  start: _startContentObserver,
  stop: _stopContentObserver,
  scrollToBottom,
};
