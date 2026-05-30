/* ═══════════════════════════════════════════════════════════════
   settings.js — 设置管理器（主题切换 + 打字机速度 + 通知 + 字体大小）
   提供统一配置读写接口和设置面板 UI。
   依赖: 无（纯工具模块，在 index.html 中 <script> 加载）
   ═══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  /* ── 配置存储 Key ─────────────────────────────────────── */
  var STORAGE_KEY = 'chat_webui_settings';

  /* ── 默认配置 ──────────────────────────────────────────── */
  var DEFAULTS = {
    theme: 'dark',          // 'dark' | 'light'
    typingSpeed: 'normal',  // 'instant' | 'fast' | 'normal' | 'slow'
    fontSize: 'medium',     // 'small' | 'medium' | 'large'
  };

  /* ── 打字机速度映射（字符间隔 ms） ────────────────────── */
  var SPEED_MAP = {
    instant: 0,
    fast: 8,
    normal: 20,
    slow: 45,
  };
  var SPEED_LABELS = {
    instant: '即时',
    fast: '快速',
    normal: '标准',
    slow: '慢速',
  };

  /* ── 字体大小映射 ─────────────────────────────────────── */
  var FONT_SIZE_MAP = {
    small: { base: '13px', bubble: '12px', code: '11px' },
    medium: { base: '14px', bubble: '14px', code: '13px' },
    large: { base: '16px', bubble: '15px', code: '14px' },
  };
  var FONT_SIZE_LABELS = {
    small: '小',
    medium: '中',
    large: '大',
  };

  /* ── 当前配置（惰性加载） ── */
  var _config = null;

  /** 从 localStorage 加载配置 */
  function _load() {
    if (_config) return _config;
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        var parsed = JSON.parse(saved);
        _config = Object.assign({}, DEFAULTS, parsed);
      } else {
        _config = Object.assign({}, DEFAULTS);
      }
    } catch (_) {
      _config = Object.assign({}, DEFAULTS);
    }
    return _config;
  }

  /** 保存配置到 localStorage */
  function _save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(_config));
    } catch (_) { /* 存储满时静默失败 */ }
  }

  /** 获取配置值 */
  function get(key) {
    _load();
    return _config[key];
  }

  /** 设置配置值，触发对应回调 */
  function set(key, value) {
    _load();
    var old = _config[key];
    if (old === value) return;
    _config[key] = value;
    _save();

    // 触发对应配置的生效逻辑
    switch (key) {
      case 'theme':
        _applyTheme(value);
        break;
      case 'typingSpeed':
        _applyTypingSpeed(value);
        break;
      case 'fontSize':
        _applyFontSize(value);
        break;
    }

    // 触发自定义事件，让其他模块可以监听配置变更
    var evt = new CustomEvent('settings:changed', {
      detail: { key: key, value: value, oldValue: old }
    });
    document.dispatchEvent(evt);
  }

  /** 批量设置 */
  function setBatch(updates) {
    for (var key in updates) {
      if (updates.hasOwnProperty(key) && DEFAULTS.hasOwnProperty(key)) {
        set(key, updates[key]);
      }
    }
  }

  /** 重置为默认值 */
  function reset() {
    _config = Object.assign({}, DEFAULTS);
    _save();
    _applyTheme(_config.theme);
    _applyTypingSpeed(_config.typingSpeed);
    _applyFontSize(_config.fontSize);
  }

  /* ═══════════════════════════════════════════════════════════
     配置生效逻辑
     ═══════════════════════════════════════════════════════════ */

  /** 应用主题 */
  function _applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    document.body.setAttribute('data-theme', theme);
    // 同步更新浏览器地址栏/状态栏颜色（PWA）
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme === 'light' ? '#f5f5f5' : '#1a1a2e');
    }
    // 切换 highlight.js 主题
    // highlight.js 已全局加载，主题通过 CSS 文件控制
    // 目前使用 atom-one-dark，亮色模式保留（可接受）
  }

  /** 应用打字机速度 */
  function _applyTypingSpeed(speed) {
    var ms = SPEED_MAP[speed] || SPEED_MAP.normal;
    window.__typingSpeed = ms;
  }

  /** 应用字体大小 */
  function _applyFontSize(size) {
    var map = FONT_SIZE_MAP[size] || FONT_SIZE_MAP.medium;
    document.documentElement.style.setProperty('--font-size-base', map.base);
    document.documentElement.style.setProperty('--font-size-bubble', map.bubble);
    document.documentElement.style.setProperty('--font-size-code', map.code);
  }

  /* ═══════════════════════════════════════════════════════════
     设置面板 UI
     ═══════════════════════════════════════════════════════════ */

  /** 打开设置面板 */
  function openPanel() {
    _load();
    _renderPanel();
    var overlay = document.getElementById('settings-overlay');
    if (overlay) overlay.classList.remove('hidden');
  }

  /** 关闭设置面板 */
  function closePanel() {
    var overlay = document.getElementById('settings-overlay');
    if (overlay) overlay.classList.add('hidden');
  }

  /** 渲染设置面板内容 */
  function _renderPanel() {
    var dialog = document.getElementById('settings-dialog');
    if (!dialog) return;

    var html = '';
    // ── 主题 ──
    html += '<div class="settings-group">';
    html += '  <div class="settings-label">主题</div>';
    html += '  <div class="settings-options" data-key="theme">';
    html += _radioGroup('theme', { dark: '深色', light: '亮色' }, _config.theme);
    html += '  </div>';
    html += '</div>';

    // ── 打字机速度 ──
    html += '<div class="settings-group">';
    html += '  <div class="settings-label">打字机速度</div>';
    html += '  <div class="settings-options" data-key="typingSpeed">';
    html += _radioGroup('typingSpeed', SPEED_LABELS, _config.typingSpeed);
    html += '  </div>';
    html += '</div>';

    // ── 字体大小 ──
    html += '<div class="settings-group">';
    html += '  <div class="settings-label">字体大小</div>';
    html += '  <div class="settings-options" data-key="fontSize">';
    html += _radioGroup('fontSize', FONT_SIZE_LABELS, _config.fontSize);
    html += '  </div>';
    html += '</div>';

    // ── 底部操作 ──
    html += '<div class="settings-footer">';
    html += '  <button id="settings-reset-btn" class="settings-btn settings-btn-secondary">重置默认</button>';
    html += '  <span class="settings-version">v2.2.0</span>';
    html += '  <button id="settings-close-btn-bottom" class="settings-btn settings-btn-primary">关闭</button>';
    html += '</div>';

    dialog.innerHTML = html;

    // 绑定单选按钮事件
    var groups = dialog.querySelectorAll('.settings-options[data-key]');
    for (var i = 0; i < groups.length; i++) {
      (function(group) {
        var key = group.getAttribute('data-key');
        var radios = group.querySelectorAll('input[type="radio"]');
        for (var j = 0; j < radios.length; j++) {
          radios[j].addEventListener('change', function() {
            if (this.checked) {
              set(key, this.value);
            }
          });
        }
      })(groups[i]);
    }

    // 绑定重置按钮
    var resetBtn = document.getElementById('settings-reset-btn');
    if (resetBtn) {
      resetBtn.addEventListener('click', function() {
        reset();
        _renderPanel(); // 刷新 UI
      });
    }

    // 绑定关闭按钮
    var closeBtn = document.getElementById('settings-close-btn-bottom');
    if (closeBtn) {
      closeBtn.addEventListener('click', closePanel);
    }
  }

  /** 生成单选按钮组 HTML */
  function _radioGroup(name, labelMap, current) {
    var html = '';
    for (var val in labelMap) {
      if (labelMap.hasOwnProperty(val)) {
        var checked = (val === current) ? ' checked' : '';
        html += '<label class="settings-radio">';
        html += '  <input type="radio" name="settings-' + name + '" value="' + val + '"' + checked + '>';
        html += '  <span class="settings-radio-label">' + labelMap[val] + '</span>';
        html += '</label>';
      }
    }
    return html;
  }

  /* ═══════════════════════════════════════════════════════════
     初始化
     ═══════════════════════════════════════════════════════════ */

  function init() {
    _load();
    _applyTheme(_config.theme);
    _applyTypingSpeed(_config.typingSpeed);
    _applyFontSize(_config.fontSize);

    // 主题切换按钮（快速切换，不打开面板）
    var themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', function() {
        var current = _config.theme;
        var next = (current === 'dark') ? 'light' : 'dark';
        set('theme', next);
        // 更新按钮图标（在 index.html 中通过 CSS 控制显示）
        themeBtn.setAttribute('data-theme', next);
      });
    }

    // 设置按钮（打开设置面板）
    var settingsBtn = document.getElementById('settings-btn');
    if (settingsBtn) {
      settingsBtn.addEventListener('click', openPanel);
    }

    // 设置面板关闭（点击遮罩）
    var overlay = document.getElementById('settings-overlay');
    if (overlay) {
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay) closePanel();
      });
    }
  }

  /* ── 导出到全局 ── */
  window.__settings = {
    get: get,
    set: set,
    setBatch: setBatch,
    reset: reset,
    openPanel: openPanel,
    closePanel: closePanel,
    init: init,
    SPEED_MAP: SPEED_MAP,
    SPEED_LABELS: SPEED_LABELS,
  };

  // DOMContentLoaded 时自动初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
