/* ═══════════════════════════════════════════════════════════════
   全局计时器管理器 — 单一定时器替代 N 个独立 setInterval
   所有工具/Agent 的耗时更新通过此管理器统一调度（200ms 间隔）
   依赖: 无外部依赖，直接定义在全局作用域
   ═══════════════════════════════════════════════════════════════ */
const _globalTimer = {
  _id: null,
  _tools: new Map(),   // label → { phaseEl, startTime, type (parsing|exec), execStart }
  _agents: new Map(),  // label → { elapsedEl, startTime, phaseEl, phaseStartTime, currentPhase, updateFn }

  start() {
    if (this._id) return;
    this._id = setInterval(() => this._tick(), 200);
  },

  stop() {
    if (this._id) {
      clearInterval(this._id);
      this._id = null;
    }
  },

  _tick() {
    // ★ 无活跃工具/Agent 时直接跳过，避免空转遍历
    if (this._tools.size === 0 && this._agents.size === 0) {
      this.stop();
      return;
    }
    const now = Date.now();
    // 更新工具耗时
    for (const [, t] of this._tools) {
      if (!t.active) continue;
      const baseTime = t.type === 'exec' ? (t.execStart || t.startTime) : t.startTime;
      if (t.phaseEl && baseTime) {
        const elapsed = ((now - baseTime) / 1000).toFixed(1);
        // ★ 使用缓存的 _timerEl 避免每次 querySelector；无缓存时 fallback 查询
        let timerEl = t._timerEl;
        if (!timerEl && t.phaseEl) {
          timerEl = t.phaseEl.querySelector('.tool-timer-text');
          if (timerEl) t._timerEl = timerEl;
        }
        if (timerEl) {
          timerEl.textContent = elapsed + 's';
        }
      }
    }
    // 更新 agent 耗时
    for (const [, a] of this._agents) {
      if (!a.active) continue;
      if (a.elapsedEl && a.startTime) {
        a.elapsedEl.textContent = ((now - a.startTime) / 1000).toFixed(1) + 's';
      }
      // 更新 agent phase 行
      if (a.phaseEl && a.phaseStartTime && a.currentPhase) {
        const elapsed = ((now - a.phaseStartTime) / 1000).toFixed(1);
        const info = a.phaseInfo || '';
        let text = '';
        if (a.currentPhase === 'thinking') text = '...thinking ' + elapsed + 's';
        else if (a.currentPhase === 'answering') text = '...answering ' + elapsed + 's';
        else if (a.currentPhase === 'parsing') text = '...parsing' + (info ? ' ' + info : '');
        else if (a.currentPhase === 'batch') text = '...batch' + (info ? ' ' + info : '');
        else text = '...' + a.currentPhase + (info ? ' ' + info : '');
        a.phaseEl.textContent = text;
      }
    }
  },

  registerTool(label, data) {
    // ★ 缓存 timerEl 引用，避免 _tick() 中每次 querySelector
    let timerEl = null;
    if (data.phaseEl) {
      timerEl = data.phaseEl.querySelector('.tool-timer-text');
    }
    this._tools.set(label, { ...data, active: true, _timerEl: timerEl });
    this.start();
  },

  unregisterTool(label) {
    this._tools.delete(label);
    if (this._tools.size === 0 && this._agents.size === 0) this.stop();
  },

  registerAgent(label, data) {
    this._agents.set(label, { ...data, active: true });
    this.start();
  },

  unregisterAgent(label) {
    this._agents.delete(label);
    if (this._tools.size === 0 && this._agents.size === 0) this.stop();
  },

  /** 清理所有计时器 */
  clearAll() {
    this._tools.clear();
    this._agents.clear();
    this.stop();
  },
};
