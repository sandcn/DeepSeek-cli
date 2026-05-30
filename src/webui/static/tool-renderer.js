/* ═══════════════════════════════════════════════════════════════
   tool-renderer.js — 工具输出特殊渲染器
   依赖: utils/core.js (escapeHtml), 全局 hljs
   ── 已拆分出子模块 ──
   tool-renderers/web-diff.js — LCS 逐行 Diff 渲染（renderWebDiff）
   ═══════════════════════════════════════════════════════════════ */

/* ─── 语法高亮 + 行号渲染（通用） ────────────────────────── */

function renderCodeWithLines(container, codeContent, lang, startLine) {
  startLine = startLine || 1;
  const lines = codeContent.split('\n');
  if (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  const lineCount = lines.length;
  const digits = Math.max(String(lineCount + startLine - 1).length, 2);

  let highlighted;
  if (lang && typeof hljs !== 'undefined' && hljs.getLanguage(lang)) {
    try {
      highlighted = hljs.highlight(codeContent, { language: lang, ignoreIllegals: true }).value;
    } catch (_) {
      highlighted = escapeHtml(codeContent);
    }
  } else {
    highlighted = escapeHtml(codeContent);
  }

  const hlLines = highlighted.split('\n');
  if (hlLines.length > 0 && hlLines[hlLines.length - 1] === '') {
    hlLines.pop();
  }

  let numsHtml = '';
  for (let i = 0; i < lineCount; i++) {
    const lineNum = startLine + i;
    numsHtml += String(lineNum).padStart(digits) + '\n';
  }

  let codeHtml = '';
  for (let i = 0; i < hlLines.length; i++) {
    codeHtml += hlLines[i] + '\n';
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'code-block-with-lines';
  const numsDiv = document.createElement('div');
  numsDiv.className = 'code-line-nums';
  numsDiv.textContent = numsHtml;
  const codeDiv = document.createElement('div');
  codeDiv.className = 'code-content';
  codeDiv.innerHTML = codeHtml;
  wrapper.appendChild(numsDiv);
  wrapper.appendChild(codeDiv);
  container.appendChild(wrapper);
}

/* ─── read_file 渲染 ───────────────────────────────────────── */

function renderReadFileOutput(container, text) {
  const firstNewline = text.indexOf('\n');
  let filePath = '';
  let codeContent = text;
  let startLine = 1;

  if (firstNewline > 0) {
    const headerLine = text.substring(0, firstNewline);
    filePath = headerLine.replace(/^文件:\s*/, '').trim();
    codeContent = text.substring(firstNewline + 1);
    const rangeMatch = filePath.match(/\s*\(L(\d+)(?:-(\d+)|\+)\)\s*$/);
    if (rangeMatch) {
      startLine = parseInt(rangeMatch[1], 10);
      filePath = filePath.replace(/\s*\(L\d+(?:-\d+|\+)\)\s*$/, '').trim();
    }
  }

  let lang = '';
  if (filePath) {
    const ext = filePath.split('.').pop().toLowerCase();
    if (ext && ext !== filePath) lang = ext;
  }

  if (filePath) {
    const info = document.createElement('div');
    info.className = 'readfile-path';
    info.textContent = '📄 ' + filePath;
    container.appendChild(info);
  }

  renderCodeWithLines(container, codeContent, lang, startLine);
}

/* ─── ANSI 转 HTML（逃逸快速路径 + 单次扫描）───────────── */

function ansiToHtml(text) {
  if (!text) return '';

  // ★ 快速路径：不含 \x1b 转义码 → 直接 HTML escape，跳过正则
  //   大部分 cmd 输出（grep/find/cat/git status 等）不含 ANSI 颜色码，
  //   此检查比正则匹配快 ~10x。
  if (text.indexOf('\x1b') === -1) {
    return escapeHtml(text);
  }

  // ★ 慢速路径：单次正则遍历替代 20+ 次 String.replace
  //   \x1b\[([0-9;]*)m  → SGR 颜色序列（捕获数字代码）
  //   \x1b\[[0-?]*[ -/]*[@-~] → 其他 CSI 序列（光标移动等）→ 剥离
  const clsMap = {
    '0': '',  // reset → 产出 </span>
    '1': 'ansi-bold', '2': 'ansi-dim', '3': 'ansi-italic', '4': 'ansi-underline',
    '30': 'ansi-black', '31': 'ansi-red', '32': 'ansi-green', '33': 'ansi-yellow',
    '34': 'ansi-blue', '35': 'ansi-magenta', '36': 'ansi-cyan', '37': 'ansi-white',
    '90': 'ansi-gray',
    '91': 'ansi-bright-red', '92': 'ansi-bright-green', '93': 'ansi-bright-yellow',
    '94': 'ansi-bright-blue', '95': 'ansi-bright-magenta', '96': 'ansi-bright-cyan',
    '01;31': 'ansi-bold ansi-red', '01;32': 'ansi-bold ansi-green',
    '01;33': 'ansi-bold ansi-yellow', '01;34': 'ansi-bold ansi-blue',
    '01;35': 'ansi-bold ansi-magenta', '01;36': 'ansi-bold ansi-cyan',
    '01;37': 'ansi-bold ansi-white',
    '41': 'ansi-bg-red', '42': 'ansi-bg-green', '49': 'ansi-bg-off',
  };

  return escapeHtml(text).replace(
    /\x1b\[([0-9;]*)m|\x1b\[[0-?]*[ -/]*[@-~]/g,
    (match, code) => {
      if (code === undefined) return '';  // 非 SGR CSI → 剥离
      if (code === '0') return '</span>';
      const cls = clsMap[code];
      return cls ? `<span class="${cls}">` : '';
    }
  );
}

/* ─── 从 ANSI diff 文本中解析文件信息 ─────────────────────── */

function _parseFileInfoFromDiff(text) {
  const lines = text.split('\n');
  if (lines.length > 0 && lines[0].startsWith('📄')) {
    const header = lines[0].replace(/^📄\s*/, '');
    let filePath = header.replace(/\s+(字符串替换|覆盖写入整个文件|追加内容)$/, '').trim();
    let lang = '';
    const ext = filePath.split('.').pop().toLowerCase();
    if (ext && ext !== filePath && ext.length < 10) lang = ext;
    return { filePath, lang };
  }
  return { filePath: '', lang: '' };
}

/* ─── ANSI diff 渲染 ────────────────────────────────────────── */

function renderAnsiDiff(container, text) {
  let fileContent = null;
  let diffText = text;
  const fileMarkerIdx = text.lastIndexOf('\n╌FILE╌\n');
  if (fileMarkerIdx !== -1) {
    diffText = text.substring(0, fileMarkerIdx);
    fileContent = text.substring(fileMarkerIdx + '\n╌FILE╌\n'.length);
  }

  const lines = diffText.split('\n');
  const wrapper = document.createElement('div');
  wrapper.className = 'diff-view';

  let lineIdx = 0;
  if (lines.length > 0 && lines[0].startsWith('📄')) {
    const h = document.createElement('div');
    h.className = 'diff-file-header';
    h.textContent = lines[0].replace(/^📄\s*/, '');
    wrapper.appendChild(h);
    lineIdx = 1;
  }

  for (; lineIdx < lines.length; lineIdx++) {
    const raw = lines[lineIdx];
    const trimmed = raw.trim();
    if (!trimmed) continue;
    if (trimmed === '(无变化)') {
      const d = document.createElement('div');
      d.className = 'diff-no-change';
      d.textContent = '(无变化)';
      wrapper.appendChild(d);
      continue;
    }
    if (!/\x1b\[/.test(raw)) {
      const s = document.createElement('div');
      s.className = 'diff-status-line';
      s.textContent = raw;
      wrapper.appendChild(s);
      continue;
    }

    const html = ansiToHtml(raw);
    const lineDiv = document.createElement('div');
    if (raw.includes('\x1b[31m')) lineDiv.className = 'diff-line del';
    else if (raw.includes('\x1b[32m')) lineDiv.className = 'diff-line add';
    else if (raw.includes('\x1b[2m') || raw.includes('\x1b[90m')) lineDiv.className = 'diff-line ctx';
    else lineDiv.className = 'diff-line ctx';

    if (trimmed.startsWith('@@')) {
      lineDiv.className = 'diff-hunk-header';
      const clean = trimmed.replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, '');
      lineDiv.textContent = clean;
      wrapper.appendChild(lineDiv);
      continue;
    }
    if (trimmed === '...') {
      lineDiv.className = 'diff-line ctx';
      lineDiv.innerHTML = ansiToHtml(raw);
      wrapper.appendChild(lineDiv);
      continue;
    }
    lineDiv.innerHTML = html;
    wrapper.appendChild(lineDiv);
  }

  container.appendChild(wrapper);

  if (fileContent !== null) {
    const info = _parseFileInfoFromDiff(text);
    const title = document.createElement('div');
    title.className = 'diff-file-header';
    title.style.cssText = 'border-top: 1px solid var(--tool-border); margin-top: 8px;';
    title.textContent = '📄 ' + (info.filePath || '文件内容');
    container.appendChild(title);
    renderCodeWithLines(container, fileContent, info.lang, 1);
  }
}

window.ansiToHtml = ansiToHtml;
window.renderCodeWithLines = renderCodeWithLines;
window.renderReadFileOutput = renderReadFileOutput;
window.renderAnsiDiff = renderAnsiDiff;
