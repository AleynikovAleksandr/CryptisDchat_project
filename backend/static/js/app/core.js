/* CryptisDchat — ядро клиента: состояние экрана и общие помощники.
 *
 * Архитектура макета cryptis.html сохранена: одно состояние `state`, функции-сборщики
 * HTML (views.js), обработчики `H` (handlers.js) и точечное обновление DOM по data-mk
 * (engine.js). Демо-данные макета заменены данными с сервера (data.js).
 */
'use strict';

const BLUE = 'hsl(204 88% 53%)';

/* ============================================================
   Состояние
   ============================================================ */
const state = {
  /* --- интерфейс (как в макете) --- */
  filter: 'All', filterOpen: false, profileOpen: false, profileScreen: 'details',
  navDir: 'forward', searchOpen: false, view: 'chat', query: '', msgQuery: '',
  msgSearchFocus: false, newSearchFocus: false,
  draft: '', hoverId: null, menuId: null, menuPos: null, activeThread: null,
  focusField: null, attachments: [], searchFocus: false,
  newOpen: false, newQuery: '', moreOpen: false, profileClosing: false,
  groupOpen: false, groupQuery: '', groupSearchFocus: false, groupSelected: [],
  groupStep: 'pick', groupName: '', groupPhoto: '', groupPhotoFile: null, membersOpen: false,
  groupPermQuery: '', blacklistQuery: '', blacklistFocus: false,
  sidebarOpen: true, deleteMediaOpen: false,
  toast: null, toastVisible: false,
  replyDraft: null, forwardOpen: false, forwardQuery: '', forwardFocus: false, forwardMsgId: null,
  infoOpen: false, infoMsgId: null, info: null,
  editName: '', editHandle: '', profileTarget: 'thread',

  /* --- настройки аккаунта (метки интерфейса; коды API — в LABELS) --- */
  autolock: '1 minute', groupPerm: 'Everyone', retention: '30 days', markdownPreview: false,
  showOnline: true, readReceipts: true, cachedMediaBytes: 0,

  /* --- PIN: экран-«шлюз» до разблокировки и смена кода в настройках --- */
  gate: { mode: 'boot' },
  pc: { step: 'current', value: '', first: '', error: '', busy: false },

  /* --- данные с сервера --- */
  me: null,
  threads: [], threadsLoaded: false,
  messagesByThread: {}, hasMore: {}, loadingOlder: false,
  directory: [], allowlist: [], blocked: [], members: {}, sessions: [],
  typing: {}, msgSearchResults: null, connection: 'online',
};

/* в исходнике эти настройки приходили как props компонента */
const props = { compact: false, showTimestamps: true };

let prevDraft = state.draft;

function setState(patch) {
  Object.assign(state, typeof patch === 'function' ? patch(state) : patch);
  render();
  if (prevDraft !== state.draft && !state.draft) hardResetDraft();
  prevDraft = state.draft;
}
const set = (patch) => setState(patch);

/* ============================================================
   Метки интерфейса ↔ коды API
   ============================================================ */
const LABELS = {
  autolock: { '1 minute': '1m', '5 minutes': '5m', '30 minutes': '30m', '1 hour': '1h', 'Never': 'never' },
  groupPerm: { 'Everyone': 'everyone', 'No one': 'nobody', 'Invite link only': 'allowlist' },
  retention: { 'Keep all': 'keep', '30 days': '30d', '60 days': '60d', '6 months': '6m' },
  vanish: { 'None': 0, '5 minutes': 300, '1 hour': 3600, '8 hours': 28800, '1 day': 86400, '1 week': 604800, '4 weeks': 2419200 },
};
const labelOf = (map, code) => Object.keys(LABELS[map]).find((k) => LABELS[map][k] === code) || Object.keys(LABELS[map])[0];
const AUTOLOCK_MS = { '1m': 60e3, '5m': 300e3, '30m': 1800e3, '1h': 3600e3, never: 0 };

/* ============================================================
   Вспомогательное
   ============================================================ */
const formatMsgTime = (d) => d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

const esc = (v) => String(v == null ? '' : v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const initialsOf = (name) => (String(name || '?').trim()[0] || '?').toUpperCase();

function relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h';
  if (diff < 7 * 86400) return d.toLocaleDateString('en-US', { weekday: 'short' });
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

const parseTime = (iso) => new Date(iso && !iso.endsWith('Z') ? iso + 'Z' : iso);

function dayLabel(d) {
  const today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return 'Today';
  if (same(d, y)) return 'Yesterday';
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
}

const fmtBytes = (b) => (b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB');
const shortAddr = (a) => (a && a.length > 20 ? a.slice(0, 10) + '…' + a.slice(-6) : a || '');

/* аватар: размер и цвет приходят из данных, поэтому идут inline */
function avatarHtml(size, tint, font, initials, url, mk, extra) {
  const bg = url ? 'background-image:url(' + esc(url) + ');' : 'background:' + esc(tint) + ';';
  return '<div class="av' + (extra ? ' ' + extra : '') + '"' + (mk ? ' data-mk="' + mk + '"' : '') +
    ' style="width:' + size + 'px;height:' + size + 'px;font-size:' + font + 'px;' + bg + '">' +
    (url ? '' : esc(initials)) + '</div>';
}

let toastTimer = null, toastHideTimer = null;
function showToast(text) {
  if (toastTimer) clearTimeout(toastTimer);
  if (toastHideTimer) clearTimeout(toastHideTimer);
  setState({ toast: text, toastVisible: false });
  requestAnimationFrame(() => requestAnimationFrame(() => setState({ toastVisible: true })));
  toastTimer = setTimeout(() => {
    setState({ toastVisible: false });
    toastHideTimer = setTimeout(() => setState({ toast: null }), 220);
  }, 2400);
}

/* Человекочитаемое сообщение об ошибке API. */
function errorText(err) {
  const map = {
    blocked: 'Messaging is blocked between you.',
    peer_no_keys: 'This person hasn’t finished setting up encryption yet.',
    invite_not_allowed: 'This person doesn’t accept group invites.',
    username_taken: 'This username is already taken.',
    stale_epoch: 'Encryption keys changed. Please try again.',
    no_conversation_key: 'The group keys are still being set up.',
    rate_limited: 'Too many requests. Slow down a little.',
    too_large: 'The file is too large.',
    realms_unavailable: 'Key backup services are unavailable. Try again later.',
  };
  return (err && map[err.code]) || (err && err.message) || 'Something went wrong.';
}

const scrollEl = () => document.getElementById('chatScroll');
const draftEl = () => document.getElementById('dc-draft');

function wiggle(el, mine) {
  const target = mine ? -10 : 10;
  el.style.transition = 'transform 160ms ease-in-out';
  el.style.transform = 'translateX(' + target + 'px)';
  setTimeout(() => { el.style.transform = 'translateX(' + (-target * 0.5) + 'px)'; }, 170);
  setTimeout(() => { el.style.transform = 'translateX(' + (target * 0.25) + 'px)'; }, 340);
  setTimeout(() => { el.style.transform = 'translateX(0)'; }, 500);
}

function jumpToMessage(id) {
  setTimeout(() => {
    const sc = scrollEl();
    const el = sc && sc.querySelector('[data-msg-id="' + id + '"]');
    if (!el || !sc) { showToast('Message is not loaded'); return; }
    const top = el.offsetTop - sc.clientHeight / 2 + el.offsetHeight / 2;
    sc.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' });
    const bubble = el.querySelector('div:last-child') || el;
    const msg = (state.messagesByThread[state.activeThread] || []).find((x) => x.id === id);
    wiggle(bubble, msg && msg.mine);
  }, 60);
}

function hardResetDraft() {
  const apply = () => {
    const el = draftEl();
    if (!el) return;
    el.value = '';
    el.style.height = '20px';
    el.style.minHeight = '20px';
    el.style.maxHeight = '280px';
    el.style.overflowY = 'hidden';
    el.scrollTop = 0;
  };
  apply();
  requestAnimationFrame(apply);
  requestAnimationFrame(() => requestAnimationFrame(apply));
  setTimeout(apply, 60);
}

function autosizeDraft() {
  requestAnimationFrame(() => {
    const el = draftEl();
    if (!el) return;
    el.style.height = '20px';
    el.style.overflowY = 'hidden';
    const cs = getComputedStyle(el);
    const pad = cs.boxSizing === 'content-box'
      ? parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
      : 0;
    const scrollH = el.scrollHeight - pad;
    const newHeight = Math.max(20, Math.min(scrollH, 280));
    el.style.height = newHeight + 'px';
    el.style.overflowY = scrollH > 280 ? 'auto' : 'hidden';
  });
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    const sc = scrollEl();
    if (sc) sc.scrollTop = sc.scrollHeight;
  });
}

const isNearBottom = () => {
  const sc = scrollEl();
  return !sc || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120;
};

let dismissT = null;
function dismissProfile(after) {
  if (state.profileClosing) return;
  setState({ profileClosing: true, moreOpen: false });
  clearTimeout(dismissT);
  dismissT = setTimeout(() => {
    setState({ profileOpen: false, profileClosing: false });
    if (after) after();
  }, 180);
}

const threadById = (id) => state.threads.find((t) => t.id === id);

const peerOf = () =>
  threadById(state.activeThread) ||
  { name: 'No conversation', initials: '—', tint: 'hsl(200 19% 70%)' };

async function copyText(str) {
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(str);
      ok = true;
    }
  } catch (err) { /* буфер недоступен — идём запасным путём */ }
  if (!ok) {
    const ta = document.createElement('textarea');
    ta.value = str;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (err2) { /* и это может не сработать */ }
    ta.remove();
  }
}

/* Разбор имени файла на основу и расширение — как в карточках вложений макета. */
function splitName(name) {
  const dot = name.lastIndexOf('.');
  return { base: dot > 0 ? name.slice(0, dot) : name, ext: dot > 0 ? name.slice(dot) : '' };
}
