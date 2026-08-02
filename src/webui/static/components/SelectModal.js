/* ═══════════════════════════════════════════════════════════════
   SelectModal — 选择弹窗 Preact 组件
   纯展示组件（Dumb Component），通过 props 接收数据
   支持单选/多选，复用 style.css 中 select-overlay / select-dialog 等 CSS 类
   ═══════════════════════════════════════════════════════════════ */
import { h } from '../lib/preact.module.js';
import htm from '../lib/htm.module.js';
import { useState, useEffect } from '../lib/hooks.module.js';

const html = htm.bind(h);

/**
 * SelectModal — 选择弹窗组件
 *
 * Props:
 *   visible         - 是否显示
 *   title           - 弹窗标题
 *   options         - 选项列表（Array<string>）
 *   multiSelect     - 是否多选（true=checkbox, false=radio）
 *   defaultOptions  - 默认选中项（Array<string>）
 *   timeout         - 超时秒数（由父组件处理倒计时逻辑）
 *   onConfirm       - 确认回调 (selected: string[], action: string) => void
 *   onCancel        - 取消回调 () => void
 */
export function SelectModal(props) {
  const {
    visible = false,
    title = '',
    options = [],
    optionDescriptions = [],
    multiSelect = false,
    defaultOptions = [],
    /* timeout 由父组件处理倒计时逻辑，此处仅透传 */
    onConfirm,
    onCancel,
  } = props;

  // 内部状态：当前选中的选项列表
  const [selected, setSelected] = useState([]);

  // 当弹窗打开时，用 defaultOptions 初始化选中状态
  useEffect(() => {
    if (visible) {
      setSelected(defaultOptions || []);
    }
  }, [visible]);

  // ── 选项变更处理 ──────────────────────────────────────────
  const handleChange = (opt) => {
    if (multiSelect) {
      // 多选：切换选中/取消
      setSelected((prev) =>
        prev.includes(opt)
          ? prev.filter((item) => item !== opt)
          : [...prev, opt],
      );
    } else {
      // 单选：直接替换为当前选项
      setSelected([opt]);
    }
  };

  // ── 确认 ──────────────────────────────────────────────────
  const handleConfirm = () => {
    if (onConfirm) {
      onConfirm(selected, 'confirmed');
    }
  };

  // ── 取消 ──────────────────────────────────────────────────
  const handleCancel = () => {
    if (onCancel) {
      onCancel();
    }
  };

  // ── 遮罩层点击（仅当点击 overlay 本身时触发取消） ─────────
  const handleOverlayClick = (e) => {
    if (e.target === e.currentTarget) {
      handleCancel();
    }
  };

  // ── 模板 ──────────────────────────────────────────────────
  const overlayClass = 'select-overlay' + (visible ? '' : ' hidden');

  return html`
    <div class="${overlayClass}" onClick=${handleOverlayClick}>
      <div class="select-dialog">
        <div class="select-title">📋 ${title}</div>
        <div class="select-options">
          ${options.map(
            (opt, i) => html`
              <label>
                <input
                  type=${multiSelect ? 'checkbox' : 'radio'}
                  name="modal-select"
                  value=${opt}
                  checked=${selected.includes(opt)}
                  onChange=${() => handleChange(opt)}
                />
                <span class="select-option-label">${opt}</span>
                ${optionDescriptions && optionDescriptions[i] ? html`<span class="select-option-desc">${optionDescriptions[i]}</span>` : ''}
              </label>
            `,
          )}
        </div>
        <div class="select-buttons">
          <button class="btn-confirm" onClick=${handleConfirm}>确认</button>
          <button class="btn-cancel" onClick=${handleCancel}>取消</button>
        </div>
      </div>
    </div>
  `;
}
