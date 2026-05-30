/* ═══════════════════════════════════════════════════════════════
   handlers/gen-status.js — 生成状态弹窗
   依赖: utils/core.js (formatSpeed, _formatGenTokens, _estimateTokens)
   ═══════════════════════════════════════════════════════════════ */

/** 生成状态 */
let _genState = null; // { startTime, charCount, realTokens, inputTokens, totalTokens, timerId }

function _showGenStatus() {
  const popup = document.getElementById('gen-status-popup');
  if (!popup) return;
  if (_genState) return;

  _genState = {
    startTime: Date.now(),
    charCount: 0,
    realTokens: 0,
    inputTokens: 0,
    totalTokens: 0,
    speed: 0,
    timerId: null,
  };
  popup.classList.remove('hidden');
  _genState.timerId = setInterval(() => {
    if (!_genState) return;
    _updateGenStatus();
  }, 500);
  _updateGenStatus();
}

function _hideGenStatus() {
  const popup = document.getElementById('gen-status-popup');
  if (!popup) return;
  if (_genState) {
    if (_genState.timerId) {
      clearInterval(_genState.timerId);
    }
    _genState = null;
  }
  popup.classList.add('hidden');
}

function _updateGenStatus() {
  if (!_genState) return;
  const elapsed = (Date.now() - _genState.startTime) / 1000;
  document.getElementById('gen-elapsed').textContent = _formatElapsed(elapsed);
  let totalTokens = _genState.totalTokens;
  if (totalTokens <= 0) {
    totalTokens = _genState.realTokens > 0
      ? _genState.realTokens
      : Math.max(1, Math.ceil(_genState.charCount / 3));
  }
  document.getElementById('gen-tokens').textContent = '总 ' + _fmtTokens(totalTokens);
  const speedEl = document.getElementById('gen-speed');
  if (speedEl) {
    speedEl.textContent = _genState.speed > 0 ? formatSpeed(_genState.speed) : '';
  }
}

function _addGenChars(count) {
  if (!_genState) return;
  _genState.charCount += count;
}

function _setGenTokens(tokens) {
  if (!_genState) return;
  if (tokens > _genState.realTokens) {
    _genState.realTokens = tokens;
  }
}

function _setGenInputTokens(tokens) {
  if (!_genState) return;
  if (tokens > _genState.inputTokens) {
    _genState.inputTokens = tokens;
  }
}

function _setGenTotalTokens(tokens) {
  if (!_genState) return;
  if (tokens > _genState.totalTokens) {
    _genState.totalTokens = tokens;
  }
}

function _formatElapsed(seconds) {
  if (seconds < 60) return seconds.toFixed(1) + 's';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m + 'm' + s.toString().padStart(2, '0') + 's';
}

function _fmtTokens(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return n + 'T';
}

window._showGenStatus = _showGenStatus;
window._hideGenStatus = _hideGenStatus;
window._updateGenStatus = _updateGenStatus;
window._addGenChars = _addGenChars;
window._setGenTokens = _setGenTokens;
window._setGenInputTokens = _setGenInputTokens;
window._setGenTotalTokens = _setGenTotalTokens;
