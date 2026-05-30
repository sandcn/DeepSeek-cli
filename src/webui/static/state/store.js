/* ═══════════════════════════════════════════════════════════════
   状态管理 — Single Source of Truth
   作为全局状态 → 所有组件的同步桥梁
   支持通用状态 + 集合管理（工具/Agent/消息）
   ═══════════════════════════════════════════════════════════════ */

/** 观察者集合 */
const _listeners = new Set();
/** 流式输出时暂停 Preact 通知标记 */
let _notificationsPaused = false;
/** 暂停期间缓存最后一次状态快照 */
let _pendingNotification = null;

/** 状态快照 — 单一状态源 */
const _state = {
  // ── 工具集合 label → toolData ──
  tools: {},

  // ── Agent 集合 label → agentData ──
  agents: {},

  // ── 消息集合 key → msgData ──
  messages: {},

  // ── dispatch 状态 ──
  dispatch: {
    labelOrder: [],
    agentCounter: 0,
    batchDone: 0,
    pendingAgents: [],
  },
};

// ═══════════════════════════════════════════════════════════════
// 通用状态操作
// ═══════════════════════════════════════════════════════════════

/** 获取当前状态快照（浅拷贝） */
export function getState() {
  return {
    ..._state,
    tools: { ..._state.tools },
    agents: { ..._state.agents },
    messages: { ..._state.messages },
    dispatch: { ..._state.dispatch },
  };
}

/** 合并更新状态，通知所有订阅者 */
export function setState(partial) {
  // ★ #16 修复：先备份旧值，再修改 _state，确保原子性
  const prevTools = _state.tools;
  const prevAgents = _state.agents;
  const prevMessages = _state.messages;
  const prevDispatch = _state.dispatch;

  Object.assign(_state, partial);

  // ★ 创建 snapshot 时：如果 partial 包含某集合则直接使用 _state 中的新值，
  //   否则从备份复制旧值（避免丢失未被 partial 覆盖的集合引用一致性）
  const snapshot = {
    ..._state,
    tools: partial && partial.tools !== undefined ? _state.tools : { ...prevTools },
    agents: partial && partial.agents !== undefined ? _state.agents : { ...prevAgents },
    messages: partial && partial.messages !== undefined ? _state.messages : { ...prevMessages },
    dispatch: partial && partial.dispatch !== undefined ? _state.dispatch : { ...prevDispatch },
  };
  // 流式输出时暂停通知，只缓存最后一次快照
  if (_notificationsPaused) {
    _pendingNotification = snapshot;
    return;
  }
  for (const fn of _listeners) {
    try { fn(snapshot); } catch (e) { console.warn('store listener error:', e); }
  }
}

/** 订阅状态变化，返回取消订阅函数 */
export function subscribe(fn) {
  _listeners.add(fn);
  return () => { _listeners.delete(fn); };
}

// ═══════════════════════════════════════════════════════════════
// 工具集合管理
// ═══════════════════════════════════════════════════════════════

/** 添加或替换一个工具记录 */
export function addTool(label, toolData) {
  _state.tools = { ..._state.tools, [label]: { ...toolData, label } };
  setState({ tools: _state.tools });
}

/** 更新指定工具的部分字段 */
export function updateTool(label, partial) {
  const existing = _state.tools[label];
  if (!existing) return;
  _state.tools = { ..._state.tools, [label]: { ...existing, ...partial } };
  setState({ tools: _state.tools });
}

/** 删除一个工具记录 */
export function removeTool(label) {
  if (!_state.tools[label]) return;
  _state.tools = { ..._state.tools };
  delete _state.tools[label];
  setState({ tools: _state.tools });
}

/** 清空所有工具记录 */
export function clearTools() {
  _state.tools = {};
  setState({ tools: {} });
}

/** 获取全部工具（浅拷贝） */
export function getTools() {
  return { ..._state.tools };
}

// ═══════════════════════════════════════════════════════════════
// Agent 集合管理
// ═══════════════════════════════════════════════════════════════

/** 添加或替换一个 Agent 记录 */
export function addAgent(label, agentData) {
  _state.agents = { ..._state.agents, [label]: { ...agentData, label } };
  setState({ agents: _state.agents });
}

/** 更新指定 Agent 的部分字段 */
export function updateAgent(label, partial) {
  const existing = _state.agents[label];
  if (!existing) return;
  _state.agents = { ..._state.agents, [label]: { ...existing, ...partial } };
  setState({ agents: _state.agents });
}

/** 删除一个 Agent 记录 */
export function removeAgent(label) {
  if (!_state.agents[label]) return;
  _state.agents = { ..._state.agents };
  delete _state.agents[label];
  setState({ agents: _state.agents });
}

/** 清空所有 Agent 记录 */
export function clearAgents() {
  _state.agents = {};
  setState({ agents: {} });
}

/** 获取全部 Agent（浅拷贝） */
export function getAgents() {
  return { ..._state.agents };
}

// ═══════════════════════════════════════════════════════════════
// 消息集合管理（bubble 气泡）
// ═══════════════════════════════════════════════════════════════

/** 添加或替换一条消息 */
export function addMessage(key, msgData) {
  _state.messages[key] = { ...msgData, key };
  setState({ messages: _state.messages });
}

/**
 * 批量添加消息 — 避免 session_initialized 等场景逐条触发 O(n) setState
 * @param {Object} messagesMap - { key: msgData, ... }
 * 一次性合并所有消息到 _state.messages，只触发一次 setState
 */
export function addMessagesBatch(messagesMap) {
  if (!messagesMap || typeof messagesMap !== 'object') return;
  let changed = false;
  for (const key of Object.keys(messagesMap)) {
    if (!_state.messages[key]) {
      _state.messages[key] = { ...messagesMap[key], key };
      changed = true;
    }
  }
  if (changed) {
    setState({ messages: _state.messages });
  }
}

/** 更新指定消息的部分字段 */
export function updateMessage(key, partial) {
  const existing = _state.messages[key];
  if (!existing) return;
  // ★ #17 修复：不再直接突变 existing，而是创建新对象
  _state.messages = { ..._state.messages, [key]: { ...existing, ...partial } };
  setState({ messages: _state.messages });
}

/** 删除一条消息 */
export function removeMessage(key) {
  if (!_state.messages[key]) return;
  delete _state.messages[key];
  setState({ messages: _state.messages });
}

/** 清空所有消息 */
export function clearMessages() {
  _state.messages = {};
  setState({ messages: {} });
}

/** 获取全部消息（浅拷贝） */
export function getMessages() {
  return { ..._state.messages };
}

// ═══════════════════════════════════════════════════════════════
// Dispatch 状态管理
// ═══════════════════════════════════════════════════════════════

/** 更新 dispatch 状态 */
export function setDispatchState(partial) {
  _state.dispatch = { ..._state.dispatch, ...partial };
  setState({ dispatch: _state.dispatch });
}

/** 重置 dispatch 状态 */
export function resetDispatchState() {
  _state.dispatch = { labelOrder: [], agentCounter: 0, batchDone: 0, pendingAgents: [] };
  setState({ dispatch: _state.dispatch });
}

/** 获取 dispatch 状态 */
export function getDispatchState() {
  return { ..._state.dispatch };
}

// ═══════════════════════════════════════════════════════════════
// 批量重置
// ═══════════════════════════════════════════════════════════════

/** 重置所有状态（工具/Agent/消息/dispatch） */
export function resetAll() {
  _state.tools = {};
  _state.agents = {};
  _state.messages = {};
  _state.dispatch = { labelOrder: [], agentCounter: 0, batchDone: 0, pendingAgents: [] };
  _notificationsPaused = false;
  _pendingNotification = null;
  setState({
    tools: {},
    agents: {},
    messages: {},
    dispatch: { ..._state.dispatch },
  });
}

// ═══════════════════════════════════════════════════════════════
// 流式暂停/恢复（防止流式输出时 Preact 频繁重渲染）
// ═══════════════════════════════════════════════════════════════

/**
 * 暂停/恢复 Preact 通知。
 * 流式输出期间暂停，大幅减少 Preact VDOM diff 开销。
 * 暂停期间最后一次缓存的状态快照会在恢复时立即推送。
 */
export function setNotificationsPaused(paused) {
  _notificationsPaused = paused;
  if (!paused && _pendingNotification) {
    // 恢复时推送最后一次缓存的状态
    const snapshot = _pendingNotification;
    _pendingNotification = null;
    for (const fn of _listeners) {
      try { fn(snapshot); } catch (e) { console.warn('store listener error:', e); }
    }
  }
}

// ═══════════════════════════════════════════════════════════════
// Bridge 兼容存根（已弃用，保留为空函数避免调用方报错）
// ═══════════════════════════════════════════════════════════════

export function startBridge() { /* 已弃用 */ }
export function stopBridge()  { /* 已弃用 */ }
