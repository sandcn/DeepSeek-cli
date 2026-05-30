/* ═══════════════════════════════════════════════════════════════
   tool-renderers/web-diff.js — LCS 逐行 Diff 渲染（Web 风格）
   依赖: utils/core.js (escapeHtml)
   ═══════════════════════════════════════════════════════════════ */

/**
 * 计算 LCS（最长公共子序列）表
 */
function _lcsTable(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
  for (let i = 1; i <= m; i++) {
    const ai = a[i - 1];
    const dp_i = dp[i], dp_im1 = dp[i - 1];
    for (let j = 1; j <= n; j++) {
      dp_i[j] = (ai === b[j - 1])
        ? dp_im1[j - 1] + 1
        : Math.max(dp_im1[j], dp_i[j - 1]);
    }
  }
  return dp;
}

/**
 * 从 LCS 表回溯生成 diff 操作序列
 */
function _lcsBacktrack(a, b, dp) {
  const result = [];
  let i = a.length, j = b.length;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      result.push({ type: 'equal', text: a[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.push({ type: 'add', text: b[j - 1] });
      j--;
    } else {
      result.push({ type: 'del', text: a[i - 1] });
      i--;
    }
  }
  result.reverse();
  return result;
}

/**
 * 将类型相邻的 diff 操作合并为块（上下文环绕）
 */
function _groupDiffOps(ops, contextLines) {
  contextLines = contextLines || 3;
  const rawGroups = [];
  let cur = null;
  for (const op of ops) {
    if (!cur || cur.type !== op.type) {
      cur = { type: op.type, lines: [op.text] };
      rawGroups.push(cur);
    } else {
      cur.lines.push(op.text);
    }
  }
  const result = [];
  for (let i = 0; i < rawGroups.length; i++) {
    const g = rawGroups[i];
    if (g.type === 'equal') {
      if (g.lines.length > contextLines * 2 + 1) {
        const prefix = g.lines.slice(0, contextLines);
        const suffix = g.lines.slice(-contextLines);
        result.push({ type: 'equal', lines: prefix });
        result.push({ type: 'fold', lines: ['…', g.lines.length - contextLines * 2] });
        result.push({ type: 'equal', lines: suffix });
      } else {
        result.push(g);
      }
    } else {
      result.push(g);
    }
  }
  return result;
}

/**
 * 渲染 web diff（LCS 逐行 diff，类似 GitHub 风格）
 */
function renderWebDiff(container, diffData) {
  if (!diffData) return;
  const { path: filePath, mode: modeDesc, old_content: oldText, new_content: newText, result } = diffData;

  const header = document.createElement('div');
  header.className = 'webfile-header';
  header.textContent = `${filePath} ${modeDesc}`;
  container.appendChild(header);

  if (oldText === newText) {
    const noChange = document.createElement('div');
    noChange.className = 'webdiff-no-change';
    noChange.textContent = '(无变化)';
    container.appendChild(noChange);
    if (result) {
      const statusLine = document.createElement('div');
      statusLine.className = 'webdiff-status-line';
      statusLine.textContent = result;
      container.appendChild(statusLine);
    }
    return;
  }

  const oldLines = (oldText || '').split('\n');
  const newLines = (newText || '').split('\n');
  if (oldLines.length > 0 && oldLines[oldLines.length - 1] === '') oldLines.pop();
  if (newLines.length > 0 && newLines[newLines.length - 1] === '') newLines.pop();

  const dp = _lcsTable(oldLines, newLines);
  const ops = _lcsBacktrack(oldLines, newLines, dp);
  const groups = _groupDiffOps(ops, 3);

  const view = document.createElement('div');
  view.className = 'webdiff-view';
  let oldLineNum = 1, newLineNum = 1;

  for (const group of groups) {
    if (group.type === 'fold') {
      const lineDiv = document.createElement('div');
      lineDiv.className = 'webdiff-line webdiff-fold';
      const badge = document.createElement('span'); badge.className = 'webdiff-badge'; badge.textContent = '…'; lineDiv.appendChild(badge);
      const num = document.createElement('span'); num.className = 'webdiff-num'; num.textContent = '···'; lineDiv.appendChild(num);
      const sep = document.createElement('span'); sep.className = 'webdiff-sep'; sep.textContent = ' '; lineDiv.appendChild(sep);
      const num2 = document.createElement('span'); num2.className = 'webdiff-num'; num2.textContent = '···'; lineDiv.appendChild(num2);
      const text = document.createElement('span'); text.className = 'webdiff-text'; text.textContent = '… 省略 ' + (group.lines[1] || '') + ' 行 …'; lineDiv.appendChild(text);
      view.appendChild(lineDiv);
      continue;
    }
    for (const line of group.lines) {
      const lineDiv = document.createElement('div');
      lineDiv.className = 'webdiff-line';
      if (group.type === 'equal') {
        lineDiv.classList.add('webdiff-ctx');
        const badge = document.createElement('span'); badge.className = 'webdiff-badge'; badge.textContent = ' '; lineDiv.appendChild(badge);
        const num = document.createElement('span'); num.className = 'webdiff-num'; num.textContent = oldLineNum; lineDiv.appendChild(num);
        const sep = document.createElement('span'); sep.className = 'webdiff-sep'; sep.textContent = '│'; lineDiv.appendChild(sep);
        const num2 = document.createElement('span'); num2.className = 'webdiff-num'; num2.textContent = newLineNum; lineDiv.appendChild(num2);
        const text = document.createElement('span'); text.className = 'webdiff-text'; text.textContent = line; lineDiv.appendChild(text);
        oldLineNum++; newLineNum++;
      } else if (group.type === 'del') {
        lineDiv.classList.add('webdiff-del');
        const badge = document.createElement('span'); badge.className = 'webdiff-badge'; badge.textContent = '-'; lineDiv.appendChild(badge);
        const num = document.createElement('span'); num.className = 'webdiff-num'; num.textContent = oldLineNum; lineDiv.appendChild(num);
        const sep = document.createElement('span'); sep.className = 'webdiff-sep'; sep.textContent = '│'; lineDiv.appendChild(sep);
        const num2 = document.createElement('span'); num2.className = 'webdiff-num'; num2.textContent = ''; lineDiv.appendChild(num2);
        const text = document.createElement('span'); text.className = 'webdiff-text'; text.textContent = line; lineDiv.appendChild(text);
        oldLineNum++;
      } else if (group.type === 'add') {
        lineDiv.classList.add('webdiff-add');
        const badge = document.createElement('span'); badge.className = 'webdiff-badge'; badge.textContent = '+'; lineDiv.appendChild(badge);
        const num = document.createElement('span'); num.className = 'webdiff-num'; num.textContent = ''; lineDiv.appendChild(num);
        const sep = document.createElement('span'); sep.className = 'webdiff-sep'; sep.textContent = '│'; lineDiv.appendChild(sep);
        const num2 = document.createElement('span'); num2.className = 'webdiff-num'; num2.textContent = newLineNum; lineDiv.appendChild(num2);
        const text = document.createElement('span'); text.className = 'webdiff-text'; text.textContent = line; lineDiv.appendChild(text);
        newLineNum++;
      }
      view.appendChild(lineDiv);
    }
  }
  container.appendChild(view);
  if (result) {
    const statusLine = document.createElement('div');
    statusLine.className = 'webdiff-status-line';
    statusLine.textContent = result;
    container.appendChild(statusLine);
  }
}

window.renderWebDiff = renderWebDiff;
