/* ═══════════════════════════════════════════════════════════════
   连接管理 + 会话初始化 + 可见性处理
   依赖: ws-client.js, bubble.js, tool-renderer.js, utils/postprocess.js
   ── 子模块 ──
   handlers/rebuild.js   — _rebuildMessagesFromData、滚动函数
   handlers/register.js  — WS 事件注册、DOM 事件绑定、输入管理器
   handlers/gen-status.js — 生成状态弹窗
   handlers/editmsg.js    — 编辑消息弹窗
   handlers/streaming.js  — 流式消息处理器
   handlers/tools.js      — 工具处理器
   handlers/agents.js     — Agent 处理器
   handlers/sessions.js   — 历史会话处理器
   ═══════════════════════════════════════════════════════════════ */

/* ── 中断按钮控制 ──────────────────────────────────────────── */
function _getStopBtn() {
  return document.getElementById('stop-btn');
}

function _showStopBtn() {
  const btn = _getStopBtn();
  if (!btn) return;
  btn.style.display = '';
}

function _hideStopBtn() {
  const btn = _getStopBtn();
  if (!btn) return;
  btn.style.display = 'none';
}

/* ── Markdown 渲染（使用本地 renderMarkdown + 公式预处理） ── */
function _renderMarkdownFallback(text) {
  if (!text) return '';
  try {
    const preprocessed = (typeof window.preprocessMathBeforeRender === 'function')
      ? window.preprocessMathBeforeRender(text)
      : text;
    if (typeof window.renderMarkdown === 'function') return window.renderMarkdown(preprocessed);
    const esc = typeof window.escapeHtml === 'function' ? window.escapeHtml(preprocessed) : preprocessed;
    return esc.replace(/\n/g, '<br>');
  } catch (e) {
    console.warn('[md] renderMarkdownFallback 异常:', e);
    const esc = String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.replace(/\n/g, '<br>');
  }
}

/* ═══════════════════════════════════════════════════════════════
   初始化 — 创建 WSClient + 事件注册
   ═══════════════════════════════════════════════════════════════ */

const ws = new WSClient();
window.ws = ws;

ws._onReconnect = function _onReconnectCleanup() {
  _cleanupAllTimers();
  if (typeof _streamingText !== 'undefined' && _streamingText !== null) {
    _streamingText.clear();
  }
};

/* ── 会话初始化 ────────────────────────────────────────────── */
ws.on('session_initialized', (data) => {
  if (data.model) {
    document.querySelector('.model').textContent = data.model;
    modelNameEl.textContent = '';
  }
  if (data.title) {
    const titleEl = document.getElementById('session-title');
    if (titleEl) {
      titleEl.textContent = data.title;
      titleEl.classList.add('visible');
    }
    document.title = data.title + ' - Chat';
  } else {
    // 无标题时重置显示（新会话）
    const titleEl = document.getElementById('session-title');
    if (titleEl) {
      titleEl.textContent = '';
      titleEl.classList.remove('visible');
    }
    document.title = 'Chat';
  }
  if (data.messages) {
    _rebuildMessagesFromData(data.messages);
  }
  inputEl.disabled = false;
  inputEl.focus();
});

/* ── 连接 ── */
ws.connect();

/* ── 后台任务完成通知 ── */
ws.on('messages_updated', (data) => {
  if (data.messages && data.messages.length > 0) {
    console.log('[handlers] 后台 LLM 任务已完成，优雅重建消息列表 (%d 条)...', data.messages.length);
    // ★ 修复：不再使用 window.location.reload() 全页面刷新，
    //   改为调用 _rebuildMessagesFromData 优雅重建消息 DOM。
    //   这样浏览器在后台标签页时仍能正常更新，不丢失 UI 状态，
    //   且避免了全量重载带来的闪烁和 Preact 状态丢失。
    _rebuildMessagesFromData(data.messages);
    if (data.model) {
      const modelTitle = document.querySelector('.model');
      if (modelTitle) modelTitle.textContent = data.model;
    }
    _debouncedScrollToBottom();
  }
});

/* ── 可见性变化处理：浏览器从后台切回前台时兜底重渲染 + 重置标题 ── */
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    console.log('[handlers] 页面回到前台，重新渲染消息内容...');
    // ★ 重置后台未读计数 & 页面标题
    if (window._bgTitleManager) {
      window._bgTitleManager.reset();
    }
    // 后台标签页时 setTimeout 被节流到 ~1s 间隔，渲染可能滞后或不完整。
    // 切回前台时，对所有 data-raw 元素做一次完整重渲染兜底。
    const rawElements = messagesEl.querySelectorAll('[data-raw]');
    let reRenderCount = 0;
    rawElements.forEach(el => {
      const raw = el.getAttribute('data-raw');
      if (raw && typeof window.renderMarkdown === 'function') {
        const md = window.renderMarkdown(raw);
        if (md !== el.innerHTML) {
          el.innerHTML = DOMPurify ? DOMPurify.sanitize(md) : md;
          reRenderCount++;
          // 对重渲染的内容执行后处理（KaTeX / Mermaid / 复制按钮等）
          if (typeof window.postProcessMarkdown === 'function') {
            setTimeout(() => window.postProcessMarkdown(el), 0);
          }
        }
      }
    });
    if (reRenderCount > 0) {
      console.log('[handlers] 重渲染了 %d 个消息块', reRenderCount);
    }
    // 滚动到底部（后台可能有新消息到达）
    scrollToBottom();
    _retryScrollToBottom(3, 200);
  }
});
