/* ═══════════════════════════════════════════════════════════════
   handlers/rebuild.js — 消息列表重建函数
   依赖: bubble.js (bubbles, messagesEl, addBubble, scrollToBottom,
         _lastToolBubble, _parallelBatchEl, dispatchState.reset, 
         activeTools, activeAgents, _globalTimer.clearAll)
         utils/postprocess.js (postProcessMarkdown)
         tool-renderer.js (renderReadFileOutput, renderAnsiDiff)
         handlers.js (_renderMarkdownFallback)
   ═══════════════════════════════════════════════════════════════ */

/* ── 更新时滚动到最底部（带 debounce + 用户滚动保护） ── */
var _scrollTimer = null;

/* ── session_initialized 阶段使用的临时容器（模块级，提升健壮性） ── */
var _toolOutputRows = {};
var _sessionMessages = {};

/**
 * 多次重试滚动到底部（兜底：等待异步渲染完成）
 */
function _retryScrollToBottom(maxRetries, interval) {
  maxRetries = maxRetries || 6;
  interval = interval || 350;
  const el = typeof messagesEl !== 'undefined' ? messagesEl : document.getElementById('messages');
  if (!el) return;
  let count = 0;
  function next() {
    el.scrollTop = el.scrollHeight;
    count++;
    if (count < maxRetries) {
      setTimeout(next, interval);
    }
  }
  next();
}

function _debouncedScrollToBottom() {
  if (_scrollTimer) return;
  _scrollTimer = setTimeout(() => {
    _scrollTimer = null;
    const el = typeof messagesEl !== 'undefined' ? messagesEl : document.getElementById('messages');
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, 0);
}

/**
 * _rebuildMessagesFromData — 从消息数据重建整个消息 DOM
 *
 * ★ Bug 2 修复：从 session_initialized 中提取为公共函数，
 *   供 session_initialized 和 messages_truncated 共用。
 *   避免编辑消息后全量重建导致 DOM 闪烁和滚动位置丢失。
 *
 * 清空 messagesEl，根据 messagesData 重建所有气泡，
 * 同时同步 reset store/bubbles/activeTools/activeAgents 状态。
 *
 * @param {Array} messagesData - 消息列表（与 session_initialized.data.messages 格式一致）
 */
function _rebuildMessagesFromData(messagesData) {
  const preactTools = document.getElementById('preact-tools-container');
  const preactAgents = document.getElementById('preact-agents-container');
  const preactMsgList = document.getElementById('preact-message-list');
  const scrollSentinel = document.getElementById('scroll-sentinel');
  messagesEl.innerHTML = '';
  if (preactTools) messagesEl.appendChild(preactTools);
  if (preactAgents) messagesEl.appendChild(preactAgents);
  if (preactMsgList) messagesEl.appendChild(preactMsgList);
  if (scrollSentinel) messagesEl.appendChild(scrollSentinel);
  bubbles.clear();
  _lastToolBubble = null;
  _parallelBatchEl = null;
  _activeToolCount = 0;
  _globalTimer.clearAll();
  Object.keys(activeTools).forEach(k => { delete activeTools[k]; });
  Object.keys(activeAgents).forEach(k => delete activeAgents[k]);
  dispatchState.reset();
  // 清理气泡回收状态（DOM 回收存档 + restoreObserver）
  if (window.__recycle && typeof window.__recycle.clearAll === 'function') {
    window.__recycle.clearAll();
  }

  if (window.__store) {
    window.__store.resetAll();
  }

  // ★ 无限滚动：消息超过 MAX_VISIBLE_MSGS 时缓存完整数据，只渲染最新 N 条
  // ★ 2026-05-18 修复：跳过全量渲染 + setTimeout 翻转的闪烁路径，
  //   直接以 MAX_VISIBLE 条一次性 slice 渲染初始界面。
  const MAX_VISIBLE = typeof window.MAX_VISIBLE_MSGS !== 'undefined' ? window.MAX_VISIBLE_MSGS : 50;
  if (messagesData && messagesData.length > MAX_VISIBLE) {
    if (typeof window._rebuildMessagesWithRange === 'function') {
      // 直接以 MAX_VISIBLE 条 slice 渲染，无闪烁
      window._rebuildMessagesWithRange(messagesData, 0, messagesData.length, MAX_VISIBLE);
      // 缓存完整数据供「加载更多」展开
      if (typeof window._cachedMessagesData !== 'undefined') {
        window._cachedMessagesData = messagesData;
      }
      return;
    }
  }

  _toolOutputRows = {};
  _sessionMessages = {};

  for (const msg of messagesData) {
    if (msg.role === 'user') {
      const idx = msg.msg_index;
      const userKey = 'user-' + idx;
      if (!bubbles.has(userKey)) {
        const uEl = addBubble('user');
        const uHeader = document.createElement('div');
        uHeader.className = 'header';
        uHeader.innerHTML = '<span class="msg-tag">#' + idx + '</span>';
        uEl.appendChild(uHeader);
        const uContent = document.createElement('div');
        uContent.className = 'bubble-content';
        uContent.textContent = msg.content || '';
        uEl.appendChild(uContent);
        const uTs = document.createElement('div');
        uTs.className = 'timestamp';
        uTs.textContent = new Date().toLocaleTimeString();
        uEl.appendChild(uTs);
        bubbles.set(userKey, uEl);
      }
      _sessionMessages['user-' + idx] = {
        type: 'user', msgIndex: idx, content: msg.content || '',
        timestamp: new Date().toLocaleTimeString(),
      };
    } else if (msg.role === 'assistant') {
      const content = msg.content || '';
      const reasoningContent = msg.reasoning_content || '';
      const idx = msg.content_msg_index ?? msg.reasoning_msg_index;
      const asstKey = 'assistant-' + idx;
      if (!bubbles.has(asstKey) && (reasoningContent || content)) {
        const el = addBubble('answer');
        const header = document.createElement('div');
        header.className = 'header';
        header.innerHTML = '<span class="msg-tag">#' + idx + ' 🤖</span>';
        el.appendChild(header);
        if (reasoningContent) {
          const thinkEl = document.createElement('div');
          thinkEl.className = 'think-section';
          thinkEl.setAttribute('data-raw', reasoningContent);
          thinkEl.innerHTML = _renderMarkdownFallback(reasoningContent || '');
          el.appendChild(thinkEl);
        }
        if (content) {
          const answerEl = document.createElement('div');
          answerEl.className = 'answer-section';
          answerEl.setAttribute('data-raw', content);
          answerEl.innerHTML = _renderMarkdownFallback(content || '');
          el.appendChild(answerEl);
        }
        if (content) {
          const tsEl = document.createElement('div');
          tsEl.className = 'timestamp';
          tsEl.textContent = new Date().toLocaleTimeString();
          el.appendChild(tsEl);
        }
        bubbles.set(asstKey, el);
      }
      _sessionMessages['assistant-' + idx] = {
        type: 'assistant', msgIndex: idx,
        thinkRaw: reasoningContent,
        answerRaw: content,
        timestamp: content ? new Date().toLocaleTimeString() : '',
      };
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        _lastToolBubble = document.createElement('div');
        _lastToolBubble.className = 'bubble tool';
        const isParallel = msg.tool_calls.length > 1;
        if (isParallel) {
          const title = document.createElement('div');
          title.className = 'tool-parallel-header';
          title.innerHTML = '<span class="icon">🔧</span> 并行工具 (' + msg.tool_calls.length + ')';
          _lastToolBubble.appendChild(title);
        } else {
          const header = document.createElement('div');
          header.className = 'tool-header';
          header.textContent = '🔧 工具调用';
          _lastToolBubble.appendChild(header);
        }
        msg.tool_calls.forEach(tc => {
          const name = tc.function?.name || tc.name || '?';
          const tcId = tc.id || '';
          const tIdx = tc.msg_index;
          const container = document.createElement('div');
          container.className = isParallel ? 'tool-parallel-row' : 'tool-single-row';
          if (name === 'subagent') {
            const outputDiv = document.createElement('div');
            outputDiv.className = 'tool-output';
            outputDiv.style.cssText = 'display:block;max-height:none;overflow-y:visible;font-family:var(--font);white-space:normal;';
            outputDiv.dataset.toolId = tcId;
            container.appendChild(outputDiv);
            if (tcId) _toolOutputRows[tcId] = { div: outputDiv, toolName: name };
            _lastToolBubble.appendChild(container);
            return;
          }
          const hdr = document.createElement('div');
          hdr.className = 'tool-header';
          const tag = tIdx !== undefined ? '<span class="msg-tag">#' + tIdx + '</span> ' : '';
          hdr.innerHTML = tag + '<span class="icon small">⚙</span> ' + escapeHtml(name);
          container.appendChild(hdr);
          const phase = document.createElement('div');
          phase.className = 'tool-phase';
          phase.innerHTML = '<span class="tick">✓</span> ' + escapeHtml(name);
          container.appendChild(phase);
          const outputDiv = document.createElement('div');
          outputDiv.className = 'tool-output';
          outputDiv.dataset.toolId = tcId;
          container.appendChild(outputDiv);
          if (tcId) _toolOutputRows[tcId] = { div: outputDiv, toolName: name };
          _lastToolBubble.appendChild(container);
        });
        messagesEl.appendChild(_lastToolBubble);
      }
    } else if (msg.role === 'tool') {
      const output = msg.content || '';
      const tcId = msg.tool_call_id || '';
      const targetOutput = _toolOutputRows[tcId];
      if (targetOutput) {
        targetOutput.div.style.display = 'block';
        targetOutput.div.style.maxHeight = 'none';
        targetOutput.div.style.overflowY = 'visible';
        if (targetOutput.toolName === 'read_file') {
          renderReadFileOutput(targetOutput.div, output);
        } else if (targetOutput.toolName === 'write_file' || targetOutput.toolName === 'update_file') {
          renderAnsiDiff(targetOutput.div, output);
        } else if (targetOutput.toolName === 'subagent') {
          targetOutput.div.style.display = 'block';
          targetOutput.div.style.maxHeight = 'none';
          targetOutput.div.style.overflowY = 'visible';
          targetOutput.div.style.fontFamily = 'var(--font)';
          targetOutput.div.style.whiteSpace = 'normal';
          targetOutput.div.innerHTML = _renderMarkdownFallback(output);
          scheduleTask(() => postProcessMarkdown(targetOutput.div));
        } else {
          targetOutput.div.textContent = output.length > 5000 ? output.slice(0, 5000) + '…' : output;
        }
      } else {
        if (!_lastToolBubble) {
          _lastToolBubble = addBubble('tool');
          const header = document.createElement('div');
          header.className = 'tool-header';
          header.textContent = '🔧 工具输出';
          _lastToolBubble.appendChild(header);
        }
        const fallbackDiv = document.createElement('div');
        fallbackDiv.className = 'tool-output';
        fallbackDiv.style.display = 'block';
        fallbackDiv.textContent = output.length > 5000 ? output.slice(0, 5000) + '…' : output;
        _lastToolBubble.appendChild(fallbackDiv);
      }
    }
  }

  if (Object.keys(_sessionMessages).length > 0) {
    const _store = _st();
    if (_store && typeof _store.addMessagesBatch === 'function') {
      _store.addMessagesBatch(_sessionMessages);
    }
  }
  _debouncedScrollToBottom();
}
