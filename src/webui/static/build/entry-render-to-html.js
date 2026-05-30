/**
 * entry-render-to-html.js — Markdown → HTML 渲染管线打包入口
 *
 * 纯 unified/remark/rehype 管线。
 *
 * 打包：
 *   npx esbuild build/entry-render-to-html.js \
 *     --bundle --format=esm --outfile=lib/md-engine.bundle.js \
 *     --target=es2020 --minify-syntax --minify-whitespace
 */

import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkRehype from 'remark-rehype';
import rehypeHighlight from 'rehype-highlight';
import rehypeStringify from 'rehype-stringify';

// ═══ Emoji 快捷键映射 ═══
const EMOJI_MAP = {
  ':smile:': '😄', ':laughing:': '😆', ':joy:': '😂', ':wink:': '😉',
  ':heart_eyes:': '😍', ':heart:': '❤️', ':broken_heart:': '💔',
  ':fire:': '🔥', ':rocket:': '🚀', ':star:': '⭐', ':zap:': '⚡',
  ':thumbsup:': '👍', ':thumbsdown:': '👎', ':ok_hand:': '👌',
  ':wave:': '👋', ':clap:': '👏', ':pray:': '🙏',
  ':check:': '✅', ':x:': '❌', ':warning:': '⚠️',
  ':bulb:': '💡', ':book:': '📖', ':memo:': '📝', ':computer:': '💻',
  ':bug:': '🐛', ':test:': '🧪', ':tada:': '🎉',
  ':lock:': '🔒', ':unlock:': '🔓', ':key:': '🔑',
  ':link:': '🔗', ':search:': '🔍', ':gear:': '⚙️', ':wrench:': '🔧',
  ':100:': '💯', ':muscle:': '💪', ':trophy:': '🏆',
  ':thinking:': '🤔', ':eyes:': '👀', ':robot:': '🤖',
  ':sparkles:': '✨', ':coffee:': '☕', ':tea:': '🍵',
  ':white_check_mark:': '✅', ':collision:': '💥',
  ':sweat_smile:': '😅', ':sob:': '😭',
  ':sunny:': '☀️', ':moon:': '🌙', ':rainbow:': '🌈',
  ':package:': '📦', ':page:': '📄', ':file:': '📁', ':folder:': '📂',
  ':art:': '🎨', ':gift:': '🎁', ':bell:': '🔔',
  ':speech_balloon:': '💬', ':thought_balloon:': '💭',
};

const _EMOJI_PATTERN = (function() {
  const keys = Object.keys(EMOJI_MAP).sort((a, b) => b.length - a.length);
  return new RegExp(
    keys.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'),
    'g'
  );
})();

function _replaceEmojis(text) {
  if (text.indexOf(':') === -1) return text;
  return text.replace(_EMOJI_PATTERN, match => EMOJI_MAP[match] || match);
}

function _escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── 缓存 unified 处理器 ──
let _processor = null;

function _getProcessor() {
  if (!_processor) {
    try {
      _processor = unified()
        .use(remarkParse)
        .use(remarkGfm)
        .use(remarkRehype, { allowDangerousHtml: true })
        .use(rehypeHighlight, { ignoreMissing: true })
        .use(rehypeStringify, { allowDangerousHtml: true });
    } catch (e) {
      console.error('[md-engine] 创建 unified 处理器失败:', e.message);
      throw e;
    }
  }
  return _processor;
}

/**
 * 同步 Markdown → HTML 字符串
 * @param {string} text - Markdown 文本
 * @returns {string} HTML 字符串
 */
function renderMarkdownToHtml(text) {
  if (!text) return '';

  // 快速路径
  if (text.length < 100 && !/[#*`\[>\|\-\\]/.test(text) && !text.includes(':')) {
    return _escapeHtml(text).replace(/\n/g, '<br>');
  }

  try {
    const processed = _replaceEmojis(text);
    const processor = _getProcessor();
    const result = processor.processSync(processed);
    const html = String(result);
    return html;
  } catch (e) {
    console.error('[md-engine] 渲染异常:', e.message);
    return _escapeHtml(text).replace(/\n/g, '<br>');
  }
}

export { renderMarkdownToHtml };
