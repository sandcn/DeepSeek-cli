/* ═══════════════════════════════════════════════════════════════
   handlers/agents.js — Agent 事件处理器
   从 handlers.js 提取，依赖 bubble.js (activeAgents, _globalTimer, dispatchState, 
   _createSubagentRow, scrollToBottom), utils.js (escapeHtml, 
   scheduleTask, postProcessMarkdown), tool-renderer.js
   handlers.js 提供 _st, _renderMarkdownFallback, _debouncedScrollToBottom
   ═══════════════════════════════════════════════════════════════ */

// _st 已定义于 bubble.js（需在 bubble.js 之后加载）
// const _st = () => window.__store;

/* ── 工具树形连接符更新辅助 ─────────────────────────────── */
function _updateAgentToolConnectors(agent) {
  const container = agent.toolsEl;
  const row = container.lastElementChild;
  if (!row) return;
  const prevRow = row.previousElementSibling;
  if (prevRow) {
    const prevConnector = prevRow.querySelector('.tree-connector');
    if (prevConnector) prevConnector.textContent = '├';
  }
  const curConnector = row.querySelector('.tree-connector');
  if (curConnector) curConnector.textContent = '└';
}

/* ═══════════════════════════════════════════════════════════════
   1. handleAgentAdded — 添加 Agent 气泡
   ═══════════════════════════════════════════════════════════════ */
function handleAgentAdded(data) {
  // ★ Bug 修复：使用 dispatch_label 精确路由到对应 subagent 容器
  //   source=parallel 时，dispatch_label 标识 subagent 所属的 subagent 工具 label
  //   避免所有 subagent 被路由到最后一个 subagent 容器
  if (data.source === 'parallel') {
    let dispatchData = null;

    // 优先使用 dispatch_label 精确查找
    if (data.dispatch_label && dispatchState.map.has(data.dispatch_label)) {
      dispatchData = dispatchState.map.get(data.dispatch_label);
    } else {
      // fallback: 倒序遍历 labelOrder 找到最后一个活跃的 dispatch 容器
      for (let i = dispatchState.labelOrder.length - 1; i >= 0; i--) {
        const lbl = dispatchState.labelOrder[i];
        const dd = dispatchState.map.get(lbl);
        if (dd) {
          dispatchData = dd;
          break;
        }
      }
    }

    if (dispatchData) {
      _createSubagentRow(data, dispatchData);
      if (_st()) {
        _st().addAgent(data.label, {
          description: data.description || data.label,
          msg_index: data.msg_index,
          status: data.status || 'running',
          phase: '', phaseInfo: '',
          tools: [], usage: {},
          result: '', error: '',
          dispatchLabel: dispatchData.toolLabel,
          startTime: Date.now(),
        });
      }
      return;
    }
    // map 尚未就绪（tool_started 未到）→ 缓冲到 pending，后续由 flush 处理
    dispatchState.pendingAgents.push(data);
    return;
  }

  if (activeAgents[data.label]) return;

  // 独立 agent 气泡
  const el = addBubble('agent');
  const header = document.createElement('div');
  header.className = 'agent-title-line';
  const tag = data.msg_index !== undefined ? '<span class="msg-tag">#' + data.msg_index + '</span> ' : '';
  header.innerHTML = tag
    + '<span class="tree-connector">└</span>'
    + '<span class="agent-desc">' + escapeHtml(data.description || data.label) + '</span>'
    + '<span class="agent-inline-status"><span class="status-dot running"></span></span>'
    + '<span class="agent-meta-inline"></span>';
  el.appendChild(header);

  const statusEl = document.createElement('div');
  statusEl.className = 'agent-status';
  statusEl.innerHTML = '<span class="status-dot running"></span> ' + data.status;
  el.appendChild(statusEl);

  const phaseEl = document.createElement('div');
  phaseEl.className = 'agent-phase-line';
  phaseEl.style.display = 'none';
  el.appendChild(phaseEl);

  const toolsEl = document.createElement('div');
  toolsEl.className = 'agent-tools';
  el.appendChild(toolsEl);

  const metaEl = document.createElement('div');
  metaEl.className = 'agent-meta';
  metaEl.style.display = 'none';
  el.appendChild(metaEl);

  const inlineMeta = header.querySelector('.agent-meta-inline');

  activeAgents[data.label] = {
    el, statusEl, phaseEl, toolsEl, metaEl, toolRecords: {},
    headerEl: header, inlineMeta,
    _phaseStartTime: null,
  };
  if (_st()) {
    _st().addAgent(data.label, {
      description: data.description || data.label,
      msg_index: data.msg_index,
      status: data.status || 'running',
      phase: '', phaseInfo: '',
      tools: [], usage: {},
      result: '', error: '',
      dispatchLabel: null,
      startTime: Date.now(),
    });
  }
  if (typeof _debouncedScrollToBottom === 'function') _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   2. handleAgentStatus — Agent 状态更新
   ═══════════════════════════════════════════════════════════════ */
function handleAgentStatus(data) {
  const agent = activeAgents[data.label];
  if (!agent) return;
  const status = data.status;
  const statusEl = agent.statusEl;
  if (!statusEl) return;

  let statusHtml;
  if (status === 'done' || status === 'completed') {
    statusHtml = '<span class="tick">✓</span> 完成';
  } else if (status === 'fail' || status === 'error') {
    statusHtml = '<span class="cross">✗</span> 失败';
  } else {
    statusHtml = '<span class="status-dot running"></span> ' + escapeHtml(status);
  }
  statusEl.innerHTML = statusHtml;

  if (agent.headerEl) {
    const inlineStatus = agent.headerEl.querySelector('.agent-inline-status');
    if (inlineStatus) inlineStatus.innerHTML = statusHtml;
  }
  if (_st()) _st().updateAgent(data.label, { status: data.status });
  if (status === 'done' || status === 'completed' || status === 'fail' || status === 'error') {
    _globalTimer.unregisterAgent(data.label);
  }
}

/* ═══════════════════════════════════════════════════════════════
   3. handleAgentToolParsing — Agent 子工具解析
   ═══════════════════════════════════════════════════════════════ */
function handleAgentToolParsing(data) {
  const agent = activeAgents[data.agent_label];
  if (!agent) {
    // parallel subagent（不在 activeAgents 中）→ 只更新 store，不操作 DOM
    if (_st()) {
      const st = _st();
      const curAgent = st.getAgents()[data.agent_label];
      if (!curAgent) return;
      const tools = (curAgent.tools) ? [...curAgent.tools] : [];
      const seq = (curAgent._toolSeq || 0) + 1;
      const toolKey = 't' + seq;
      tools.push({ toolKey, tool_name: data.tool_name, tool_id: data.tool_id || '', status: 'parsing', startTime: Date.now() });
      st.updateAgent(data.agent_label, { tools, _toolSeq: seq, _currentToolKey: toolKey });
    }
    return;
  }
  const toolName = data.tool_name;
  const toolsEl = agent.toolsEl;
  if (!toolsEl) return;

  // 自增序列生成唯一键，同名工具多次调用不再互相覆盖
  if (!agent._toolSeq) agent._toolSeq = 0;
  agent._toolSeq += 1;
  const toolKey = 't' + agent._toolSeq;
  agent._currentToolKey = toolKey;

  const row = document.createElement('div');
  row.className = 'agent-tool-record';
  const connector = document.createElement('span');
  connector.className = 'tree-connector';
  connector.textContent = '│';
  row.appendChild(connector);
  const phaseEl = document.createElement('span');
  row.appendChild(phaseEl);
  const timeEl = document.createElement('span');
  timeEl.className = 'tool-elapsed';
  timeEl.textContent = '0.0s';
  row.appendChild(timeEl);

  const _startTime = Date.now();
  phaseEl.innerHTML = '<span class="status-icon-pending">⏳</span> ' + escapeHtml(toolName);
  _globalTimer.registerAgent(data.agent_label + '::tool::' + toolKey, { elapsedEl: timeEl, startTime: _startTime });

  toolsEl.appendChild(row);
  const tool_id = data.tool_id || '';
  agent.toolRecords[toolKey] = { row, phaseEl, timeEl, _startTime, _timer: null, _toolName: toolName, status: 'parsing', tool_id: tool_id };
  _updateAgentToolConnectors(agent);
  if (_st()) {
    const curAgent = _st().getAgents()[data.agent_label];
    const tools = (curAgent && curAgent.tools) ? [...curAgent.tools] : [];
    tools.push({ toolKey, tool_name: toolName, tool_id: tool_id, status: 'parsing', startTime: _startTime });
    _st().updateAgent(data.agent_label, { tools });
  }
  if (typeof _debouncedScrollToBottom === 'function') _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   4. handleAgentToolStarted — Agent 子工具开始执行
   ═══════════════════════════════════════════════════════════════ */
function handleAgentToolStarted(data) {
  const agent = activeAgents[data.agent_label];
  if (!agent) {
    // parallel subagent（不在 activeAgents 中）→ 只更新 store，不操作 DOM
    if (_st()) {
      const st = _st();
      const curAgent = st.getAgents()[data.agent_label];
      if (!curAgent) return;
      const tools = (curAgent.tools) ? [...curAgent.tools] : [];
      // ★ 优先用 tool_id 精确匹配，兜底 _currentToolKey + 倒序遍历
      let foundIdx = -1;
      if (data.tool_id) {
        foundIdx = tools.findIndex(t => t.tool_id === data.tool_id && t.status === 'parsing');
      }
      if (foundIdx === -1) {
        const currentToolKey = curAgent._currentToolKey;
        if (currentToolKey) {
          foundIdx = tools.findIndex(t => t.toolKey === currentToolKey && t.tool_name === data.tool_name && t.status === 'parsing');
        }
      }
      if (foundIdx === -1) {
        // 回退：倒序遍历匹配
        for (let i = tools.length - 1; i >= 0; i--) {
          if (tools[i].tool_name === data.tool_name && tools[i].status === 'parsing') {
            foundIdx = i;
            break;
          }
        }
      }
      if (foundIdx !== -1) {
        tools[foundIdx] = { ...tools[foundIdx], status: 'started', startTime: Date.now(), detail: data.detail || '', tool_id: data.tool_id || tools[foundIdx].tool_id };
        st.updateAgent(data.agent_label, { _currentToolKey: tools[foundIdx].toolKey });
      } else {
        // parsing 阶段被跳过，创建新记录
        const seq = (curAgent._toolSeq || 0) + 1;
        const toolKey = 't' + seq;
        tools.push({ toolKey, tool_name: data.tool_name, tool_id: data.tool_id || '', status: 'started', startTime: Date.now(), detail: data.detail || '' });
        st.updateAgent(data.agent_label, { _toolSeq: seq, _currentToolKey: toolKey });
      }
      st.updateAgent(data.agent_label, { tools });
    }
    return;
  }
  const toolName = data.tool_name;
  const toolsEl = agent.toolsEl;
  if (!toolsEl) return;

  const toolIcons = { cmd: '⚡', read_file: '📖', write_file: '📝', update_file: '🔧', subagent: '📡' };
  const toolIcon = toolIcons[toolName] || '·';

  let rec = null;
  let toolKey = agent._currentToolKey;

  // 优先用 tool_id 精确匹配（彻底消除 _currentToolKey 竞态）
  if (data.tool_id) {
    for (const [k, v] of Object.entries(agent.toolRecords)) {
      if (v.tool_id === data.tool_id && v.status === 'parsing') {
        rec = v;
        toolKey = k;
        agent._currentToolKey = toolKey;
        break;
      }
    }
  }

  // tool_id 未匹配 → fallback: _currentToolKey 匹配
  if (!rec && toolKey && agent.toolRecords[toolKey] && agent.toolRecords[toolKey]._toolName === toolName && agent.toolRecords[toolKey].status === 'parsing') {
    rec = agent.toolRecords[toolKey];
  }

  // 仍不匹配 → fallback: 倒序遍历查找同名 parsing 记录
  if (!rec) {
    const keys = Object.keys(agent.toolRecords).sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)));
    for (let i = keys.length - 1; i >= 0; i--) {
      const k = keys[i];
      const r = agent.toolRecords[k];
      if (r._toolName === toolName && r.status === 'parsing') {
        rec = r;
        toolKey = k;
        agent._currentToolKey = toolKey;
        break;
      }
    }
  }

  if (!rec) {
    // 确实没有 parsing 记录（parsing 阶段被完全跳过），创建新行
    if (!agent._toolSeq) agent._toolSeq = 0;
    agent._toolSeq += 1;
    toolKey = 't' + agent._toolSeq;
    agent._currentToolKey = toolKey;
    const row = document.createElement('div');
    row.className = 'agent-tool-record';
    const connector = document.createElement('span');
    connector.className = 'tree-connector';
    connector.textContent = '├';
    row.appendChild(connector);
    const phaseEl = document.createElement('span');
    row.appendChild(phaseEl);
    const timeEl = document.createElement('span');
    timeEl.className = 'tool-elapsed';
    timeEl.textContent = '0.0s';
    row.appendChild(timeEl);
    const _startTime = Date.now();
    phaseEl.innerHTML = '<span class="status-icon-running">●</span> <span class="tool-icon">' + toolIcon + '</span> <span class="tool-name">' + escapeHtml(toolName) + '</span>';
    _globalTimer.registerAgent(data.agent_label + '::tool::' + toolKey, { elapsedEl: timeEl, startTime: _startTime });
    toolsEl.appendChild(row);
    rec = { row, phaseEl, timeEl, _startTime, _timer: null, _toolName: toolName, status: 'parsing', tool_id: data.tool_id || '' };
    agent.toolRecords[toolKey] = rec;
  }

  // 更新为 started
  rec.status = 'started';
  rec._startTime = Date.now();
  rec.phaseEl.innerHTML = '<span class="status-icon-running">●</span> <span class="tool-icon">' + toolIcon + '</span> <span class="tool-name">' + escapeHtml(toolName) + '</span>';
  if (rec.timeEl) rec.timeEl.textContent = '0.0s';

  if (data.detail) {
    const detailSpan = document.createElement('span');
    detailSpan.style.cssText = 'color: var(--text-dim); font-size: 10px; margin-left: 4px;';
    detailSpan.textContent = ' ' + data.detail;
    rec.phaseEl.appendChild(detailSpan);
  }
  _updateAgentToolConnectors(agent);
  // 只保留最新 3 条工具记录，删除最旧的多余行
  var _akeys = Object.keys(agent.toolRecords).sort(function(a,b){return parseInt(a.slice(1))-parseInt(b.slice(1));});
  while (_akeys.length > 3) {
    var _ok = _akeys.shift();
    var _orec = agent.toolRecords[_ok];
    if (_orec && _orec.row && _orec.row.parentNode) _orec.row.parentNode.removeChild(_orec.row);
    delete agent.toolRecords[_ok];
  }
  if (_st()) {
    const curAgent = _st().getAgents()[data.agent_label];
    const tools = (curAgent && curAgent.tools) ? [...curAgent.tools] : [];
    // 优先递归 toolKey，兜底 tool_id
    let foundIdx = toolKey ? tools.findIndex(t => t.toolKey === toolKey) : -1;
    if (foundIdx === -1 && data.tool_id) {
      foundIdx = tools.findIndex(t => t.tool_id === data.tool_id && t.status !== 'done');
    }
    if (foundIdx !== -1) {
      tools[foundIdx] = { ...tools[foundIdx], status: 'started', startTime: rec._startTime, detail: data.detail || '', tool_id: data.tool_id || tools[foundIdx].tool_id };
    } else {
      tools.push({ toolKey, tool_name: toolName, tool_id: data.tool_id || '', status: 'started', startTime: rec._startTime, detail: data.detail || '' });
    }
    _st().updateAgent(data.agent_label, { tools });
  }
  if (typeof _debouncedScrollToBottom === 'function') _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   5. handleAgentToolDone — Agent 子工具完成
   ═══════════════════════════════════════════════════════════════ */
function handleAgentToolDone(data) {
  const agent = activeAgents[data.agent_label];
  if (!agent) {
    // parallel subagent（不在 activeAgents 中）→ 只更新 store，不操作 DOM
    if (_st()) {
      const st = _st();
      const curAgent = st.getAgents()[data.agent_label];
      if (!curAgent) return;
      const tools = (curAgent.tools) ? [...curAgent.tools] : [];
      // ★ 优先用 tool_id 精确匹配，兜底 _currentToolKey + 倒序遍历
      let foundIdx = -1;
      if (data.tool_id) {
        foundIdx = tools.findIndex(t => t.tool_id === data.tool_id && t.status === 'started');
      }
      if (foundIdx === -1) {
        const currentToolKey = curAgent._currentToolKey;
        if (currentToolKey) {
          foundIdx = tools.findIndex(t => t.toolKey === currentToolKey && t.tool_name === data.tool_name && t.status === 'started');
        }
      }
      if (foundIdx === -1) {
        for (let i = tools.length - 1; i >= 0; i--) {
          if (tools[i].tool_name === data.tool_name && tools[i].status === 'started') {
            foundIdx = i;
            break;
          }
        }
      }
      if (foundIdx !== -1) {
        tools[foundIdx] = { ...tools[foundIdx], status: 'done', elapsed: data.elapsed || 0, success: data.success, tool_id: data.tool_id || tools[foundIdx].tool_id };
      }
      st.updateAgent(data.agent_label, { tools });
    }
    return;
  }
  const toolName = data.tool_name;

  // 优先用 tool_id 精确匹配（彻底消除 _currentToolKey 竞态）
  let rec = null;
  let toolKey = null;
  if (data.tool_id) {
    for (const [k, v] of Object.entries(agent.toolRecords)) {
      if (v.tool_id === data.tool_id && v.status === 'started') {
        rec = v;
        toolKey = k;
        break;
      }
    }
  }

  // tool_id 未匹配 → fallback: _currentToolKey
  if (!rec && agent._currentToolKey && agent.toolRecords[agent._currentToolKey] && agent.toolRecords[agent._currentToolKey]._toolName === toolName) {
    rec = agent.toolRecords[agent._currentToolKey];
    toolKey = agent._currentToolKey;
  }

  // 仍不匹配 → fallback: 倒序查找同名 'started' 记录
  if (!rec) {
    const keys = Object.keys(agent.toolRecords).sort((a, b) => parseInt(a.slice(1)) - parseInt(b.slice(1)));
    for (let i = keys.length - 1; i >= 0; i--) {
      const k = keys[i];
      const r = agent.toolRecords[k];
      if (r._toolName === toolName && r.status === 'started') {
        rec = r;
        toolKey = k;
        break;
      }
    }
  }
  if (!rec) return;

  _globalTimer.unregisterAgent(data.agent_label + '::tool::' + toolKey);

  const toolIcons = { cmd: '⚡', read_file: '📖', write_file: '📝', update_file: '🔧', subagent: '📡' };
  const toolIcon = toolIcons[toolName] || '·';
  const elapsed = rec._startTime ? ((Date.now() - rec._startTime) / 1000).toFixed(1) : '0.0';
  const statusIcon = data.success ? '<span class="status-icon-done">✔</span>' : '<span class="status-icon-fail">✗</span>';
  rec.phaseEl.innerHTML = statusIcon + ' <span class="tool-icon">' + toolIcon + '</span> <span class="tool-name">' + escapeHtml(toolName) + '</span>';
  if (rec.timeEl) rec.timeEl.textContent = elapsed + 's';
  rec.status = 'done';
  _updateAgentToolConnectors(agent);
  // 只保留最新 3 条，删除最旧的多余 DOM 行
  var _dkeys = Object.keys(agent.toolRecords).sort(function(a,b){return parseInt(a.slice(1))-parseInt(b.slice(1));});
  while (_dkeys.length > 3) {
    var _dok = _dkeys.shift();
    var _drec = agent.toolRecords[_dok];
    if (_drec && _drec.row && _drec.row.parentNode) _drec.row.parentNode.removeChild(_drec.row);
    delete agent.toolRecords[_dok];
  }

  if (_st()) {
    const curAgent = _st().getAgents()[data.agent_label];
    const tools = (curAgent && curAgent.tools) ? [...curAgent.tools] : [];
    // 优先递归 toolKey，兜底 tool_id
    let foundIdx = toolKey ? tools.findIndex(t => t.toolKey === toolKey) : -1;
    if (foundIdx === -1 && data.tool_id) {
      foundIdx = tools.findIndex(t => t.tool_id === data.tool_id);
    }
    if (foundIdx !== -1) {
      tools[foundIdx] = { ...tools[foundIdx], status: 'done', startTime: rec._startTime, elapsed: elapsed, success: data.success, tool_id: data.tool_id || tools[foundIdx].tool_id };
    } else {
      tools.push({ toolKey, tool_name: toolName, tool_id: data.tool_id || '', status: 'done', startTime: rec._startTime, elapsed: elapsed, success: data.success });
    }
    _st().updateAgent(data.agent_label, { tools });
  }
  if (typeof _debouncedScrollToBottom === 'function') _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   6. _updateAgentPhaseText — Agent 阶段文本更新（辅助）
   ═══════════════════════════════════════════════════════════════ */
function _updateAgentPhaseText(agent) {
  if (!agent || !agent._currentPhase || !agent.phaseEl) return;
  const elapsed = ((Date.now() - agent._phaseStartTime) / 1000).toFixed(1);
  const phase = agent._currentPhase;
  const info = agent._phaseInfo || '';
  let text = '';
  if (phase === 'thinking') text = '...thinking ' + elapsed + 's';
  else if (phase === 'answering') text = '...answering ' + elapsed + 's';
  else if (phase === 'parsing') text = '...parsing' + (info ? ' ' + info : '');
  else if (phase === 'batch') text = '...batch' + (info ? ' ' + info : '');
  else if (phase === 'error') text = '...error ' + info;
  else text = '...' + phase + (info ? ' ' + info : '');
  agent.phaseEl.textContent = text;
}

/* ═══════════════════════════════════════════════════════════════
   7. handleAgentPhase — Agent 阶段变化
   ═══════════════════════════════════════════════════════════════ */
function handleAgentPhase(data) {
  const agent = activeAgents[data.agent_label];
  if (!agent) return;
  const phaseEl = agent.phaseEl;
  if (!phaseEl) return;
  const phase = data.phase;
  const info = data.info || '';

  if (phase) {
    phaseEl.style.display = '';
    agent._phaseStartTime = Date.now();
    agent._currentPhase = phase;
    agent._phaseInfo = info;
    _globalTimer.registerAgent(data.agent_label + '::phase', {
      phaseEl: phaseEl, phaseStartTime: agent._phaseStartTime,
      currentPhase: phase, phaseInfo: info,
    });
  } else {
    phaseEl.style.display = 'none';
    agent._phaseStartTime = null;
    agent._currentPhase = null;
    agent._phaseInfo = '';
    _globalTimer.unregisterAgent(data.agent_label + '::phase');
  }
  if (_st()) _st().updateAgent(data.agent_label, { phase: data.phase || '', phaseInfo: data.info || '' });
}

/* ═══════════════════════════════════════════════════════════════
   8. handleAgentUsage — Agent 用量更新
   ═══════════════════════════════════════════════════════════════ */
function handleAgentUsage(data) {
  const usage = data.usage || {};
  const agent = activeAgents[data.agent_label];
  if (!agent) return;
  const inlineMeta = agent.inlineMeta;
  if (!inlineMeta) return;
  const parts = [];
  if (usage.input !== undefined) parts.push('↑' + (typeof formatTokens === 'function' ? formatTokens(usage.input) : usage.input));
  if (usage.output !== undefined) parts.push('↓' + (typeof formatTokens === 'function' ? formatTokens(usage.output) : usage.output));
  if (usage.speed && usage.speed > 0) parts.push(usage.speed.toFixed(1) + '/s');
  inlineMeta.textContent = parts.length ? parts.join(' · ') : '';
  if (_st()) _st().updateAgent(data.agent_label, { usage: { input: usage.input, output: usage.output, speed: usage.speed } });
}

/* ═══════════════════════════════════════════════════════════════
   9. handleAgentResult — Agent 执行结果
   ═══════════════════════════════════════════════════════════════ */
function handleAgentResult(data) {
  const agent = activeAgents[data.agent_label];
  if (!agent) return;
  const dispatchLabel = agent._dispatchLabel;
  const dispatchData = dispatchLabel ? dispatchState.map.get(dispatchLabel) : null;
  const agentsContainer = dispatchData ? dispatchData.agentsContainer :
    (agent.row && agent.row.parentNode ? agent.row.parentNode : null);

  if (agentsContainer && agent.row && agent.row.parentNode) {
    agent.row.parentNode.removeChild(agent.row);
    const resultRow = document.createElement('div');
    resultRow.className = 'tool-parallel-row';
    const header = document.createElement('div');
    header.className = 'tool-header';
    const icon = data.error ? '❌' : '✅';
    header.innerHTML = icon + ' ' + escapeHtml(data.description || data.agent_label);
    resultRow.appendChild(header);
    if (data.result) {
      const contentDiv = document.createElement('div');
      contentDiv.style.cssText = 'font-size: 13px; line-height: 1.5; padding-left: 2px; font-family: var(--font); white-space: normal; word-break: break-word; max-width: 100%; box-sizing: border-box; overflow-x: auto;';
      contentDiv.innerHTML = (typeof _renderMarkdownFallback === 'function' ? _renderMarkdownFallback(data.result) : escapeHtml(data.result || ''));
      if (typeof scheduleTask === 'function') scheduleTask(() => postProcessMarkdown(contentDiv));
      resultRow.appendChild(contentDiv);
    }
    if (data.error) {
      const errDiv = document.createElement('div');
      errDiv.style.cssText = 'color: var(--error); font-size: 12px; margin-top: 2px; padding-left: 2px;';
      errDiv.textContent = '错误: ' + data.error;
      resultRow.appendChild(errDiv);
    }
    agentsContainer.appendChild(resultRow);
    _globalTimer.unregisterAgent(data.agent_label);
    delete activeAgents[data.agent_label];
    if (typeof _debouncedScrollToBottom === 'function') _debouncedScrollToBottom();
    return;
  }

  if (agent.el && agent.el.parentNode) {
    if (agent.headerEl) {
      const connector = agent.headerEl.querySelector('.tree-connector');
      if (connector) connector.textContent = '└';
      const inlineStatus = agent.headerEl.querySelector('.agent-inline-status');
      if (inlineStatus) inlineStatus.textContent = data.error ? '✗' : '✔';
    }
    if (agent.toolsEl) agent.toolsEl.style.display = 'none';
    if (agent.phaseEl) agent.phaseEl.style.display = 'none';
    if (data.result) {
      const oldResult = agent.el.querySelector('.agent-result');
      if (oldResult) oldResult.remove();
      const resultDiv = document.createElement('div');
      resultDiv.className = 'agent-result';
      resultDiv.innerHTML = (typeof _renderMarkdownFallback === 'function' ? _renderMarkdownFallback(data.result) : escapeHtml(data.result || ''));
      agent.el.appendChild(resultDiv);
      requestAnimationFrame(function () {
        if (typeof window.postProcessMarkdown === 'function') window.postProcessMarkdown(resultDiv);
      });
    }
  } else {
    if (agent.el && agent.el.parentNode) agent.el.parentNode.removeChild(agent.el);
    _globalTimer.unregisterAgent(data.agent_label);
    delete activeAgents[data.agent_label];
    const resultEl = addBubble('tool');
    resultEl.style.cssText = 'border-left: 3px solid var(--success); margin-left: 12px; max-width: 85%;';
    const header = document.createElement('div');
    header.className = 'tool-header';
    const icon2 = data.error ? '❌' : '✅';
    header.innerHTML = icon2 + ' ' + escapeHtml(data.description || data.agent_label);
    resultEl.appendChild(header);
    if (data.result) {
      const contentDiv = document.createElement('div');
      contentDiv.className = 'tool-output';
      contentDiv.style.cssText = 'display: block; max-height: none; overflow-y: visible; word-break: break-word; max-width: 100%; box-sizing: border-box; font-family: var(--font); font-size: 13px; white-space: normal; margin-top: 4px; overflow-x: auto;';
      contentDiv.innerHTML = (typeof _renderMarkdownFallback === 'function' ? _renderMarkdownFallback(data.result) : escapeHtml(data.result || ''));
      resultEl.appendChild(contentDiv);
      if (typeof scheduleTask === 'function') scheduleTask(() => postProcessMarkdown(contentDiv));
    }
    if (data.error) {
      const errDiv = document.createElement('div');
      errDiv.style.cssText = 'color: var(--error); font-size: 12px; margin-top: 4px;';
      errDiv.textContent = '错误: ' + data.error;
      resultEl.appendChild(errDiv);
    }
  }

  if (_st()) {
    // ★ Bug 修复：标记为已完成但暂不删除（2s 后标记为过期，next dispatch 轮次清理）
    //   避免 setTimeout 删除导致 Preact 组件闪烁
    _st().updateAgent(data.agent_label, {
      status: data.error ? 'fail' : 'done',
      result: data.result || '',
      error: data.error || '',
      _completed: true,       // 标记完成
      _completedAt: Date.now(), // 记录完成时间戳
      // ★ Bug 4 修复：Agent 完成后清理工具记录（已在 UI 渲染完毕，不再需要保留在 store）
      tools: [],
      _toolSeq: 0,
      _currentToolKey: null,
    });
    // 改用 setTimeout 标记过期而不是删除，保留在 store 中供 UI 引用但不再更新
  }
  if (typeof _debouncedScrollToBottom === 'function') _debouncedScrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   导出到全局（供 handlers.js 使用）
   ═══════════════════════════════════════════════════════════════ */
Object.assign(window, {
  handleAgentAdded,
  handleAgentStatus,
  handleAgentToolParsing,
  handleAgentToolStarted,
  handleAgentToolDone,
  handleAgentPhase,
  handleAgentUsage,
  handleAgentResult,
  _updateAgentToolConnectors,
  _updateAgentPhaseText,
});
