/* ═══════════════════════════════════════════════════════════════
   handlers/editmsg.js — 编辑消息弹窗（/editmsg 功能）
   依赖: ws-client.js (window.ws), utils/core.js (escapeHtml)
   ═══════════════════════════════════════════════════════════════ */

/** 编辑消息弹窗状态 */
let _editMsgState = {
  messages: [],
  selectedIdx: -1,
};

function _onEditMsgKeydown(e) {
  if (e.key === 'Escape') _closeEditMsgModal();
}

function _openEditMsgModal() {
  const overlay = document.getElementById('editmsg-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  _editMsgState.messages = [];
  _editMsgState.selectedIdx = -1;
  document.getElementById('editmsg-actions').classList.add('hidden');
  document.getElementById('editmsg-list').innerHTML = '<div class="editmsg-loading">加载中...</div>';
  document.addEventListener('keydown', _onEditMsgKeydown);
  window.ws.send({ type: 'get_messages' });
}

function _closeEditMsgModal() {
  const overlay = document.getElementById('editmsg-overlay');
  if (overlay) overlay.classList.add('hidden');
  _editMsgState.selectedIdx = -1;
  document.querySelectorAll('.editmsg-msg-row.selected').forEach(r => r.classList.remove('selected'));
  document.removeEventListener('keydown', _onEditMsgKeydown);
}

function _renderEditMsgList(messages) {
  const listEl = document.getElementById('editmsg-list');
  const userMessages = (messages || []).filter(m => m.role === 'user');
  if (userMessages.length === 0) {
    listEl.innerHTML = '<div class="editmsg-empty">📝 暂无用户消息</div>';
    return;
  }
  // ★ Bug 4 修复：显示连续编号（1/2/3…），data_index 保留在 data 属性中用于后端
  let html = '';
  let displayIdx = 0;
  for (const msg of userMessages) {
    displayIdx++;
    const realIdx = msg.data_index;
    const content = msg.content || '';
    const preview = content.replace(/\n/g, ' ').substring(0, 80);
    const display = preview.length >= 80 ? preview + '…' : preview;
    html += '<div class="editmsg-msg-row" data-index="' + realIdx + '" data-role="user">'
      + '<span class="editmsg-msg-index" title="原始索引 #' + realIdx + '">#' + displayIdx + '</span>'
      + '<span class="editmsg-msg-role user">用户</span>'
      + '<span class="editmsg-msg-content">' + escapeHtml(display || '(空消息)') + '</span>'
      + '</div>';
  }
  listEl.innerHTML = html;
  listEl.querySelectorAll('.editmsg-msg-row').forEach(row => {
    row.addEventListener('click', function() {
      listEl.querySelectorAll('.editmsg-msg-row.selected').forEach(r => r.classList.remove('selected'));
      this.classList.add('selected');
      const idx = parseInt(this.dataset.index);
      _editMsgState.selectedIdx = idx;
      const msg = _editMsgState.messages.find(m => m.data_index === idx);
      if (msg) {
        const actionsEl = document.getElementById('editmsg-actions');
        const infoEl = document.getElementById('editmsg-selected-info');
        const content = (msg.content || '').replace(/\n/g, ' ').substring(0, 60);
        infoEl.innerHTML = '<span class="sel-label">已选择: </span>'
          + '<span class="sel-preview">' + escapeHtml(content || '(空)') + '</span>';
        actionsEl.classList.remove('hidden');
      }
    });
  });
}

function _doEditMsgAction(action) {
  const idx = _editMsgState.selectedIdx;
  if (idx < 0) return;
  // ★ Bug 3 修复：先发送 WS 消息再关弹窗，发送失败时弹窗保持打开
  const sent = window.ws.send({ type: 'edit_messages_action', action: action, data_index: idx });
  if (!sent) {
    console.error('[editmsg] 发送编辑请求失败：WebSocket 未连接');
    // 弹窗不关闭，停留在编辑界面让用户重试
    return;
  }
  _closeEditMsgModal();
}

window._editMsgState = _editMsgState;
window._openEditMsgModal = _openEditMsgModal;
window._closeEditMsgModal = _closeEditMsgModal;
window._renderEditMsgList = _renderEditMsgList;
window._doEditMsgAction = _doEditMsgAction;

// ── 编辑消息角标计数（类似文件沙盒） ──
let _editMsgCount = 0;

function _updateEditMsgBadge() {
  const badge = document.getElementById('editmsg-badge');
  if (!badge) return;
  if (_editMsgCount <= 0) {
    badge.style.display = 'none';
    badge.textContent = '';
  } else {
    const display = _editMsgCount > 99 ? '99+' : String(_editMsgCount);
    badge.textContent = display;
    // 确保父按钮是定位参考点（内联样式，不受 CSS 缓存影响）
    const btn = document.getElementById('editmsg-btn');
    if (btn) btn.style.position = 'relative';
    // 所有样式内联，不依赖 CSS 文件
    badge.style.cssText = 'position:absolute;top:-5px;right:-5px;min-width:18px;height:18px;' +
      'padding:0 5px;border-radius:9px;background:#f44336;color:#fff;' +
      'font-size:11px;font-weight:700;line-height:18px;text-align:center;' +
      'display:block;box-shadow:0 1px 3px rgba(0,0,0,0.3);' +
      'pointer-events:none;user-select:none;';
  }
}

/** 新用户消息到来时 +1 */
function _incEditMsgCount() {
  _editMsgCount += 1;
  _updateEditMsgBadge();
}

/** 从后端获取精确消息数后更新 */
function _setEditMsgCount(count) {
  _editMsgCount = count;
  _updateEditMsgBadge();
}

/** 请求后端消息列表刷新角标计数（不打开弹窗） */
function _refreshEditMsgCount() {
  window.ws.send({ type: 'get_messages' });
}

window._incEditMsgCount = _incEditMsgCount;
window._setEditMsgCount = _setEditMsgCount;
window._updateEditMsgBadge = _updateEditMsgBadge;
window._refreshEditMsgCount = _refreshEditMsgCount;
