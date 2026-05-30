/* ═══════════════════════════════════════════════════════════════
   handlers/tools.js — 工具调用消息处理器
   依赖: handlers/base.js (_createToolBubble, _st, _debouncedScrollToBottom,
         _showGenStatus, _hideGenStatus, _addGenChars, _setGenTokens,
         _setGenInputTokens, _setGenTotalTokens, _genState)
         bubble.js (activeTools, activeAgents, _globalTimer, bubbles,
         dispatchState, _parallelBatchEl, _activeToolCount, addBubble,
         scrollToBottom, _flushPendingDispatchAgent, _cleanupAllTimers)
         tool-renderer.js (renderReadFileOutput, renderAnsiDiff, renderWebDiff)
         utils.js (escapeHtml, postProcessMarkdown)
   ═══════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════════
   1. handleToolParsing — 工具参数解析
   使用 _createToolBubble() 创建气泡（dispatch_agent 统一走抽象函数，
   不再需要特殊 ~40 行分支）。并行工具根据 _activeToolCount > 0
   判断。同步 store（200ms 节流）。
   ═══════════════════════════════════════════════════════════════ */
function handleToolParsing(data) {
  const label = data.label;
  const existingTool = activeTools[label];

  // ── 已有此工具 → 更新参数接收进度 ────────────────
  let tool = existingTool;
  if (tool) {
    tool._lastArgs = data.arguments || '';
    // 流式到达 tool_name 后补全 header 和 toolName
    if (data.tool_name && data.tool_name !== tool.toolName) {
      tool.toolName = data.tool_name;
      const tag = data.msg_index !== undefined ? '<span class="msg-tag">#' + data.msg_index + '</span> ' : '';
      tool.headerEl.innerHTML = tag + '<span class="icon small">⚙</span> ' + escapeHtml(data.tool_name);
    }
    // ★ 更新 phase 行的 token 估算值（修复「0T 不动」bug）
    //   流式参数到达时，重新估算 token 数并更新 phase 行显示。
    // ★ 耗时刷新修复：innerHTML 会创建新的 .tool-timer-text 元素，使
    //   _globalTimer 中缓存的 _timerEl 指向已移除的旧元素 → 耗时不再刷新。
    //   通过将 _timerEl 置 null 强制下一次 tick 重新查找。
    if (data.arguments && tool.phaseEl) {
      const argTokens = typeof window._estimateTokens === 'function'
        ? window._estimateTokens(data.arguments)
        : Math.round(data.arguments.length / 4);
      const timerEl = tool.phaseEl.querySelector('.tool-timer-text');
      const elapsed = timerEl ? timerEl.textContent : '0.0s';
      tool.phaseEl.innerHTML = '<span class="spinner"></span> 接收参数中 ' + argTokens + 'T <span class="tool-timer-text">' + elapsed + '</span>';
      // 强制 _globalTimer 的缓存引用失效，下次 tick 重新查找
      // ★ 注意：_globalTimer 是 bubble.js 中的顶层 const（非 window 属性），
      //   但在非 module 脚本中全局作用域可访问，直接引用变量名即可。
      if (typeof _globalTimer !== 'undefined' && _globalTimer._tools) {
        const entry = _globalTimer._tools.get(label);
        if (entry) entry._timerEl = null;
      }
    }
    // ★ 同步 store（节流：同一 label 200ms 内只同步一次）
    if (_st()) {
      if (!window._toolStoreThrottle) window._toolStoreThrottle = {};
      const now = Date.now();
      const lastSync = window._toolStoreThrottle[label] || 0;
      if (now - lastSync > 200) {
        window._toolStoreThrottle[label] = now;
        const upd = { arguments: data.arguments || '' };
        if (data.tool_name) upd.tool_name = data.tool_name;
        _st().updateTool(label, upd);
      }
    }
    _debouncedScrollToBottom();
    return;
  }

  // ── 创建新气泡（dispatch_agent 与普通工具统一走 _createToolBubble） ──
  const isParallel = _activeToolCount > 0 && _parallelBatchEl;
  const toolName = data.tool_name || '';
  const isDispatch = toolName === 'dispatch_agent';

  const result = _createToolBubble(label, toolName, data.msg_index, {
    isDispatch: isDispatch,
    arguments: data.arguments || '',
    isParallel: isParallel,
  });

  activeTools[label] = {
    el: result.row,
    phaseEl: result.phaseEl,
    outputEl: result.outputEl,
    metaEl: result.metaEl,
    headerEl: result.headerEl,
    toolName: toolName,
    _parsingStart: result.startTime,
    _lastArgs: data.arguments || '',
  };
  _activeToolCount++;

  // dispatch_agent 特殊状态跟踪
  if (isDispatch) {
    dispatchState.labelOrder.push(label);
  }

  // 同步 store
  if (_st()) {
    _st().addTool(label, {
      tool_name: toolName,
      msg_index: data.msg_index,
      phase: 'parsing',
      arguments: data.arguments || '',
      parsingStart: result.startTime,
      isDispatch: isDispatch,
    });
  }

  _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   2. handleToolStarted — 工具开始执行
   parsing → exec 模式切换，更新全局计时器。
   label 回退匹配（数字 key → tc id）。
   read_file/write_file/update_file 特殊渲染。
   ═══════════════════════════════════════════════════════════════ */
function handleToolStarted(data) {
  const label = data.label;
  let tool = activeTools[label];

  // ── label 回退匹配 ──
  if (!tool) {
    // 优先按 tool_name 匹配（dispatch_agent 或普通工具）
    for (const [k, v] of Object.entries(activeTools)) {
      if (v.toolName === data.tool_name && /^\d+$/.test(k)) {
        tool = v;
        delete activeTools[k];
        activeTools[label] = tool;
        break;
      }
    }
    // 回退：tool_parsing 首次创建时 tool_name 可能为空，宽松匹配数字 key
    if (!tool) {
      for (const [k, v] of Object.entries(activeTools)) {
        if (!v.toolName && /^\d+$/.test(k)) {
          tool = v;
          tool.toolName = data.tool_name;
          const tag = data.msg_index !== undefined ? '<span class="msg-tag">#' + data.msg_index + '</span> ' : '';
          tool.headerEl.innerHTML = tag + '<span class="icon small">⚙</span> ' + escapeHtml(data.tool_name);
          delete activeTools[k];
          activeTools[label] = tool;
          break;
        }
      }
    }
    // 最终 fallback：创建空气泡
    if (!tool) {
      handleToolParsing({ label, tool_name: data.tool_name, arguments: '' });
      tool = activeTools[label];
    }
  }
  if (!tool) return;

  const execStart = Date.now();
  tool._execStart = execStart;

  // ── dispatch_agent 特殊处理 ──
  if (data.tool_name === 'dispatch_agent' || tool.toolName === 'dispatch_agent') {
    // ★ 从 parsing 模式切换到 exec 模式
    _globalTimer.unregisterTool(label);
    tool.phaseEl.innerHTML = '<span class="spinner"></span> 执行中 dispatch_agent <span class="tool-timer-text">0.0s</span>';
    _globalTimer.registerTool(label, {
      phaseEl: tool.phaseEl,
      startTime: execStart,
      type: 'exec',
      execStart: execStart,
    });

    // 重置 output 容器为 agents 容器
    // ★ 2026-05-18 修复：使用 style.setProperty 逐属性设置，避免 cssText 覆盖其他内联样式
    var _oStyle = tool.outputEl.style;
    _oStyle.setProperty('display', 'block');
    _oStyle.setProperty('max-height', 'none');
    _oStyle.setProperty('max-width', '100%');
    _oStyle.setProperty('box-sizing', 'border-box');
    _oStyle.setProperty('overflow-y', 'visible');
    _oStyle.setProperty('word-break', 'break-word');
    _oStyle.setProperty('font-family', 'var(--font)');
    _oStyle.setProperty('white-space', 'normal');
    _oStyle.setProperty('font-size', '13px');
    _oStyle.setProperty('padding', '0');
    _oStyle.setProperty('background', 'transparent');
    tool.outputEl.innerHTML = '';

    const agentsContainer = document.createElement('div');
    agentsContainer.className = 'dispatch-agent-container';
    tool.outputEl.appendChild(agentsContainer);

    dispatchState.map.set(label, {
      toolLabel: label,
      containerEl: tool.outputEl,
      agentsContainer: agentsContainer,
      phaseEl: tool.phaseEl,
    });

    if (_st()) _st().updateTool(label, { phase: 'executing', execStart: execStart });

    // 刷新缓冲的 dispatch agent 条目
    _flushPendingDispatchAgent();
    _debouncedScrollToBottom();
    return;
  }

  // ── 普通工具：从 parsing 切换到 exec ──
  _globalTimer.unregisterTool(label);
  const toolName = data.tool_name;
  tool.phaseEl.innerHTML = '<span class="spinner"></span> 执行中 ' + escapeHtml(toolName) + ' <span class="tool-timer-text">0.0s</span>';
  _globalTimer.registerTool(label, {
    phaseEl: tool.phaseEl,
    startTime: execStart,
    type: 'exec',
    execStart: execStart,
  });

  // detail 信息展示
  if (data.detail) {
    tool.outputEl.style.display = 'block';
    if (toolName === 'bash') {
      const cmdText = data.detail.replace(/^'|'$/g, '');
      tool._cmdLine = '$ ' + cmdText;
      // ★ 保护：仅当 output 中还没有 cmd-line 时才设置，避免覆盖已到达的实时输出
      if (!tool.outputEl.querySelector('.cmd-line')) {
        tool.outputEl.innerHTML = '<span class="cmd-line">' + escapeHtml(tool._cmdLine) + '</span>\n';
      }
    } else {
      tool.outputEl.textContent = '参数: ' + data.detail + '\n';
    }
  }

  // metadata 展示
  const meta = data.metadata || {};
  const parts = [];
  if (meta['参数']) parts.push('参数: ' + meta['参数']);
  if (meta['解析']) parts.push('解析: ' + meta['解析']);
  if (parts.length) tool.metaEl.textContent = parts.join(' | ');

  // 更新 header 图标
  const tag = data.msg_index !== undefined ? '<span class="msg-tag">#' + data.msg_index + '</span> ' : '';
  tool.headerEl.innerHTML = tag + '<span class="icon">🔧</span> ' + escapeHtml(toolName);

  // 同步 store
  if (_st()) {
    const upd = {
      phase: 'executing',
      execStart: execStart,
      detail: data.detail || '',
      metadata: data.metadata || {},
    };
    if (toolName === 'bash') {
      upd.cmdLine = '$ ' + (data.detail || '').replace(/^'|'$/g, '');
    }
    _st().updateTool(label, upd);
  }
}

/* ═══════════════════════════════════════════════════════════════
   3. handleToolOutput — 工具输出块（rAF 合并 DOM 追加）
   ═══════════════════════════════════════════════════════════════ */

/* ── rAF 缓冲池：存 raw text，rAF 时统一 ansiToHtml + DOM ── */
const _toolOutputBuffer = new Map();   // label → [rawText, ...]
let _pendingRAF = null;

/** 刷新所有缓冲的工具输出到 DOM（ansiToHtml + insertAdjacentHTML 一次完成） */
function _flushToolOutputBuffer() {
  _pendingRAF = null;
  for (const [label, raws] of _toolOutputBuffer) {
    const tool = activeTools[label];
    if (!tool) continue;
    tool.outputEl.style.display = 'block';
    // ★ rAF 时统一执行 ansiToHtml，WS handler 中不处理，减少消息积压
    for (let i = 0; i < raws.length; i++) {
      tool.outputEl.insertAdjacentHTML('beforeend',
        typeof window.ansiToHtml === 'function' ? window.ansiToHtml(raws[i]) : escapeHtml(raws[i]));
    }
    _throttledToolScroll(tool.outputEl, label);
  }
  _toolOutputBuffer.clear();
}

/** 清除已完成的工具滚动节流缓存 */
function _clearToolScrollThrottle(label) {
  delete _toolScrollThrottle[label];
}

/** 立即刷新指定 label 的缓冲（handleToolDone 调用，避免数据丢失） */
function _flushToolOutputNow(label) {
  const raws = _toolOutputBuffer.get(label);
  if (!raws || raws.length === 0) return;
  _toolOutputBuffer.delete(label);
  const tool = activeTools[label];
  if (!tool) return;
  tool.outputEl.style.display = 'block';
  // ★ 立即执行 ansiToHtml + DOM，确保 handleToolDone 看到完整输出
  for (let i = 0; i < raws.length; i++) {
    tool.outputEl.insertAdjacentHTML('beforeend',
      typeof window.ansiToHtml === 'function' ? window.ansiToHtml(raws[i]) : escapeHtml(raws[i]));
  }
  _throttledToolScroll(tool.outputEl, label);
}

/** 调度 rAF 刷新（同一帧内多个 handleToolOutput 合并为一次 DOM 操作） */
function _scheduleFlush() {
  if (_pendingRAF) return;
  // ★ 修复：浏览器后台标签页时 requestAnimationFrame 完全暂停，
  //   改用 setTimeout(fn, 0) 确保后台时仍能正常渲染工具输出。
  //   与 streaming.js 中流式文本增量渲染的修复保持一致。
  _pendingRAF = setTimeout(_flushToolOutputBuffer, 0);
}

/* ── 工具输出滚动节流（避免 O(n²) 强制布局） ── */
// 每 100ms 最多一次 scrollTop=scrollHeight，n 次更新 → O(n) 布局而非 O(n²)
const _toolScrollThrottle = {};

function _throttledToolScroll(el, label) {
  const now = Date.now();
  const last = _toolScrollThrottle[label] || 0;
  if (now - last > 100) {
    _toolScrollThrottle[label] = now;
    el.scrollTop = el.scrollHeight;
  }
}

/* ── 主容器滚动节流（与 rebuild.js _debouncedScrollToBottom 配合） ── */
let _mainScrollPending = false;

function _throttledMainScroll() {
  if (_mainScrollPending) return;
  _mainScrollPending = true;
  requestAnimationFrame(() => {
    _mainScrollPending = false;
    const el = typeof messagesEl !== 'undefined' ? messagesEl : document.getElementById('messages');
    if (el) el.scrollTop = el.scrollHeight;
  });
}

function handleToolOutput(data) {
  const label = data.label;
  const text = data.text;
  // ★ 缓冲 raw text 到 rAF：WS handler 不做 ansiToHtml，减少消息积压
  //   ansiToHtml + DOM 操作推迟到 rAF flush 统一执行
  if (text && text.trim()) {
    if (!_toolOutputBuffer.has(label)) {
      _toolOutputBuffer.set(label, []);
    }
    _toolOutputBuffer.get(label).push(text);
    _scheduleFlush();
  }

  // ★ 同步 store — O(1) 轻量标志，替代 O(n²) 全量 output 积累
  if (_st()) {
    const existing = _st().getTools()[label];
    if (existing && !existing._hasStreamedOutput) {
      _st().updateTool(label, { _hasStreamedOutput: true });
    }
  }

  _throttledMainScroll();
}

/* ═══════════════════════════════════════════════════════════════
   4. handleToolDone — 工具完成
   dispatch_agent 特殊处理（创建 agentsContainer），
   普通工具根据 metadata 渲染输出（readFileOutput/ansiDiff/webDiff）。
   1s 后从 activeTools 清理。
   ═══════════════════════════════════════════════════════════════ */
function handleToolDone(data) {
  const label = data.label;

  // ★ 先刷新 rAF 缓冲，确保所有流式输出已提交到 DOM
  //   handleToolDone 会检查 tool.outputEl.textContent 等状态，
  //   如果缓冲未刷，可能导致空白兜底逻辑误触发或最终输出不完整。
  _flushToolOutputNow(label);

  const tool = activeTools[label];
  if (!tool) return;

  // ★ 全局计时器管理器注销
  _globalTimer.unregisterTool(label);
  // ★ 清除工具滚动节流缓存，防止无限增长的内存泄漏
  _clearToolScrollThrottle(label);

  const meta = data.metadata || {};
  const name = data.tool_name || meta.tool_name || '工具';

  // ── 计算执行耗时 ──
  const elapsed = (tool._execStart
    ? ((Date.now() - tool._execStart) / 1000).toFixed(1)
    : tool._parsingStart
      ? ((Date.now() - tool._parsingStart) / 1000).toFixed(1)
      : '0.0');

  // ── dispatch_agent 特殊处理 ──
  if (name === 'dispatch_agent') {
    if (tool.phaseEl) {
      tool.phaseEl.innerHTML = data.success
        ? '<span class="tick">✓</span> dispatch_agent 完成 <span class="tool-duration">' + elapsed + 's</span>'
        : '<span class="cross">✗</span> dispatch_agent 失败 <span class="tool-duration">' + elapsed + 's</span>';
    }
    if (_st()) _st().updateTool(label, { phase: 'done', success: data.success, metadata: meta });

    dispatchState.batchDone++;
    dispatchState.map.delete(label);
    // ★ Bug 修复：从 labelOrder 中移除当前 label，防止跨轮残留
    const idxInOrder = dispatchState.labelOrder.indexOf(label);
    if (idxInOrder !== -1) {
      dispatchState.labelOrder.splice(idxInOrder, 1);
    }
    // ★ 更稳健的完成判定：所有 dispatch 的 map 条目都已清空
    const isLast = dispatchState.map.size === 0;
    delete activeTools[label];
    if (_activeToolCount > 0) {
      _activeToolCount--;
    }

    if (!isLast) {
      _debouncedScrollToBottom();
      return;
    }
    // ★ Bug 4 修复：dispatch_agent 完成时清理其子 Agent 的工具记录
    if (_st()) {
      const allAgents = _st().getAgents();
      for (const [agentLabel, agentData] of Object.entries(allAgents)) {
        if (agentData.dispatchLabel === label) {
          _st().updateAgent(agentLabel, { tools: [], _toolSeq: 0, _currentToolKey: null });
        }
      }
    }
    // ★ Bug 修复：延迟重置 dispatch 状态，给 agent_result 事件到达的时间窗口
    //   避免 _on_agent_result 中 dispatchState.map.get(dispatchLabel) 返回 null
    // ★ 竞态条件修复：使用 _generation 计数器检测新 dispatch 是否已启动。
    //   如果 500ms 内新 dispatch 启动（reset() 被调用），_generation 递增，
    //   timeout 回调中 generation 不匹配，跳过 reset()，防止新注册条目丢失。
    const resetGen = dispatchState._generation;
    // 取消旧的待处理 reset
    if (dispatchState._resetTimer) {
      clearTimeout(dispatchState._resetTimer);
    }
    dispatchState._resetTimer = setTimeout(() => {
      dispatchState._resetTimer = null;
      // 仅当没有新 dispatch 启动时才 reset
      if (dispatchState._generation === resetGen && dispatchState.map.size === 0) {
        dispatchState.reset();
      }
    }, 500);
    if (_st()) _st().removeTool(label);
    _debouncedScrollToBottom();
    return;
  }

  // ── 普通工具完成（显示最终耗时） ──
  if (data.success) {
    tool.phaseEl.innerHTML = '<span class="tick">✓</span> ' + escapeHtml(name) + ' 完成 <span class="tool-duration">' + elapsed + 's</span>';
  } else {
    tool.phaseEl.innerHTML = '<span class="cross">✗</span> ' + escapeHtml(name) + ' 失败 <span class="tool-duration">' + elapsed + 's</span>';
  }

  // metadata 行
  const parts = [];
  if (meta['参数']) parts.push('参数: ' + meta['参数']);
  if (meta['输出']) parts.push('输出: ' + meta['输出']);
  if (meta['行数']) parts.push('行数: ' + meta['行数']);
  if (parts.length) tool.metaEl.textContent = parts.join(' | ');

  // 根据工具类型和 metadata 渲染 output_preview
  if (meta['output_preview']) {
    tool.outputEl.style.display = 'block';
    const previewText = meta['output_preview'];

    if (name === 'bash') {
      // ★ 实时输出已在执行期间通过 handleToolOutput 逐行流式追加，
      //   handleToolStarted 已设置 cmd-line 行，此处不再覆盖，
      //   避免清掉已到达的实时内容。
      //   仅当异常空白时兜底显示 cmd-line。
      if (!tool.outputEl.textContent.trim()) {
        const cmdLine = tool._cmdLine || '$ ' + escapeHtml(data.tool_name || 'bash');
        tool.outputEl.innerHTML = '<span class="cmd-line">' + escapeHtml(cmdLine) + '</span>\n'
          + '<span class="cmd-output">' + escapeHtml(previewText) + '</span>';
        tool.outputEl.scrollTop = tool.outputEl.scrollHeight;
      }

    } else if (name === 'read_file' && data.success) {
      tool.outputEl.style.cssText = 'max-height:none;overflow-y:visible;';
      // 清空残留内容（tool_start 阶段写入的参数行），再渲染高亮代码
      tool.outputEl.innerHTML = '';
      renderReadFileOutput(tool.outputEl, previewText);

    } else if (name === 'write_file' || name === 'update_file') {
      tool.outputEl.style.cssText = 'max-height:none;overflow-y:visible;';
      // 清空残留内容
      tool.outputEl.innerHTML = '';
      // 有 diff_data → 用 web diff 渲染（LCS 逐行 diff，类似 GitHub 风格）
      if (meta['diff_data']) {
        renderWebDiff(tool.outputEl, meta['diff_data']);
      } else {
        // 回退：用 ANSI diff 渲染（旧版兼容）
        renderAnsiDiff(tool.outputEl, previewText);
      }

    } else {
      tool.outputEl.textContent = previewText;
    }
  }

  // 同步 store（done 且保留 1s 后移除）
  if (_st()) {
    _st().updateTool(label, {
      phase: 'done',
      success: data.success,
      metadata: meta,
      output_preview: meta['output_preview'] || '',
      cmdLine: tool._cmdLine || '',
    });
  }

  // ★ 立即更新计数器，防止后续工具（顺序调用）被误合并到当前气泡
  if (_activeToolCount > 0) {
    _activeToolCount--;
  }
  if (_activeToolCount <= 0) {
    _activeToolCount = 0;
    _parallelBatchEl = null;
  }

  // 立即清理 activeTools 和 store（不再延迟，防止后续同 label 工具竞态）
  delete activeTools[label];
  if (_st()) _st().removeTool(label);

  _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   5. handleToolSummary — 工具汇总气泡
   ═══════════════════════════════════════════════════════════════ */
function handleToolSummary(data) {
  const el = addBubble('tool');
  const header = document.createElement('div');
  header.className = 'tool-header';
  header.textContent = '📊 工具执行汇总';
  el.appendChild(header);

  const summary = document.createElement('div');
  summary.style.cssText = 'font-size: 12px; line-height: 1.6;';
  let html = '';
  if (data.successful_tools && data.successful_tools.length) {
    html += '<div style="color: var(--success);">✓ ' + escapeHtml(data.successful_tools.join(', ')) + '</div>';
  }
  if (data.failed_tools && data.failed_tools.length) {
    for (const item of data.failed_tools) {
      html += '<div style="color: var(--error);">✗ ' + escapeHtml(item.name) + ': ' + escapeHtml(item.error) + '</div>';
    }
  }
  summary.innerHTML = html;
  el.appendChild(summary);

  const ts = document.createElement('div');
  ts.className = 'timestamp';
  ts.textContent = new Date().toLocaleTimeString();
  el.appendChild(ts);

  if (_st()) _st().addMessage('summary-' + Date.now(), {
    type: 'tool',
    content: '',
    toolName: '工具执行汇总',
    toolStatus: '完成',
    timestamp: new Date().toLocaleTimeString(),
  });

  _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   6. handleModelPhase — 空操作（模型阶段转换不产生 UI 变化）
   ═══════════════════════════════════════════════════════════════ */
function handleModelPhase(/* data */) {
  // 不产生任何 UI 变化，保持为空操作
}

/* ═══════════════════════════════════════════════════════════════
   7. handleUsageUpdate — 更新 _genState 中的 token/速度数据
   ═══════════════════════════════════════════════════════════════ */
function handleUsageUpdate(data) {
  if (data.usage) {
    if (data.usage.input !== undefined) {
      _setGenInputTokens(data.usage.input);
    }
    if (data.usage.output !== undefined) {
      _setGenTokens(data.usage.output);
    }
    if (data.usage.total !== undefined) {
      _setGenTotalTokens(data.usage.total);
    }
    if (data.usage.speed !== undefined) {
      if (_genState) _genState.speed = data.usage.speed;
    }
  }
}

/* ═══════════════════════════════════════════════════════════════
   8. handleUserSelectNeeded — 用户选择弹窗（Modal）
   ═══════════════════════════════════════════════════════════════ */
function handleUserSelectNeeded(data) {
  if (!data.options || data.options.length === 0) {
    ws.send({ type: 'user_select', select_id: data.select_id, selected: [], action: 'confirmed' });
    return;
  }

  const overlay = document.getElementById('select-overlay');
  const dialog = document.getElementById('select-dialog');

  dialog.querySelector('.select-title').textContent = '📋 ' + data.title;

  const optionsDiv = dialog.querySelector('.select-options');
  optionsDiv.innerHTML = '';
  optionsDiv.dataset.multi = data.multi_select ? 'true' : 'false';

  for (const opt of data.options) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = data.multi_select ? 'checkbox' : 'radio';
    input.name = 'modal-select';
    input.value = opt;
    if (data.default_options && data.default_options.includes(opt)) {
      input.checked = true;
    }
    label.appendChild(input);
    label.appendChild(document.createTextNode(' ' + opt));
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
    const checked = optionsDiv.querySelectorAll('input:checked');
    const selected = Array.from(checked).map(c => c.value);
    ws.send({ type: 'user_select', select_id: data.select_id, selected, action: 'confirmed' });
    cleanup();
  };

  cancelBtn.onclick = () => {
    ws.send({
      type: 'user_select',
      select_id: data.select_id,
      selected: data.default_options || [],
      action: 'cancel',
    });
    cleanup();
  };

  overlay.classList.remove('hidden');
}

/* ═══════════════════════════════════════════════════════════════
   9. handleStatusPopup — 显示/隐藏生成状态弹窗
   ═══════════════════════════════════════════════════════════════ */
function handleStatusPopup(data) {
  if (data.action === 'show') {
    _showGenStatus();
    _showStopBtn();
  } else if (data.action === 'hide') {
    _hideGenStatus();
    _hideStopBtn();
  }
}

/* ═══════════════════════════════════════════════════════════════
   导出到全局（供 handlers.js 等外部文件使用）
   ═══════════════════════════════════════════════════════════════ */
Object.assign(window, {
  handleToolParsing,
  handleToolStarted,
  handleToolOutput,
  handleToolDone,
  handleToolSummary,
  handleModelPhase,
  handleUsageUpdate,
  handleUserSelectNeeded,
  handleStatusPopup,
});
