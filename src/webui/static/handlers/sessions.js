/* ═══════════════════════════════════════════════════════════════
   handlers/sessions.js — 历史会话浏览 + 弹窗管理
   从 handlers.js 提取，依赖 ws-client.js (ws), utils.js (escapeHtml)
   handlers.js 提供 _st
   ═══════════════════════════════════════════════════════════════ */

/** 历史会话状态 */
let _historyState = { sessions: [], currentId: '' };

/** 打开历史会话弹窗 */
function _openHistoryModal() {
  const overlay = document.getElementById('history-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  document.getElementById('history-list').innerHTML = '<div class="history-loading">加载中...</div>';
  window.ws && window.ws.send({ type: 'get_sessions' });
}

/** 关闭历史会话弹窗 */
function _closeHistoryModal() {
  const overlay = document.getElementById('history-overlay');
  if (overlay) overlay.classList.add('hidden');
}

/** 渲染会话列表 */
function _renderHistoryList(sessions, currentId) {
  const listEl = document.getElementById('history-list');
  _historyState.sessions = sessions || [];
  _historyState.currentId = currentId || '';

  if (!sessions || sessions.length === 0) {
    listEl.innerHTML = '<div class="history-empty">📋 暂无已保存的会话<br><span style="font-size:11px;opacity:0.6;">发送消息后会话会自动保存</span></div>';
    document.getElementById('history-count').textContent = '';
    return;
  }

  document.getElementById('history-count').textContent = '(' + sessions.length + ')';
  const sorted = [...sessions].sort((a, b) => new Date(b.saved_at || 0).getTime() - new Date(a.saved_at || 0).getTime());

  const groups = {};
  for (const session of sorted) {
    const d = new Date(session.saved_at || 0);
    if (isNaN(d.getTime())) continue;
    const key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    if (!groups[key]) groups[key] = { year: d.getFullYear(), month: d.getMonth() + 1, label: d.getFullYear() + '年' + (d.getMonth() + 1) + '月', sessions: [] };
    groups[key].sessions.push(session);
  }

  const monthKeys = Object.keys(groups).sort().reverse();
  if (monthKeys.length === 0) {
    listEl.innerHTML = '<div class="history-empty">📋 暂无已保存的会话</div>';
    return;
  }

  const newestMonthKey = monthKeys[0];
  let html = '';
  for (const monthKey of monthKeys) {
    const group = groups[monthKey];
    const isDefaultOpen = monthKey === newestMonthKey;
    html += '<div class="history-month-group">'
      + '<div class="history-month-header' + (isDefaultOpen ? '' : ' collapsed') + '" data-month="' + monthKey + '">'
      + '<span class="history-month-arrow">' + (isDefaultOpen ? '▼' : '▶') + '</span>'
      + '<span class="history-month-label">' + group.label + '</span>'
      + '<span class="history-month-count">' + group.sessions.length + ' 个会话</span>'
      + '</div><div class="history-month-body' + (isDefaultOpen ? '' : ' collapsed') + '">';

    for (const session of group.sessions) {
      const isCurrent = session.id === currentId;
      const title = session.title || '(无标题)';
      const model = session.model || '?';
      const msgCount = session.message_count || 0;
      let displayTime = '';
      try {
        const d = new Date(session.saved_at || 0);
        if (!isNaN(d.getTime())) {
          const diffMs = Date.now() - d;
          const diffMin = Math.floor(diffMs / 60000);
          if (diffMin < 1) displayTime = '刚才';
          else if (diffMin < 60) displayTime = diffMin + '分钟前';
          else if (diffMin < 1440) displayTime = Math.floor(diffMin / 60) + '小时前';
          else if (diffMin < 10080) displayTime = Math.floor(diffMin / 1440) + '天前';
          else displayTime = d.toLocaleDateString('zh-CN');
        }
      } catch (_) { displayTime = session.saved_at || ''; }
      const cls = 'history-session-row' + (isCurrent ? ' current' : '');
      html += '<div class="' + cls + '" data-id="' + escapeHtml(session.id) + '">'
        + '<div class="history-session-info">'
        + '<div class="history-session-title" data-session-id="' + escapeHtml(session.id) + '">' + escapeHtml(title) + '</div>'
        + '<div class="history-session-meta">'
        + '<span class="history-session-model">' + escapeHtml(model) + '</span>'
        + '<span class="history-session-time">' + displayTime + '</span>'
        + '<span class="history-session-msgs">' + msgCount + ' 条消息</span>'
        + '</div></div>'
        + '<div class="history-session-actions">'
        + '<button class="history-action-btn history-load-btn" data-id="' + escapeHtml(session.id) + '"' + (isCurrent ? ' disabled' : '') + ' title="' + (isCurrent ? '当前会话' : '加载此会话') + '">📂 加载</button>'
        + '<button class="history-action-btn history-del-btn" data-id="' + escapeHtml(session.id) + '" title="删除此会话">🗑️ 删除</button>'
        + '</div></div>';
    }
    html += '</div></div>';
  }
  listEl.innerHTML = html;

  listEl.querySelectorAll('.history-month-header').forEach(header => {
    header.addEventListener('click', (e) => {
      e.stopPropagation();
      const body = header.nextElementSibling;
      if (!body) return;
      const collapsed = body.classList.contains('collapsed');
      body.classList.toggle('collapsed');
      header.classList.toggle('collapsed');
      const arrow = header.querySelector('.history-month-arrow');
      if (arrow) arrow.textContent = collapsed ? '▼' : '▶';
    });
  });

  listEl.querySelectorAll('.history-load-btn:not([disabled])').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sid = btn.dataset.id;
      if (!sid) return;
      _closeHistoryModal();
      window.ws && window.ws.send({ type: 'load_session', session_id: sid });
    });
  });

  listEl.querySelectorAll('.history-del-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const sid = btn.dataset.id;
      if (!sid) return;
      // ★ 2026-05-18 修复：使用自定义确认弹窗替代 confirm()，
      //   避免移动端 WebView 中 confirm() 行为不可靠。
      _showDeleteConfirm(sid);
    });
  });

  listEl.querySelectorAll('.history-session-row:not(.current)').forEach(row => {
    row.addEventListener('click', () => {
      const sid = row.dataset.id;
      if (!sid) return;
      _closeHistoryModal();
      window.ws && window.ws.send({ type: 'load_session', session_id: sid });
    });
  });

  // ── 标题点击重命名（双击标题进入编辑模式） ──
  listEl.querySelectorAll('.history-session-title').forEach(titleEl => {
    titleEl.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      const sid = titleEl.dataset.sessionId;
      if (!sid) return;
      const oldTitle = titleEl.textContent;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'history-title-input';
      input.value = oldTitle;
      input.style.cssText = 'width:100%;background:rgba(255,255,255,0.06);border:1px solid rgba(100,181,246,0.4);border-radius:4px;color:var(--text);font-size:13px;padding:2px 6px;outline:none;';
      titleEl.textContent = '';
      titleEl.appendChild(input);
      input.focus();
      input.select();

      function _finishEdit(save) {
        const newTitle = input.value.trim();
        if (save && newTitle && newTitle !== oldTitle) {
          window.ws && window.ws.send({ type: 'rename_session', title: newTitle, session_id: sid });
          titleEl.textContent = newTitle;
        } else {
          titleEl.textContent = oldTitle;
        }
      }

      input.addEventListener('blur', () => _finishEdit(true));
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); input.blur(); }
        if (ev.key === 'Escape') { ev.preventDefault(); _finishEdit(false); }
      });
    });
  });
}

/**
 * 绑定历史会话弹窗事件（在 DOMContentLoaded 中调用）
 */
function _bindHistoryEvents() {
  const historyBtn = document.getElementById('history-btn');
  if (historyBtn) historyBtn.addEventListener('click', _openHistoryModal);
  const historyCloseBtn = document.getElementById('history-close');
  if (historyCloseBtn) historyCloseBtn.addEventListener('click', _closeHistoryModal);
  const historyOverlay = document.getElementById('history-overlay');
  if (historyOverlay) historyOverlay.addEventListener('click', (e) => { if (e.target === historyOverlay) _closeHistoryModal(); });
}

/* ═══════════════════════════════════════════════════════════════
   导出到全局
   ═══════════════════════════════════════════════════════════════ */
Object.assign(window, {
  _openHistoryModal,
  _closeHistoryModal,
  _renderHistoryList,
  _bindHistoryEvents,
});
