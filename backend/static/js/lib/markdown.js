/* Рендерер Markdown для сообщений (ТЗ 5).
 *
 * Поддержка: **полужирный**, *курсив* / _курсив_, ~~зачёркнутый~~, `инлайн-код`,
 * ```блоки кода``` с подсветкой синтаксиса, > цитаты, маркированные и нумерованные
 * списки, [ссылки](https://…) и автоссылки.
 *
 * Безопасность: весь пользовательский текст сначала экранируется, и только потом
 * в него вставляются теги из фиксированного набора; в href допускаются лишь
 * http:, https: и mailto:. Рендер происходит ПОСЛЕ расшифровки на устройстве —
 * сервер разметку не видит и не парсит.
 */
(function (root) {
  'use strict';

  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const KEYWORDS = new Set((
    'abstract as async await break case catch class const continue def default del do elif else enum export ' +
    'extends false final finally fn for from func function go if impl import in interface is lambda let loop ' +
    'match mod mut new nil none null or and not package pass private protected pub public raise return self ' +
    'static struct super switch this throw true try type typeof use var void while with yield select insert ' +
    'update delete where join into values create table print println echo'
  ).split(' '));

  const TOKEN_RE = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|--[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g;

  function highlight(code) {
    let out = '', last = 0, m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(code))) {
      out += esc(code.slice(last, m.index));
      if (m[1]) out += '<span class="tok-com">' + esc(m[1]) + '</span>';
      else if (m[2]) out += '<span class="tok-str">' + esc(m[2]) + '</span>';
      else if (m[3]) out += '<span class="tok-num">' + esc(m[3]) + '</span>';
      else if (KEYWORDS.has(m[4])) out += '<span class="tok-kw">' + esc(m[4]) + '</span>';
      else out += esc(m[4]);
      last = TOKEN_RE.lastIndex;
    }
    return out + esc(code.slice(last));
  }

  const SAFE_URL = /^(https?:\/\/|mailto:)[^\s"'<>]+$/i;

  function inline(text) {
    const slots = [];
    const stash = (html) => '\u0000' + (slots.push(html) - 1) + '\u0000';

    // 1) инлайн-код — до экранирования, содержимое не форматируется
    let s = text.replace(/`([^`\n]+)`/g, (_, code) => stash('<code>' + esc(code) + '</code>'));
    // 2) ссылки [текст](url)
    s = s.replace(/\[([^\]\n]{1,300})\]\(([^)\s]{1,2000})\)/g, (all, label, url) =>
      SAFE_URL.test(url)
        ? stash('<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer nofollow">' + format(esc(label)) + '</a>')
        : all);
    // 3) автоссылки
    s = s.replace(/\bhttps?:\/\/[^\s<>"'\u0000]+[^\s<>"'.,;:!?)\u0000]/g, (url) =>
      stash('<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer nofollow">' + esc(url) + '</a>'));
    // 4) экранирование всего остального и форматирование
    s = format(esc(s));
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => slots[Number(i)]);
  }

  function format(s) {
    return s
      .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
      .replace(/__(?=\S)([\s\S]*?\S)__/g, '<strong>$1</strong>')
      .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>')
      .replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?![_\w])/g, '$1<em>$2</em>');
  }

  function render(src) {
    const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let para = [];
    const flush = () => {
      if (para.length) out.push('<p>' + para.map(inline).join('<br>') + '</p>');
      para = [];
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(/^\s*```\s*([\w+#.-]*)\s*$/);
      if (fence) {
        flush();
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) code.push(lines[i++]);
        const lang = fence[1] ? ' data-lang="' + esc(fence[1].toLowerCase()) + '"' : '';
        out.push('<pre class="md-code"' + lang + '><code>' + highlight(code.join('\n')) + '</code></pre>');
        continue;
      }
      if (/^\s*>/.test(line)) {
        flush();
        const quote = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ''));
        i--;
        out.push('<blockquote>' + render(quote.join('\n')) + '</blockquote>');
        continue;
      }
      const ul = /^\s*[-*+]\s+(.*)$/, ol = /^\s*\d{1,9}[.)]\s+(.*)$/;
      if (ul.test(line) || ol.test(line)) {
        flush();
        const ordered = ol.test(line) && !ul.test(line);
        const re = ordered ? ol : ul;
        const items = [];
        while (i < lines.length && re.test(lines[i])) items.push(lines[i++].match(re)[1]);
        i--;
        const tag = ordered ? 'ol' : 'ul';
        out.push('<' + tag + '>' + items.map((t) => '<li>' + inline(t) + '</li>').join('') + '</' + tag + '>');
        continue;
      }
      if (!line.trim()) { flush(); continue; }
      para.push(line);
    }
    flush();
    return out.join('');
  }

  /* Текст без разметки: превью в списке чатов, поиск, цитаты. */
  function plain(src) {
    return String(src || '')
      .replace(/```[\w+#.-]*\n?([\s\S]*?)```/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/(\*\*|__|~~)(.*?)\1/g, '$2')
      .replace(/(^|\W)[*_](\S[^*_]*?)[*_](?=\W|$)/g, '$1$2')
      .replace(/^\s*>\s?/gm, '')
      .replace(/^\s*([-*+]|\d+[.)])\s+/gm, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  const hasMarkup = (s) => /(\*\*|__|~~|`|^\s*>|^\s*[-*+]\s|^\s*\d+[.)]\s|\[[^\]]+\]\(|(^|\W)[*_]\S)/m.test(String(s || ''));

  root.CX = root.CX || {};
  root.CX.md = { render, plain, highlight, hasMarkup, escape: esc };
})(typeof window !== 'undefined' ? window : globalThis);
