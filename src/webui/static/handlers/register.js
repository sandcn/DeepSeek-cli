/* ═══════════════════════════════════════════════════════════════
   handlers/register.js — WS 事件注册 + DOM 事件绑定 + 输入管理器
   依赖: handlers/rebuild.js (_rebuildMessagesFromData)
         handlers/streaming.js (handleUserMessage, handleReasoningChunk, etc.)
         handlers/tools.js (handleToolParsing, handleToolStarted, etc.)
         handlers/agents.js (handleAgentAdded, handleAgentStatus, etc.)
         handlers/gen-status.js (_showGenStatus, _hideGenStatus, etc.)
         handlers/editmsg.js (_openEditMsgModal, _closeEditMsgModal, etc.)
         handlers/sessions.js (_openHistoryModal, _closeHistoryModal, etc.)
         handlers.js (ws)
   ═══════════════════════════════════════════════════════════════ */

/* ── 注册事件处理器 ────────────────────────────────────────── */
ws.on('user_message', window.__streaming.handleUserMessage);
// 编辑消息角标：每来一条用户消息 +1
ws.on('user_message', () => { if (window._incEditMsgCount) window._incEditMsgCount(); });
ws.on('reasoning_chunk', window.__streaming.handleReasoningChunk);
ws.on('content_chunk', window.__streaming.handleContentChunk);
ws.on('phase_done', window.__streaming.handlePhaseDone);
ws.on('tool_parsing', window.handleToolParsing);
ws.on('tool_started', window.handleToolStarted);
ws.on('tool_output_chunk', window.handleToolOutput);
ws.on('tool_done', window.handleToolDone);
ws.on('tool_summary', window.handleToolSummary);
ws.on('model_phase', window.handleModelPhase);
ws.on('usage_update', window.handleUsageUpdate);
ws.on('user_select_needed', window.handleUserSelectNeeded);
ws.on('agent_added', window.handleAgentAdded);
ws.on('agent_status', window.handleAgentStatus);
ws.on('agent_tool_parsing', window.handleAgentToolParsing);
ws.on('agent_tool_started', window.handleAgentToolStarted);
ws.on('agent_tool_done', window.handleAgentToolDone);
ws.on('agent_phase', window.handleAgentPhase);
ws.on('agent_usage', window.handleAgentUsage);
ws.on('agent_result', window.handleAgentResult);
ws.on('status_popup', window.handleStatusPopup);

// ── 内联事件处理器 ──
ws.on('tool_status', (data) => {
  const tool = activeTools[data.label];
  if (tool) tool.phaseEl.textContent = data.status;
});

ws.on('tool_batch_start', (data) => {
  const el = addBubble('tool');
  el.innerHTML = '<div class="tool-header">📦 批量执行: ' + escapeHtml((data.names || []).join(', ')) + '</div>';
  _parallelBatchEl = el;
  _debouncedScrollToBottom();
});

ws.on('display_stopped', () => {});
ws.on('live_input', () => {});
ws.on('live_output', () => {});
ws.on('parse_info', () => {});

ws.on('command_error', (data) => {
  const el = addBubble('tool');
  el.style.cssText = 'border-left: 3px solid var(--error);';
  el.innerHTML = '<div class="tool-header" style="color: var(--error);">❌ 命令执行失败</div>'
    + '<div class="tool-phase">' + escapeHtml(data.command || '') + '</div>'
    + '<div class="tool-output" style="display: block; color: var(--error);">'
    + escapeHtml(data.error || '未知错误') + '</div>';
  _debouncedScrollToBottom();
});

ws.on('command_output', (data) => {
  const el = addBubble('tool');
  el.style.cssText = 'border-left: 3px solid var(--text-dim); align-self: center;';
  const header = document.createElement('div');
  header.className = 'tool-header';
  header.textContent = '💬 ' + (data.level === 'warning' ? '⚠️ ' : '') + '命令输出';
  el.appendChild(header);
  const output = document.createElement('div');
  output.className = 'tool-output';
  output.style.display = 'block';
  output.style.whiteSpace = 'pre-wrap';
  output.style.wordBreak = 'break-word';
  output.style.fontFamily = 'var(--font)';
  output.style.fontSize = '13px';
  // ★ 使用 ansiToHtml 渲染终端颜色（同工具输出气泡）
  output.innerHTML = (typeof window.ansiToHtml === 'function' ? window.ansiToHtml(data.text || '') : escapeHtml(data.text || ''));
  el.appendChild(output);
  const ts = document.createElement('div');
  ts.className = 'timestamp';
  ts.textContent = new Date().toLocaleTimeString();
  el.appendChild(ts);
  _debouncedScrollToBottom();
});

ws.on('session_saved', (data) => {
  let msg = '✅ 会话已保存';
  if (data.session_id) msg += ' (ID: ' + data.session_id + ')';
  const el = addBubble('tool');
  el.style.cssText = 'border-left: 3px solid var(--success); align-self: center; text-align: center; font-size: 13px;';
  el.textContent = msg;
  const ts = document.createElement('div');
  ts.className = 'timestamp';
  ts.textContent = '连接即将关闭…';
  el.appendChild(ts);
  _debouncedScrollToBottom();
});

/* ── 重连后状态同步（get_full_state 响应） ── */
ws.on('full_state', (data) => {
  console.log('[register] 收到全量状态同步 (%d 条消息)', data.messages ? data.messages.length : 0);
  // 更新编辑消息角标（统计已有用户消息数）
  if (data.messages && window._setEditMsgCount) {
    const userCount = data.messages.filter(m => m.role === 'user').length;
    window._setEditMsgCount(userCount);
  }
  // 更新会话标题
  if (data.title) {
    const titleEl = document.getElementById('session-title');
    if (titleEl) {
      titleEl.textContent = data.title;
      titleEl.classList.add('visible');
    }
    document.title = data.title + ' - Chat';
  }
  // 收到从后端同步的完整会话状态，重建消息列表
  if (data.messages && data.messages.length > 0) {
    _rebuildMessagesFromData(data.messages);
    if (data.model) {
      const modelTitle = document.querySelector('.model');
      if (modelTitle) modelTitle.textContent = data.model;
    }
    _debouncedScrollToBottom();
  }
});

// ── 设置重连状态同步回调（WSClient 重连后自动请求全量状态） ──
ws._onReconnectStateSync = function _requestFullStateOnReconnect() {
  console.log('[register] 重连后请求全量状态同步...');
  if (window.ws) {
    window.ws.send({ type: 'get_full_state' });
  }
};

/* ── 服务器 ping 响应（无操作，仅用于保活） ── */
ws.on('pong', () => {
  // 收到服务器 pong 响应，连接正常，无需处理
});

/* ── 模型切换 WS 事件 ── */
ws.on('models_list', (data) => {
  const models = data.models || [];
  const current = data.current || '';
  if (!models.length) return;
  const overlay = document.getElementById('select-overlay');
  const dialog = document.getElementById('select-dialog');
  dialog.querySelector('.select-title').textContent = '🤖 选择模型';
  const optionsDiv = dialog.querySelector('.select-options');
  optionsDiv.innerHTML = '';
  optionsDiv.dataset.multi = 'false';
  for (const model of models) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'model-select';
    input.value = model;
    if (model === current) { input.checked = true; }
    label.appendChild(input);
    const textSpan = document.createElement('span');
    const isActive = model === current;
    textSpan.textContent = isActive ? model + ' ✓' : model;
    if (isActive) { textSpan.style.color = '#90caf9'; }
    label.appendChild(textSpan);
    optionsDiv.appendChild(label);
  }
  const confirmBtn = dialog.querySelector('.btn-confirm');
  const cancelBtn = dialog.querySelector('.btn-cancel');
  const cleanup = () => {
    overlay.classList.add('hidden');
    confirmBtn.onclick = null;
    cancelBtn.onclick = null;
  };
  confirmBtn.onclick = () => {
    const checked = optionsDiv.querySelector('input[name="model-select"]:checked');
    if (checked) {
      const selectedModel = checked.value;
      if (selectedModel !== current) {
        ws.send({ type: 'set_model', model: selectedModel });
      }
    }
    cleanup();
  };
  cancelBtn.onclick = () => { cleanup(); };
  overlay.classList.remove('hidden');
});

ws.on('model_changed', (data) => {
  const modelTitle = document.querySelector('.model');
  if (modelTitle && data.model) { modelTitle.textContent = data.model; }
});

/* ── 历史会话 WS 事件 ── */
ws.on('sessions_list', (data) => {
  _renderHistoryList(data.sessions, data.current_id);
});

ws.on('session_deleted', () => {
  ws.send({ type: 'get_sessions' });
});

ws.on('session_renamed', () => {
  // 重命名成功后刷新会话列表
  ws.send({ type: 'get_sessions' });
});

/* ── 自动标题生成 WS 事件 ── */
ws.on('session_title_updated', (data) => {
  // 更新顶栏标题显示
  const titleEl = document.getElementById('session-title');
  if (titleEl && data.title) {
    titleEl.textContent = data.title;
    titleEl.classList.add('visible');
  }
  // 更新浏览器标签页标题
  document.title = data.title ? data.title + ' - Chat' : 'Chat';
  // 刷新历史会话列表中的标题
  ws.send({ type: 'get_sessions' });
});

ws.on('session_loaded', (data) => {
  if (data.messages) {
    _stopContentObserver();
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
    _globalTimer.clearAll();
    Object.keys(activeTools).forEach(k => { delete activeTools[k]; });
    Object.keys(activeAgents).forEach(k => { delete activeAgents[k]; });
    dispatchState.reset();
    if (window.__store) window.__store.resetAll();

    const _toolOutputRows = {};
    for (const msg of data.messages) {
      if (msg.role === 'user') {
        const idx = msg.msg_index;
        const key = 'user-' + idx;
        if (!bubbles.has(key)) {
          const el = addBubble('user');
          const header = document.createElement('div');
          header.className = 'header';
          header.innerHTML = '<span class="msg-tag">#' + idx + '</span>';
          el.appendChild(header);
          const content = document.createElement('div');
          content.className = 'bubble-content';
          content.textContent = msg.content || '';
          el.appendChild(content);
          const ts = document.createElement('div');
          ts.className = 'timestamp';
          ts.textContent = new Date().toLocaleTimeString();
          el.appendChild(ts);
          bubbles.set(key, el);
        }
        const st = _st();
        if (st) st.addMessage('user-' + idx, {
          type: 'user', msgIndex: idx, content: msg.content || '',
          timestamp: new Date().toLocaleTimeString(),
        });
      } else if (msg.role === 'assistant') {
        const content = msg.content || '';
        const reasoning = msg.reasoning_content || '';
        const idx = msg.content_msg_index ?? msg.reasoning_msg_index;
        const asstKey = 'assistant-' + idx;
        if (!bubbles.has(asstKey) && (reasoning || content)) {
          const el = addBubble('answer');
          const header = document.createElement('div');
          header.className = 'header';
          header.innerHTML = '<span class="msg-tag">#' + idx + ' 🤖</span>';
          el.appendChild(header);
          if (reasoning) {
            const thinkEl = document.createElement('div');
            thinkEl.className = 'think-section';
            thinkEl.setAttribute('data-raw', reasoning);
            thinkEl.innerHTML = _renderMarkdownFallback(reasoning || '');
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
            const ts = document.createElement('div');
            ts.className = 'timestamp';
            ts.textContent = new Date().toLocaleTimeString();
            el.appendChild(ts);
          }
          bubbles.set(asstKey, el);
        }
        const st2 = _st();
        if (st2) st2.addMessage('assistant-' + idx, {
          type: 'assistant', msgIndex: idx,
          thinkRaw: reasoning, answerRaw: content,
          timestamp: content ? new Date().toLocaleTimeString() : '',
        });
      }
    }
    scrollToBottom();
    _retryScrollToBottom();
    _startContentObserver();
  }
  if (data.model) {
    const modelTitle = document.querySelector('.model');
    if (modelTitle) modelTitle.textContent = data.model;
  }
});

/* ── 清空消息 ── */
ws.on('clear_messages', () => {
  _rebuildMessagesFromData([]);
});

/* ── 编辑消息 WS 事件 ── */
// 初始化/刷新时更新角标计数（session_initialized 包含完整消息列表）
ws.on('session_initialized', (data) => {
  if (data.messages && window._setEditMsgCount) {
    const userCount = data.messages.filter(m => m.role === 'user').length;
    window._setEditMsgCount(userCount);
  }
});
// 加载历史会话时更新角标计数
ws.on('session_loaded', (data) => {
  if (data.messages && window._setEditMsgCount) {
    const userCount = data.messages.filter(m => m.role === 'user').length;
    window._setEditMsgCount(userCount);
  }
});
ws.on('messages_list', (data) => {
  _editMsgState.messages = data.messages || [];
  // 更新编辑消息角标精确计数
  if (window._setEditMsgCount) {
    const userCount = (_editMsgState.messages || []).filter(m => m.role === 'user').length;
    window._setEditMsgCount(userCount);
  }
  _renderEditMsgList(_editMsgState.messages);
});
ws.on('edit_messages_result', (data) => {
  if (data.success && data.action === 'edit' && data.prefill) {
    inputEl.value = data.prefill;
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 150) + 'px';
    inputEl.focus();
  } else if (!data.success) {
    // ★ Bug 1 修复：编辑失败时给用户反馈
    console.error('[editmsg] 编辑消息失败:', data.error || '未知错误');
    const errMsg = data.error || '操作失败，请重试';
    // 使用 status_popup 或气泡显示错误
    const el = addBubble('tool');
    el.style.cssText = 'border-left: 3px solid var(--error); align-self: center;';
    el.innerHTML = '<div class="tool-header" style="color: var(--error);">❌ 编辑失败</div>'
      + '<div class="tool-phase">' + escapeHtml(errMsg) + '</div>';
    _debouncedScrollToBottom();
  }
});

/* ── 编辑消息：截断后刷新消息列表（替代 session_initialized 全量重建） ── */
ws.on('messages_truncated', (data) => {
  // ★ Bug 2 修复：使用专用的重建函数，避免触发 session_initialized 的额外副作用
  if (data.messages) {
    _rebuildMessagesFromData(data.messages);
  }
  if (data.model) {
    const modelTitle = document.querySelector('.model');
    if (modelTitle) modelTitle.textContent = data.model;
  }
});

/* ═══════════════════════════════════════════════════════════════
   输入管理器
   ═══════════════════════════════════════════════════════════════ */

// ── 输入历史（↑/↓ 方向键切换） ──────────────────────────
const _INPUT_HISTORY_KEY = 'chat_input_history';
const _INPUT_HISTORY_MAX = 50;
let _inputHistory = [];
let _historyIdx = -1;     // -1 = 草稿模式，0 = 最旧消息，len-1 = 最新消息
let _savedDraft = '';     // 首次按 ↑ 时保存的当前输入内容

// 从 localStorage 恢复历史
try {
  const saved = localStorage.getItem(_INPUT_HISTORY_KEY);
  if (saved) {
    _inputHistory = JSON.parse(saved);
    if (!Array.isArray(_inputHistory)) _inputHistory = [];
  }
} catch (_) { _inputHistory = []; }

/** 保存输入历史到 localStorage（节流：每次 send 后保存） */
function _saveInputHistory() {
  try {
    localStorage.setItem(_INPUT_HISTORY_KEY, JSON.stringify(_inputHistory));
  } catch (_) { /* 存储满时静默失败 */ }
}

/** 将光标移到输入框末尾 */
function _moveCursorToEnd(el) {
  const len = el.value.length;
  if (el.setSelectionRange) {
    el.setSelectionRange(len, len);
  }
}

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  // ── 记录到输入历史 ──
  // 去重：如果与最后一条相同则不重复记录
  if (_inputHistory.length === 0 || _inputHistory[_inputHistory.length - 1] !== text) {
    _inputHistory.push(text);
    if (_inputHistory.length > _INPUT_HISTORY_MAX) {
      _inputHistory = _inputHistory.slice(-_INPUT_HISTORY_MAX);
    }
    _saveInputHistory();
  }
  _historyIdx = -1;
  _savedDraft = '';

  inputEl.value = '';
  inputEl.style.height = 'auto';
  ws.send({ type: 'user_message', content: text });
}

inputEl.addEventListener('keydown', (e) => {
  // ── Ctrl+Enter 发送 ──
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendMessage();
    return;
  }

  // ── ↑ 上一条历史 ──
  if (e.key === 'ArrowUp' && _inputHistory.length > 0) {
    e.preventDefault();
    if (_historyIdx === -1) {
      // 首次按 ↑：保存草稿，切到最新一条
      _savedDraft = inputEl.value;
      _historyIdx = _inputHistory.length - 1;
    } else if (_historyIdx > 0) {
      _historyIdx--;
    } else {
      return; // 已到最旧，不动
    }
    inputEl.value = _inputHistory[_historyIdx];
    _moveCursorToEnd(inputEl);
    return;
  }

  // ── ↓ 下一条历史 ──
  if (e.key === 'ArrowDown') {
    if (_historyIdx === -1) return; // 已在新消息模式
    e.preventDefault();
    if (_historyIdx < _inputHistory.length - 1) {
      _historyIdx++;
      inputEl.value = _inputHistory[_historyIdx];
    } else {
      // 回到草稿
      _historyIdx = -1;
      inputEl.value = _savedDraft;
    }
    _moveCursorToEnd(inputEl);
    return;
  }
});

sendBtn.addEventListener('click', sendMessage);

const _stopBtn = _getStopBtn();
if (_stopBtn) {
  _stopBtn.addEventListener('click', () => {
    ws.send({ type: 'stop_generating' });
  });
}

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 150) + 'px';
});

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== inputEl && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    inputEl.focus();
  }
});

/* ── DOMContentLoaded 事件绑定 ── */
document.addEventListener('DOMContentLoaded', () => {
  const modelTitle = document.querySelector('.model');
  if (modelTitle) {
    modelTitle.addEventListener('click', () => {
      ws.send({ type: 'get_models' });
    });
  }

  const editBtn = document.getElementById('editmsg-btn');
  if (editBtn) {
    editBtn.addEventListener('click', _openEditMsgModal);
  }
  const closeBtn = document.getElementById('editmsg-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closeEditMsgModal);
  }
  const overlay = document.getElementById('editmsg-overlay');
  if (overlay) {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) _closeEditMsgModal();
    });
  }
  const rewriteBtn = document.getElementById('editmsg-rewrite-btn');
  if (rewriteBtn) {
    rewriteBtn.addEventListener('click', () => _doEditMsgAction('edit'));
  }
  const cancelActionBtn = document.getElementById('editmsg-cancel-btn');
  if (cancelActionBtn) {
    cancelActionBtn.addEventListener('click', () => {
      document.getElementById('editmsg-actions').classList.add('hidden');
      document.querySelectorAll('.editmsg-msg-row.selected').forEach(r => r.classList.remove('selected'));
      _editMsgState.selectedIdx = -1;
      // ★ Bug 5 修复：取消时关闭弹窗，交互更流畅
      _closeEditMsgModal();
    });
  }

  // 页面加载后获取初始消息数（编辑消息角标）
  setTimeout(() => { if (window._refreshEditMsgCount) window._refreshEditMsgCount(); }, 600);

  const historyBtn = document.getElementById('history-btn');
  if (historyBtn) {
    historyBtn.addEventListener('click', _openHistoryModal);
  }
  const historyCloseBtn = document.getElementById('history-close');
  if (historyCloseBtn) {
    historyCloseBtn.addEventListener('click', _closeHistoryModal);
  }
  const historyOverlay = document.getElementById('history-overlay');
  if (historyOverlay) {
    historyOverlay.addEventListener('click', (e) => {
      if (e.target === historyOverlay) _closeHistoryModal();
    });
  }
});
