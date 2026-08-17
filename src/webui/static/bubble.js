/* ═══════════════════════════════════════════════════════════════
   气泡管理 — DOM 引用 + 状态 + 创建函数
   依赖: utils.js (escapeHtml)
         utils/timer.js (_globalTimer)
         utils/scroll-observer.js (_startContentObserver, _stopContentObserver, scrollToBottom)
   ═══════════════════════════════════════════════════════════════ */

/* ── DOM 元素引用 ────────────────────────────────────────── */
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
const stopBtn = document.getElementById('stop-btn');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const modelNameEl = document.getElementById('model-name');

/* ── 气泡状态 ────────────────────────────────────────────── */
// 气泡映射表（基于后端 msg_index 管理）
// key 格式: "user-{idx}" | "assistant-{idx}" | "tool-{label}"
const bubbles = new Map();

// 跟踪活跃的工具气泡: label → {el, phaseEl, outputEl, metaEl, headerEl}
const activeTools = {};
/** ★ 性能优化：活跃工具计数器，替代 Object.keys(activeTools).length */
let _activeToolCount = 0;
// 跟踪活跃的 Agent 气泡: label → {el, statusEl, phaseEl, toolsEl, metaEl, toolRecords}
const activeAgents = {};

/** #16 修复：将 dispatch 状态封装为单一对象 */
const dispatchState = {
  map: new Map(),        // dispatch_label → { containerEl, agentsContainer, phaseEl }
  labelOrder: [],        // dispatch_label 顺序列表
  agentCounter: 0,       // 已路由 agent 数
  batchDone: 0,          // 完成计数器
  pendingAgents: [],     // agent_added 缓冲区
  _generation: 0,        // 每次 reset() 递增，用于检测新 dispatch 是否已启动
  _resetTimer: null,     // pending timeout 引用，可提前取消

  reset() {
    this.map.clear();
    this.labelOrder = [];
    this.agentCounter = 0;
    this.batchDone = 0;
    this.pendingAgents = [];
    this._generation++;
    this._resetTimer = null;
  }
};

// 历史渲染中最后一个工具气泡（用于 session_initialized）
let _lastToolBubble = null;
// 并行工具批处理气泡
let _parallelBatchEl = null;

/* ── Store 访问辅助（handlers/tools.js 等依赖此函数） ───── */
// 使用 var 避免被 agents.js 中的 const 同名声明冲突
var _st = function() { return window.__store; };

/* ── 连接状态更新 ────────────────────────────────────────── */
function updateConnectionStatus(connected) {
  statusDot.className = 'status-dot' + (connected ? '' : ' disconnected');
  statusText.textContent = connected ? '已连接' : '已断开';
  sendBtn.disabled = !connected;
  if (!connected && stopBtn) stopBtn.style.display = 'none';
  // 连接状态已通过 DOM 更新，无需同步 store
}

/* ── 气泡创建辅助 ────────────────────────────────────────── */
function addBubble(className) {
  const el = document.createElement('div');
  el.className = 'bubble ' + className + ' bubble-enter';
  messagesEl.appendChild(el);
  // 动画结束后移除 bubble-enter 类，防止已有气泡重复触发 bubbleIn 动画
  el.addEventListener('animationend', function handler() {
    el.classList.remove('bubble-enter');
    el.removeEventListener('animationend', handler);
  }, { once: true });
  // ★ 修复：将 scroll-sentinel 移至 messagesEl 末尾，确保它始终在最新气泡之后
  //   这样 _debouncedScrollToBottom 中的 sentinel.scrollIntoView 才能正确滚动到实际底部
  _ensureSentinelAtBottom();
  return el;
}

/**
 * 确保 scroll-sentinel 在 messagesEl 的末尾（在所有动态气泡之后）
 * 修复滚动锚点失效导致的「大模型生成时跳转到第一个气泡」问题
 */
/**
 * ★ 性能优化：缓存 scroll-sentinel 引用，避免每次滚动时 DOM 查询
 * 在 _ensureSentinelAtBottom 中初始化，后续 scrollToBottom/debouncedScroll 直接复用
 */
let _sentinelRef = null;

/** 刷新缓存的 sentinel 引用（在 DOM 重建后调用） */
function _refreshSentinelRef() {
  _sentinelRef = document.getElementById('scroll-sentinel');
}

function _ensureSentinelAtBottom() {
  if (!_sentinelRef) {
    _refreshSentinelRef();
  } else if (!document.body.contains(_sentinelRef)) {
    // ★ 悬空引用修复：缓存引用指向的元素已被从 DOM 移除
    _refreshSentinelRef();
  }
  if (_sentinelRef && messagesEl.lastChild !== _sentinelRef) {
    messagesEl.appendChild(_sentinelRef);
  }
}

/** 获取共享的气泡容器（解决双轨渲染冲突） */
function getBubbleContainer() {
  return document.getElementById('preact-message-list') || messagesEl;
}

function getOrCreateBubble(key, className, buildFn) {
  let el = bubbles.get(key);
  if (!el) {
    el = addBubble(className);
    if (buildFn) buildFn(el);
    bubbles.set(key, el);
  }
  return el;
}

function scrollToBottom() {
  // ★ 修复：直接设置 scrollTop = scrollHeight 而非 scrollIntoView
  //   scrollIntoView 在以下场景不可靠：
  //   ① content-visibility: auto 导致离屏气泡高度为估算值，锚点位置不准
  //   ② flex 容器（#messages 是 display:flex）下部分浏览器行为不一致
  //   scrollTop = scrollHeight 是标准行为，100% 可靠
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

/* ── subagent 辅助 ─────────────────────────────────── */
/** 在 subagent 容器内创建 agent 行（终端树形风格） */
function _createSubagentRow(data, dispatchContainer) {
  const row = document.createElement('div');
  row.className = 'dispatch-agent-row';
  row.dataset.agentLabel = data.label;

  const descText = escapeHtml(data.description || data.label);
  const agentsContainer = dispatchContainer.agentsContainer;
  const childCount = agentsContainer.childElementCount;

  // 更新前一个 agent 的连接符（从 └ 改为 ├）
  if (childCount > 0) {
    const prevRow = agentsContainer.children[childCount - 1];
    const prevConnector = prevRow.querySelector('.tree-connector');
    if (prevConnector) prevConnector.textContent = '├';
    prevRow.dataset.isLast = 'false';
  }

  const subConnector = '│';

  // ═══ 树形结构：可见元素 ═══
  // ── Agent 标题行 ──
  const titleLine = document.createElement('div');
  titleLine.className = 'agent-title-line';
  titleLine.innerHTML = '<span class="tree-connector">└</span>'
    + ' <span class="status-icon status-icon-dot"></span>'
    + ' <span class="agent-desc">' + descText + '</span>'
    + ' <span class="agent-token-count" style="display:none;"></span>'
    + ' <span class="agent-elapsed"></span>';
  row.appendChild(titleLine);

  // ── 阶段指示行（初始隐藏，handleAgentPhase 设置 textContent） ──
  const phaseLine = document.createElement('div');
  phaseLine.className = 'agent-phase-line';
  phaseLine.style.display = 'none';
  row.appendChild(phaseLine);

  // ── 工具记录容器（handleAgentToolParsing/Started 追加子记录） ──
  const toolsContainer = document.createElement('div');
  toolsContainer.className = 'agent-tools';
  row.appendChild(toolsContainer);

  // ═══ 兼容旧字段（隐藏，供 handlers.js 引用） ═══
  const statusEl = document.createElement('div');
  statusEl.className = 'dispatch-agent-status';
  statusEl.style.display = 'none';
  statusEl.innerHTML = '<span class="status-dot running"></span> ' + escapeHtml(data.status);
  row.appendChild(statusEl);

  const metaEl = document.createElement('div');
  metaEl.className = 'dispatch-agent-meta';
  metaEl.style.display = 'none';
  row.appendChild(metaEl);

  const headerEl = document.createElement('div');
  headerEl.style.display = 'none';
  headerEl.innerHTML = '<span class="agent-inline-status"></span>';
  row.appendChild(headerEl);

  row.dataset.isLast = 'true';
  agentsContainer.appendChild(row);

  // ── 构建状态对象 ──
  const startTime = Date.now();
  const agentState = {
    row,
    statusEl,
    phaseEl: phaseLine,
    toolsEl: toolsContainer,
    metaEl,
    headerEl,
    toolRecords: {},
    _dispatchLabel: dispatchContainer.toolLabel,
    _startTime: startTime,
    _timerId: null,
  };

  // ── 注册全局计时器更新标题行耗时 ──
  const titleElapsed = titleLine.querySelector('.agent-elapsed');
  if (titleElapsed) {
    _globalTimer.registerAgent(data.label, {
      elapsedEl: titleElapsed,
      startTime: startTime,
    });
  }

  activeAgents[data.label] = agentState;
  scrollToBottom();
}

/** 获取 labelOrder 中最后一个活跃的 dispatch 容器 label */
function _getActiveDispatchLabel() {
  for (let i = dispatchState.labelOrder.length - 1; i >= 0; i--) {
    const lbl = dispatchState.labelOrder[i];
    if (dispatchState.map.has(lbl)) {
      return lbl;
    }
  }
  return null;
}

/** 刷新缓冲的 subagent 条目 */
function _flushPendingSubagent() {
  const pending = dispatchState.pendingAgents;
  if (!pending.length) return;
  // 倒序查找最后一个活跃的 dispatch 容器
  const activeLabel = _getActiveDispatchLabel();
  const dispatchData = activeLabel ? dispatchState.map.get(activeLabel) : null;
  if (!dispatchData) return; // 容器尚未就绪，保留 pending
  for (const data of pending) {
    _createSubagentRow(data, dispatchData);
  }
  dispatchState.pendingAgents = [];
}

/** 连接断开/重连时清理所有残留计时器 */
function _cleanupAllTimers() {
  _globalTimer.clearAll();
}



/* ═══════════════════════════════════════════════════════════════
   _createToolBubble — 创建工具气泡 DOM 结构
   被 handlers/tools.js handleToolParsing/handleToolStarted 调用
   返回 { row, phaseEl, outputEl, metaEl, headerEl, startTime }
   ═══════════════════════════════════════════════════════════════ */
function _createToolBubble(label, toolName, msgIndex, options) {
  const opts = options || {};
  const isDispatch = !!opts.isDispatch;
  const isParallel = !!opts.isParallel;
  const startTime = Date.now();

  // ── 创建 tool row ──
  const row = document.createElement('div');
  row.className = isParallel ? 'tool-parallel-row' : 'tool-single-row';

  // ── header ──
  const headerEl = document.createElement('div');
  headerEl.className = 'tool-header';
  const tag = msgIndex !== undefined ? '<span class="msg-tag">#' + msgIndex + '</span> ' : '';
  headerEl.innerHTML = tag + '<span class="icon small">⚙</span> ' + window.escapeHtml(toolName || '工具');
  row.appendChild(headerEl);

  // ── phase（当前阶段：接收参数中） ──
  const phaseEl = document.createElement('div');
  phaseEl.className = 'tool-phase';
  const argLen = (opts.arguments || '').length;
  const estTokens = typeof window._estimateTokens === 'function'
    ? window._estimateTokens(opts.arguments || '')
    : Math.round(argLen / 4);
  phaseEl.innerHTML = '<span class="spinner"></span> 接收参数中 ' + estTokens + 'T <span class="tool-timer-text">0.0s</span>';
  row.appendChild(phaseEl);

  // ── output ──
  const outputEl = document.createElement('div');
  outputEl.className = 'tool-output';
  outputEl.style.display = 'none';
  if (isDispatch) {
    outputEl.style.cssText = 'display:block;max-height:none;overflow-y:visible;font-family:var(--font-mono);white-space:normal;font-size:13px;padding:0;background:transparent;';
  }
  row.appendChild(outputEl);

  // ── meta ──
  const metaEl = document.createElement('div');
  metaEl.className = 'tool-meta';
  row.appendChild(metaEl);

  // ── 添加到 DOM ──
  if (isParallel && _parallelBatchEl) {
    _parallelBatchEl.appendChild(row);
  } else {
    // 创建气泡容器
    const bubbleEl = document.createElement('div');
    bubbleEl.className = 'bubble tool';
    bubbleEl.appendChild(row);
    messagesEl.appendChild(bubbleEl);
    // ★ 气泡入场动画
    bubbleEl.classList.add('bubble-enter');
    bubbleEl.addEventListener('animationend', function handler() {
      bubbleEl.classList.remove('bubble-enter');
      bubbleEl.removeEventListener('animationend', handler);
    }, { once: true });
    _ensureSentinelAtBottom();
  }

  // ── 注册全局计时器 ──
  _globalTimer.registerTool(label, {
    phaseEl: phaseEl,
    startTime: startTime,
    type: 'parsing',
  });

  return { row, phaseEl, outputEl, metaEl, headerEl, startTime };
}


/* ═══════════════════════════════════════════════════════════════
   消息复制 — 为气泡添加复制按钮
   ═══════════════════════════════════════════════════════════════ */

/** 注入复制按钮样式 */
(function _injectCopyBtnStyle() {
  var style = document.createElement('style');
  style.textContent =
    '.bubble{position:relative}.copy-bubble-btn{' +
    'position:absolute;top:4px;right:4px;z-index:5;' +
    'width:28px;height:28px;padding:4px;border:none;border-radius:5px;' +
    'background:rgba(255,255,255,0.06);color:var(--text-dim, #8892a4);' +
    'cursor:pointer;opacity:0;transition:opacity 0.2s,background 0.15s,color 0.15s;' +
    'display:flex;align-items:center;justify-content:center;font-size:13px;line-height:1;' +
    '}' +
    '.bubble:hover .copy-bubble-btn{opacity:1}' +
    '.copy-bubble-btn:hover{background:rgba(255,255,255,0.15);color:var(--text-bright,#fff)}' +
    '.copy-bubble-btn:active{transform:scale(0.92)}' +
    '.copy-bubble-btn.copied{opacity:1;color:var(--success,#4caf50)}' +
    '@media(hover:none)and(pointer:coarse){' +
    '.copy-bubble-btn{opacity:1;width:32px;height:32px;background:rgba(255,255,255,0.08)}' +
    '}';
  document.head.appendChild(style);
})();

/**
 * 为气泡添加复制按钮
 * @param {HTMLElement} bubbleEl - 气泡 DOM 元素（必须已有 .bubble 类）
 * @param {Function} [getTextFn] - 可选：自定义获取文本的函数，默认为 bubbleEl.textContent
 */
function _addBubbleCopyButton(bubbleEl, getTextFn) {
  if (!bubbleEl || bubbleEl.querySelector('.copy-bubble-btn')) return;
  var btn = document.createElement('button');
  btn.className = 'copy-bubble-btn';
  btn.innerHTML = '📋';
  btn.title = '复制消息';
  btn.setAttribute('aria-label', '复制消息');
  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    var text = typeof getTextFn === 'function' ? getTextFn() : bubbleEl.textContent;
    if (!text) return;
    navigator.clipboard.writeText(text.trim()).then(function() {
      btn.innerHTML = '✓';
      btn.title = '已复制';
      btn.classList.add('copied');
      setTimeout(function() {
        btn.innerHTML = '📋';
        btn.title = '复制消息';
        btn.classList.remove('copied');
      }, 1500);
    }).catch(function() {
      var range = document.createRange();
      range.selectNodeContents(bubbleEl);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      btn.innerHTML = '✓';
      setTimeout(function() {
        btn.innerHTML = '📋';
      }, 1500);
    });
  });
  bubbleEl.appendChild(btn);
}

/** MutationObserver：自动为新气泡添加复制按钮 */
var _copyBtnBubbleObserver = null;
function _startCopyBtnBubbleObserver() {
  if (_copyBtnBubbleObserver) return;
  var el = document.getElementById('messages');
  if (!el) { setTimeout(_startCopyBtnBubbleObserver, 100); return; }
  _copyBtnBubbleObserver = new MutationObserver(function(mutations) {
    for (var m = 0; m < mutations.length; m++) {
      var mut = mutations[m];
      if (mut.type !== 'childList') continue;
      for (var n = 0; n < mut.addedNodes.length; n++) {
        var node = mut.addedNodes[n];
        if (node.nodeType === 1 && node.classList && node.classList.contains('bubble')) {
          if (!node.classList.contains('tool')) {
            _addBubbleCopyButton(node);
          }
        }
      }
    }
  });
  _copyBtnBubbleObserver.observe(el, { childList: true, subtree: false });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _startCopyBtnBubbleObserver);
} else {
  _startCopyBtnBubbleObserver();
}


/* ═══════════════════════════════════════════════════════════════
   无限滚动 — 长列表惰性渲染
   ═══════════════════════════════════════════════════════════════ */

var MAX_VISIBLE_MSGS = 50;
var _renderedMsgCount = 0;
var _cachedMessagesData = null;
var _loadMoreTrigger = null;

function _createLoadMoreTrigger() {
  if (_loadMoreTrigger) return _loadMoreTrigger;
  var el = document.createElement('div');
  el.id = 'load-more-trigger';
  el.className = 'load-more-trigger';
  el.innerHTML = '<div class="load-more-btn">↑ 加载更多消息</div>';
  el.addEventListener('click', _onLoadMore);
  return el;
}

function _onLoadMore() {
  if (!_cachedMessagesData) return;
  var trigger = document.getElementById('load-more-trigger');
  if (trigger) {
    trigger.innerHTML = '<div class="load-more-loading">加载中…</div>';
  }
  setTimeout(function() {
    _expandLoadedMessages();
  }, 50);
}

function _expandLoadedMessages() {
  if (!_cachedMessagesData) return;
  var total = _cachedMessagesData.length;
  var currentVisible = _renderedMsgCount;
  var newCount = Math.min(currentVisible + MAX_VISIBLE_MSGS, total);
  if (newCount <= currentVisible) return;
  _rebuildMessagesWithRange(_cachedMessagesData, 0, total, newCount);
}

function _rebuildMessagesWithRange(messagesData, visibleStart, totalCount, visibleCount) {
  var startIdx = Math.max(0, totalCount - visibleCount);
  var slicedData = messagesData.slice(startIdx);
  _renderedMsgCount = visibleCount;
  _doRebuildFromSlice(slicedData, totalCount, visibleCount);
}

function _doRebuildFromSlice(slicedData, totalCount, visibleCount) {
  var preactTools = document.getElementById('preact-tools-container');
  var preactAgents = document.getElementById('preact-agents-container');
  var preactMsgList = document.getElementById('preact-message-list');
  var scrollSentinel = document.getElementById('scroll-sentinel');
  var oldScrollHeight = messagesEl.scrollHeight;
  var oldScrollTop = messagesEl.scrollTop;

  // 全量重建前清理回收状态
  _clearRecycleState();

  messagesEl.innerHTML = '';
  if (preactTools) messagesEl.appendChild(preactTools);
  if (preactAgents) messagesEl.appendChild(preactAgents);
  if (preactMsgList) messagesEl.appendChild(preactMsgList);

  if (visibleCount < totalCount) {
    var triggerDiv = document.createElement('div');
    triggerDiv.id = 'load-more-trigger';
    triggerDiv.className = 'load-more-trigger';
    var remaining = totalCount - visibleCount;
    triggerDiv.innerHTML = '<div class="load-more-btn">↑ 加载更早的 ' + remaining + ' 条消息</div>';
    triggerDiv.addEventListener('click', _onLoadMore);
    messagesEl.appendChild(triggerDiv);
    _loadMoreTrigger = triggerDiv;
  } else {
    _loadMoreTrigger = null;
  }

  bubbles.clear();
  for (var i = 0; i < slicedData.length; i++) {
    var msg = slicedData[i];
    if (msg.role === 'user') {
      var idx = msg.msg_index;
      var userKey = 'user-' + idx;
      var uEl = document.createElement('div');
      uEl.className = 'bubble user bubble-enter';
      var uHeader = document.createElement('div');
      uHeader.className = 'header';
      uHeader.innerHTML = '<span class="msg-tag">#' + idx + '</span>';
      uEl.appendChild(uHeader);
      var uContent = document.createElement('div');
      uContent.className = 'bubble-content';
      uContent.textContent = msg.content || '';
      uEl.appendChild(uContent);
      var uTs = document.createElement('div');
      uTs.className = 'timestamp';
      uTs.textContent = new Date().toLocaleTimeString();
      uEl.appendChild(uTs);
      messagesEl.appendChild(uEl);
      bubbles.set(userKey, uEl);
      _addBubbleCopyButton(uEl);
    } else if (msg.role === 'assistant') {
      var content = msg.content || '';
      var reasoningContent = msg.reasoning_content || '';
      var asstIdx = msg.content_msg_index ?? msg.reasoning_msg_index;
      var asstKey = 'assistant-' + asstIdx;
      if ((reasoningContent || content)) {
        var aEl = document.createElement('div');
        aEl.className = 'bubble answer bubble-enter';
        var aHeader = document.createElement('div');
        aHeader.className = 'header';
        aHeader.innerHTML = '<span class="msg-tag">#' + asstIdx + ' 🤖</span>';
        aEl.appendChild(aHeader);
        if (reasoningContent) {
          var thinkEl = document.createElement('div');
          thinkEl.className = 'think-section';
          thinkEl.setAttribute('data-raw', reasoningContent);
          thinkEl.innerHTML = typeof window._renderMarkdownFallback === 'function'
            ? window._renderMarkdownFallback(reasoningContent) : escapeHtml(reasoningContent).replace(/\n/g, '<br>');
          aEl.appendChild(thinkEl);
        }
        if (content) {
          var answerEl = document.createElement('div');
          answerEl.className = 'answer-section';
          answerEl.setAttribute('data-raw', content);
          answerEl.innerHTML = typeof window._renderMarkdownFallback === 'function'
            ? window._renderMarkdownFallback(content) : escapeHtml(content).replace(/\n/g, '<br>');
          aEl.appendChild(answerEl);
        }
        if (content) {
          var aTs = document.createElement('div');
          aTs.className = 'timestamp';
          aTs.textContent = new Date().toLocaleTimeString();
          aEl.appendChild(aTs);
        }
        messagesEl.appendChild(aEl);
        bubbles.set(asstKey, aEl);
        _addBubbleCopyButton(aEl);
      }
    } else if (msg.role === 'tool') {
      // 工具消息：创建工具气泡
      var toolKey = 'tool-' + (msg.msg_index || Date.now());
      if (!bubbles.has(toolKey)) {
        var tEl = document.createElement('div');
        tEl.className = 'bubble tool bubble-enter';
        var tHeader = document.createElement('div');
        tHeader.className = 'tool-header';
        tHeader.innerHTML = '<span class="icon">🔧</span> ' + escapeHtml(msg.tool_name || '工具');
        tEl.appendChild(tHeader);
        var tPhase = document.createElement('div');
        tPhase.className = 'tool-phase';
        tPhase.innerHTML = '<span class="tick">✓</span> ' + escapeHtml(msg.tool_name || '工具');
        tEl.appendChild(tPhase);
        if (msg.content) {
          var tOutput = document.createElement('div');
          tOutput.className = 'tool-output';
          tOutput.style.display = 'block';
          tOutput.textContent = msg.content.length > 5000 ? msg.content.slice(0, 5000) + '...' : msg.content;
          tEl.appendChild(tOutput);
        }
        messagesEl.appendChild(tEl);
        bubbles.set(toolKey, tEl);
      }
    } else if (msg.role === 'agent') {
      // Agent 消息：创建代理气泡
      var agentKey = 'agent-' + (msg.msg_index || Date.now());
      if (!bubbles.has(agentKey)) {
        var agEl = document.createElement('div');
        agEl.className = 'bubble agent bubble-enter';
        var agHeader = document.createElement('div');
        agHeader.className = 'agent-header';
        agHeader.innerHTML = '<span class="icon">🤖</span> ' + escapeHtml(msg.agent_name || 'Agent');
        agEl.appendChild(agHeader);
        if (msg.content) {
          var agContent = document.createElement('div');
          agContent.className = 'agent-content';
          agContent.textContent = msg.content.length > 5000 ? msg.content.slice(0, 5000) + '...' : msg.content;
          agEl.appendChild(agContent);
        }
        messagesEl.appendChild(agEl);
        bubbles.set(agentKey, agEl);
      }
    }
  }

  if (scrollSentinel) messagesEl.appendChild(scrollSentinel);

  if (oldScrollHeight > 0 && visibleCount < totalCount) {
    var newScrollHeight = messagesEl.scrollHeight;
    messagesEl.scrollTop = oldScrollTop + (newScrollHeight - oldScrollHeight);
  } else {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  setTimeout(function() {
    if (typeof window.postProcessMarkdown === 'function') {
      window.postProcessMarkdown(messagesEl);
    }
  }, 100);
}



/* ═══════════════════════════════════════════════════════════════
   气泡回收 — DOM 节点数控制（移动端性能优化）
   当 #messages 中气泡数超过 MAX_DOM_BUBBLES 时，
   自动将最旧的已完成气泡移出 DOM（存入 _bubbleArchive）。
   用户向上翻页时自动恢复。
   不影响正在流式渲染或活跃执行中的气泡。
   ═══════════════════════════════════════════════════════════════ */

/** 最大 DOM 气泡数（超过此值开始回收） */
var MAX_DOM_BUBBLES = 80;

/** 回收检查节流间隔（ms） */
var RECYCLE_INTERVAL = 1500;

/** 回收缓冲：单次回收时多移除的数量，减少频繁回收 */
var RECYCLE_BUFFER = 10;

/** 已回收的气泡存档：{key, html} 按回收顺序排列 */
var _bubbleArchive = [];

/** 回收定时器 */
var _recycleTimer = null;

/** 恢复 IntersectionObserver */
var _restoreObserver = null;

/** 恢复触发器 DOM 元素 */
var _restoreTriggerEl = null;

/**
 * 通过 DOM 元素查找其对应的 bubbles Map 键名。
 * 遍历 bubbles Map，返回匹配的元素键。
 */
function _findKeyByElement(el) {
  for (var _iter = bubbles.entries(), _pair; !(_pair = _iter.next()).done;) {
    if (_pair.value[1] === el) return _pair.value[0];
  }
  return null;
}

/**
 * 判断气泡是否处于活跃状态（正在流式渲染或工具执行中）。
 * 活跃气泡不可回收。
 */
function _isBubbleActive(key, el) {
  // 正在流式渲染的 assistant 气泡
  if (key && key.startsWith('assistant-')) {
    if (el && el.querySelector('._md-streaming')) return true;
    return false;
  }
  // 活跃的工具气泡（正在执行中）
  if (key && key.startsWith('tool-')) {
    var label = key.slice(5);
    if (activeTools[label]) return true;
    return false;
  }
  // 活跃的 Agent 气泡
  if (key && key.startsWith('agent-')) {
    var label2 = key.slice(6);
    if (activeAgents[label2]) return true;
    return false;
  }
  return false;
}

/** 判断 DOM 元素是否为 sentinel（占位容器），不可回收 */
function _isSentinel(el) {
  if (!el || !el.id) return false;
  return el.id === 'scroll-sentinel' ||
         el.id === 'preact-tools-container' ||
         el.id === 'preact-agents-container' ||
         el.id === 'preact-message-list' ||
         el.id === 'load-more-trigger';
}

/**
 * 执行一次回收扫描。
 * 从 messagesEl 的子节点中找到最旧的已完成气泡，
 * 保存其 outerHTML 后从 DOM 移除。
 */
function _doRecycleCheck() {
  var children = messagesEl.children;
  if (children.length <= MAX_DOM_BUBBLES) return;

  var toRecycle = children.length - MAX_DOM_BUBBLES + RECYCLE_BUFFER;
  var recycled = 0;

  // 从前往后扫描（最旧的在前），跳过 sentinel 和活跃气泡
  for (var i = 0; i < children.length && recycled < toRecycle; i++) {
    var child = children[i];
    if (_isSentinel(child)) continue;

    var key = _findKeyByElement(child);
    if (!key) continue;
    if (_isBubbleActive(key, child)) continue;

    // 保存 outerHTML 到存档
    _bubbleArchive.push({
      key: key,
      html: child.outerHTML,
    });
    bubbles.delete(key);
    child.remove();
    recycled++;
  }

  if (recycled > 0) {
    _ensureRestoreTrigger();
  }
}

/** 确保在消息列表顶部存在恢复触发器（用户向上滚动时自动恢复存档气泡） */
function _ensureRestoreTrigger() {
  if (_restoreTriggerEl && document.body.contains(_restoreTriggerEl)) return;

  _restoreTriggerEl = document.createElement('div');
  _restoreTriggerEl.id = 'recycle-restore-trigger';
  _restoreTriggerEl.className = 'load-more-trigger';
  _restoreTriggerEl.setAttribute('data-restore', 'true');
  _restoreTriggerEl.innerHTML = '<div class="load-more-btn">↑ 查看更多历史消息</div>';
  _restoreTriggerEl.addEventListener('click', _onRestoreArchived);

  // 插入到 messagesEl 顶部（在所有气泡之前）
  var firstBubble = messagesEl.querySelector('.bubble');
  if (firstBubble) {
    messagesEl.insertBefore(_restoreTriggerEl, firstBubble);
  } else {
    messagesEl.insertBefore(_restoreTriggerEl, messagesEl.firstChild);
  }

  // 启动 IntersectionObserver：触发器进入视口时自动恢复
  _startRestoreObserver();
}

/** 恢复存档气泡 */
function _onRestoreArchived() {
  if (_bubbleArchive.length === 0) {
    if (_restoreTriggerEl) {
      _restoreTriggerEl.remove();
      _restoreTriggerEl = null;
    }
    return;
  }

  // 从末尾取出存档（最后回收的最先恢复 — 更靠近当前视口）
  var batch = _bubbleArchive.splice(-15, 15); // 每次恢复 15 条
  var firstBubble = messagesEl.querySelector('.bubble');

  for (var i = batch.length - 1; i >= 0; i--) {
    var item = batch[i];
    // 创建临时容器解析 outerHTML
    var temp = document.createElement('div');
    temp.innerHTML = item.html;
    var restored = temp.firstElementChild;
    if (!restored) continue;

    // 移除 bubble-enter 类（禁止入场动画，避免闪烁）
    restored.classList.remove('bubble-enter');

    // 插回到 restoreTrigger 之后
    if (_restoreTriggerEl && _restoreTriggerEl.parentNode) {
      _restoreTriggerEl.parentNode.insertBefore(restored, _restoreTriggerEl.nextSibling);
    } else {
      messagesEl.insertBefore(restored, messagesEl.firstChild);
    }

    bubbles.set(item.key, restored);

    // 为恢复的气泡添加复制按钮
    if (!restored.classList.contains('tool')) {
      _addBubbleCopyButton(restored);
    }
  }

  // 如果全部恢复完毕，移除触发器
  if (_bubbleArchive.length === 0 && _restoreTriggerEl) {
    _restoreTriggerEl.remove();
    _restoreTriggerEl = null;
    _stopRestoreObserver();
  }

  // 保持滚动位置不变（恢复的气泡在上方）
}

/** 启动恢复触发器的 IntersectionObserver */
function _startRestoreObserver() {
  _stopRestoreObserver();
  if (!_restoreTriggerEl) return;

  _restoreObserver = new IntersectionObserver(function(entries) {
    for (var e = 0; e < entries.length; e++) {
      if (entries[e].isIntersecting && _bubbleArchive.length > 0) {
        // 延迟执行，避免滚动过程中大量 DOM 操作卡顿
        setTimeout(_onRestoreArchived, 100);
      }
    }
  }, { rootMargin: '200px 0px' });

  _restoreObserver.observe(_restoreTriggerEl);
}

/** 停止恢复观察器 */
function _stopRestoreObserver() {
  if (_restoreObserver) {
    _restoreObserver.disconnect();
    _restoreObserver = null;
  }
}

/**
 * 调度回收检查（节流：每 RECYCLE_INTERVAL 最多一次）
 * 在新建气泡后自动调用。
 */
function _scheduleRecycleCheck() {
  if (_recycleTimer) return;
  _recycleTimer = setTimeout(function() {
    _recycleTimer = null;
    _doRecycleCheck();
  }, RECYCLE_INTERVAL);
}

/** 清理所有回收状态（在 _rebuildMessagesFromData / 断开重连时调用） */
function _clearRecycleState() {
  _bubbleArchive = [];
  _stopRestoreObserver();
  if (_restoreTriggerEl) {
    _restoreTriggerEl.remove();
    _restoreTriggerEl = null;
  }
  if (_recycleTimer) {
    clearTimeout(_recycleTimer);
    _recycleTimer = null;
  }
}

/* ── 导出到全局（供其他模块使用） ── */
window.__recycle = {
  scheduleCheck: _scheduleRecycleCheck,
  clearAll: _clearRecycleState,
  archive: _bubbleArchive,
};

/* ── 在 bubbles.set 后自动触发回收检查（通过包装原始 set 方法） ── */
(function _hookBubbleSet() {
  var _originalSet = bubbles.set.bind(bubbles);
  bubbles.set = function(key, el) {
    _originalSet(key, el);
    // 延迟触发回收（等气泡完全渲染后再检查）
    setTimeout(_scheduleRecycleCheck, 100);
    return this;
  };
})();
