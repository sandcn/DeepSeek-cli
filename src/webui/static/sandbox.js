/* ═══════════════════════════════════════════════════════════════
   文件沙盒 — 角标计数 + 文件列表弹窗 + Diff 查看
   依赖: ws-client.js (ws 实例), utils.js, bubble.js (addBubble), tool-renderer.js (renderAnsiDiff)
   ═══════════════════════════════════════════════════════════════ */

let _sandboxFileCount = 0;
let _sandboxBadgeHidden = false;  // true = 用户已点击查看，强制隐藏角标直到有新变更

/** 更新标题栏沙盒按钮的角标（badge） */
function _updateSandboxBadge() {
  const badge = document.getElementById('sandbox-badge');
  if (!badge) return;
  if (_sandboxFileCount <= 0) {
    badge.classList.add('hidden');
    badge.style.display = 'none';
    badge.textContent = '';
  } else {
    const display = _sandboxFileCount > 99 ? '99+' : String(_sandboxFileCount);
    badge.textContent = display;
    badge.classList.remove('hidden');
    badge.style.display = 'block';
  }
}

/** 请求沙盒文件列表（打开弹窗时调用，获取完整文件详情） */
function _requestSandboxFiles() {
  if (!ws.send({ type: 'get_sandbox_files' })) {
    console.warn('[sandbox] WebSocket 未连接，无法发送请求');
  }
}

let _currentSandboxRecordId = null;

function showSandboxFileDiff(recordId) {
  _currentSandboxRecordId = recordId;
  const diffEl = document.getElementById('sandbox-diff');
  const contentEl = document.getElementById('sandbox-diff-content');
  const filepathEl = document.querySelector('.sandbox-diff-filepath');
  const changetypeEl = document.querySelector('.sandbox-diff-changetype');
  if (!diffEl || !contentEl) return;

  diffEl.classList.remove('hidden');
  contentEl.innerHTML = '<div class="sandbox-diff-loading">加载中...</div>';
  filepathEl.textContent = '';
  changetypeEl.textContent = '';

  if (!ws.send({ type: 'get_sandbox_file_diff', record_id: recordId })) {
    console.warn('[sandbox] WebSocket 未连接，无法发送 diff 请求');
  }
}

// ── 监听沙盒文件列表响应：更新角标计数 ──
ws.on('sandbox_files', (data) => {
  const count = (data && data.files) ? data.files.length : 0;
  _sandboxFileCount = count;
  _updateSandboxBadge();

  // 弹窗渲染逻辑仅在有打开的 overlay 时执行（保持兼容）
  const overlay = document.getElementById('sandbox-overlay');
  if (!overlay || overlay.classList.contains('hidden')) return;

  const listEl = document.getElementById('sandbox-list');
  const countEl = document.getElementById('sandbox-count');
  listEl.innerHTML = '';

  const diffEl = document.getElementById('sandbox-diff');
  if (diffEl) diffEl.classList.add('hidden');

  if (count === 0) {
    listEl.innerHTML = '<div class="sandbox-empty">暂无文件变更记录</div>';
    countEl.textContent = '';
    return;
  }

  countEl.textContent = '(' + count + '条)';

  // 使用后端返回的 groups 进行分组显示（按 parent_user_index 从大到小）
  const groups = (data.groups || []).slice().sort((a, b) => b.parent_user_index - a.parent_user_index);

  for (const g of groups) {
    const pid = g.parent_user_index;
    const groupEl = document.createElement('div');
    groupEl.className = 'sandbox-group';

    const groupHeader = document.createElement('div');
    groupHeader.className = 'sandbox-group-header';
    if (pid < 0) {
      groupHeader.textContent = '📋 未关联用户消息 (' + g.file_count + '条)';
    } else {
      const preview = (g.user_preview || '').replace(/\n/g, ' ');
      const displayPreview = preview.length > 50 ? preview.substring(0, 50) + '…' : preview;
      groupHeader.textContent = '👤 #' + pid + ' ' + displayPreview;
    }
    groupEl.appendChild(groupHeader);

    if (g.summary) {
      const summaryEl = document.createElement('div');
      summaryEl.className = 'sandbox-summary';
      summaryEl.textContent = g.summary;
      groupEl.appendChild(summaryEl);
    }

    const groupFiles = data.files.filter(f => f.parent_user_index === pid);
    for (const f of groupFiles) {
      const row = document.createElement('div');
      row.className = 'sandbox-file-row sandbox-type-' + f.change_type;
      row.dataset.recordId = String(f.record_id);
      row.addEventListener('click', () => {
        document.querySelectorAll('.sandbox-file-row.selected').forEach(el => el.classList.remove('selected'));
        row.classList.add('selected');
        showSandboxFileDiff(f.record_id);
      });

      const changeTypeEl = document.createElement('span');
      changeTypeEl.className = 'sandbox-change-type';
      const typeMap = {
        '新建文件': '🆕 新建',
        '删除文件': '🗑️ 删除',
        '修改文件': '✏️ 修改',
        '无变化': '➖ 无变化',
      };
      changeTypeEl.textContent = typeMap[f.change_type] || f.change_type;
      row.appendChild(changeTypeEl);

      const pathEl = document.createElement('span');
      pathEl.className = 'sandbox-file-path';
      pathEl.textContent = f.file_path;
      pathEl.title = f.file_path;
      row.appendChild(pathEl);

      const toolEl = document.createElement('span');
      toolEl.className = 'sandbox-tool-name';
      toolEl.textContent = f.tool_name;
      row.appendChild(toolEl);

      groupEl.appendChild(row);
    }
    listEl.appendChild(groupEl);
  }
});

// ── 监听沙盒文件 diff 响应 ──
ws.on('sandbox_file_diff', (data) => {
  if (!data || !data.file_path) return;
  const contentEl = document.getElementById('sandbox-diff-content');
  const filepathEl = document.querySelector('.sandbox-diff-filepath');
  const changetypeEl = document.querySelector('.sandbox-diff-changetype');
  if (!contentEl) return;

  filepathEl.textContent = data.file_path;
  const typeMap = {
    '新建文件': { text: '🆕 新建', color: '#4caf50' },
    '删除文件': { text: '🗑️ 删除', color: '#f44336' },
    '修改文件': { text: '✏️ 修改', color: '#ff9800' },
    '无变化': { text: '➖ 无变化', color: '#8892a4' },
  };
  const tt = typeMap[data.change_type] || { text: data.change_type, color: '#8892a4' };
  changetypeEl.textContent = tt.text;
  changetypeEl.style.color = tt.color;

  contentEl.innerHTML = '';
  if (data.diff_text) {
    renderAnsiDiff(contentEl, data.diff_text);
    const dv = contentEl.querySelector('.diff-view');
    if (dv) {
      dv.style.margin = '0';
      dv.style.border = 'none';
      dv.style.borderRadius = '0';
    }
  } else {
    contentEl.innerHTML = '<div class="sandbox-diff-loading">无 diff 内容</div>';
  }
});

// ── 监听 sandbox_updated（由服务器在初始化 + 文件修改工具完成时推送） ──
ws.on('sandbox_updated', (data) => {
  _sandboxBadgeHidden = false;
  _sandboxFileCount = (data && typeof data.count === 'number') ? data.count : 0;
  _updateSandboxBadge();
});

// ── DOMContentLoaded 事件绑定 ──
document.addEventListener('DOMContentLoaded', () => {
  // 沙盒按钮：点击 → 打开文件列表弹窗 + 清除角标计数
  const btn = document.getElementById('sandbox-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      const overlay = document.getElementById('sandbox-overlay');
      if (overlay) overlay.classList.remove('hidden');
      if (!ws.send({ type: 'get_sandbox_files' })) {
        console.warn('[sandbox] WebSocket 未连接，无法发送请求');
      }
    });
  }

  // 弹窗关闭
  const closeBtn = document.getElementById('sandbox-close');
  const overlay = document.getElementById('sandbox-overlay');
  if (closeBtn && overlay) {
    closeBtn.addEventListener('click', () => {
      overlay.classList.add('hidden');
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        overlay.classList.add('hidden');
      }
    });
  }

  // 初始沙盒计数由服务器在 session_initialized 后推送 sandbox_updated，无需主动请求
});
