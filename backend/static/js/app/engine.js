/* ============================================================
   Привязка событий

   Обработчик вешается на узел один раз: при повторной отрисовке
   узел переиспользуется, поэтому помечаем его флагом.
   ============================================================ */
const EVENTS = {
  click: 'data-click', input: 'data-input', keydown: 'data-keydown',
  focus: 'data-focus', blur: 'data-blur',
  mouseenter: 'data-mouseenter', mouseleave: 'data-mouseleave', scroll: 'data-scroll',
};
const SELECTOR = '[data-click],[data-input],[data-keydown],[data-focus],[data-blur],[data-mouseenter],[data-mouseleave],[data-scroll]';

function bindOne(el) {
  const bound = el.__boundHandlers || (el.__boundHandlers = {});
  Object.keys(EVENTS).forEach((type) => {
    const name = el.getAttribute(EVENTS[type]);
    const handler = name && H[name] ? H[name] : null;
    if (bound[type] === handler) return;
    if (bound[type]) el.removeEventListener(type, bound[type]);
    if (handler) el.addEventListener(type, handler);
    bound[type] = handler;
  });
}
function bindEvents(root) {
  if (root.nodeType === 1 && root.matches(SELECTOR)) bindOne(root);
  root.querySelectorAll(SELECTOR).forEach(bindOne);
}

/* ============================================================
   Обновление DOM на месте

   Замена innerHTML целиком пересоздавала бы все узлы: заново
   проигрывались бы анимации, срывались бы CSS-переходы, поле
   ввода теряло бы фокус и каретку, а высота композера
   сбрасывалась бы при каждом нажатии клавиши. Поэтому новая
   разметка сверяется с текущей по атрибуту data-mk: совпавшие
   узлы остаются на месте, у них правятся только атрибуты и текст.
   ============================================================ */
const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute('data-mk') : null);
const sameKind = (a, b) => a.nodeType === b.nodeType && (a.nodeType !== 1 || a.nodeName === b.nodeName);

function syncAttrs(from, to) {
  Array.from(to.attributes).forEach((a) => {
    // высоту композера считает autosizeDraft — не перетираем её разметкой
    if (a.name === 'style' && from.id === 'dc-draft') return;
    if (from.getAttribute(a.name) !== a.value) from.setAttribute(a.name, a.value);
  });
  Array.from(from.attributes).forEach((a) => {
    if (a.name === 'style' && from.id === 'dc-draft') return;
    if (!to.hasAttribute(a.name)) from.removeAttribute(a.name);
  });

  // значение поля живёт в свойстве; поле в фокусе не трогаем — слетит каретка
  if (from.nodeName === 'INPUT') {
    const v = to.getAttribute('value');
    if (v !== null && from !== document.activeElement && from.value !== v) from.value = v;
  }
  if ('disabled' in from) from.disabled = to.hasAttribute('disabled');
}

function morph(from, to) {
  if (from.nodeType === 3) {
    if (from.nodeValue !== to.nodeValue) from.nodeValue = to.nodeValue;
    return;
  }
  syncAttrs(from, to);
  if (from.nodeName !== 'TEXTAREA') morphChildren(from, to);
}

function morphChildren(parent, source) {
  const byKey = new Map();
  Array.from(parent.childNodes).forEach((n) => { const k = keyOf(n); if (k) byKey.set(k, n); });

  let cursor = parent.firstChild;

  Array.from(source.childNodes).forEach((fresh) => {
    const k = keyOf(fresh);
    let match = null;

    if (k) {
      const candidate = byKey.get(k);
      if (candidate && sameKind(candidate, fresh)) match = candidate;
    } else if (cursor && !keyOf(cursor) && sameKind(cursor, fresh)) {
      match = cursor;
    }

    if (!match) {
      parent.insertBefore(fresh, cursor);   // новый узел — только он и анимируется
      return;
    }
    if (match === cursor) cursor = cursor.nextSibling;
    else parent.insertBefore(match, cursor);
    morph(match, fresh);
  });

  while (cursor) {                          // всё несовпавшее удаляем
    const next = cursor.nextSibling;
    parent.removeChild(cursor);
    cursor = next;
  }
}

const scratch = document.createElement('div');
function patch(container, html) {
  scratch.innerHTML = html;
  morphChildren(container, scratch);
  bindEvents(container);
}

/* ============================================================
   Отрисовка
   ============================================================ */
const elSidebar = document.getElementById('sidebar');
const elMain = document.getElementById('main');
const elOverlays = document.getElementById('overlays');
const elApp = document.querySelector('.app');
const elSidebarResizer = document.getElementById('sidebar-resizer');
let chatImageObserver = null;

function observeChatImages() {
  const placeholders = elMain.querySelectorAll('.chat-image-placeholder[data-image-id]');
  if (!placeholders.length) {
    if (chatImageObserver) chatImageObserver.disconnect();
    return;
  }
  if (!('IntersectionObserver' in window)) {
    placeholders.forEach((el) => loadImagePreview(el.getAttribute('data-image-id')));
    return;
  }
  if (chatImageObserver) chatImageObserver.disconnect();
  const root = document.getElementById('chatScroll');
  if (!root) return;
  chatImageObserver = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      loadImagePreview(entry.target.getAttribute('data-image-id'));
    });
  }, { root, rootMargin: '320px 0px' });
  placeholders.forEach((el) => chatImageObserver.observe(el));
}

/* Удаление поля в фокусе заставляет браузер синхронно послать blur,
   его обработчик вызывает setState — и отрисовка запускается заново
   прямо внутри незакончившегося обхода DOM. Поэтому вложенный вызов
   только помечает состояние грязным, а перерисовка идёт после. */
let rendering = false, dirty = false;

function render() {
  if (rendering) { dirty = true; return; }
  rendering = true;
  try {
    // пока приложение заблокировано (PIN) — только экран ввода кода, без списка чатов
    elSidebar.hidden = !!state.gate;
    elSidebarResizer.hidden = !!state.gate;
    elSidebar.classList.toggle('is-collapsed', !sidebarExpanded());
    elApp.style.setProperty('--sidebar-width', state.sidebarWidth + 'px');
    elApp.style.setProperty('--sidebar-resizer-left', (sidebarExpanded() ? state.sidebarWidth : 76) + 'px');
    elApp.classList.toggle('show-main', showsMain());
    if (!state.gate) patch(elSidebar, sidebarHtml());
    patch(elMain, state.gate ? gateHtml() : mainHtml());
    observeChatImages();
    patch(elOverlays, overlaysHtml());
  } finally {
    flushHiddenText();
    rendering = false;
  }
  if (dirty) { dirty = false; render(); return; }
  if (state.gate) {
    const pin = document.getElementById('dc-gate-input');
    if (pin && document.activeElement !== pin) pin.focus();
  }
}

let resizingSidebar = false, resizeStartX = 0, resizeStartWidth = 0, resizeWidth = initialSidebarWidth;
let resizeCollapsed = false;
elSidebarResizer.addEventListener('pointerdown', (e) => {
  if (isMobile() || state.gate) return;
  e.preventDefault();
  resizingSidebar = true;
  resizeStartX = e.clientX;
  resizeStartWidth = sidebarExpanded() ? state.sidebarWidth : 76;
  resizeWidth = state.sidebarWidth;
  resizeCollapsed = !sidebarExpanded();
  elSidebarResizer.setPointerCapture(e.pointerId);
  document.querySelector('.app-row').classList.add('is-resizing');
});
elSidebarResizer.addEventListener('pointermove', (e) => {
  if (!resizingSidebar) return;
  const rawWidth = resizeStartWidth + e.clientX - resizeStartX;
  const collapsed = rawWidth < 170;
  const width = collapsed ? 76 : Math.max(240, Math.min(520, rawWidth));
  resizeWidth = collapsed ? state.sidebarWidth : width;
  resizeCollapsed = collapsed;
  elApp.style.setProperty('--sidebar-width', width + 'px');
  elApp.style.setProperty('--sidebar-resizer-left', width + 'px');
  const wasCollapsed = elSidebar.classList.contains('is-collapsed');
  elSidebar.classList.toggle('is-collapsed', collapsed);
  if (wasCollapsed !== collapsed) {
    state.sidebarOpen = !collapsed;
    if (!collapsed) state.sidebarWidth = width;
    render();
  }
});
function finishSidebarResize(e) {
  if (!resizingSidebar) return;
  resizingSidebar = false;
  if (elSidebarResizer.hasPointerCapture(e.pointerId)) elSidebarResizer.releasePointerCapture(e.pointerId);
  document.querySelector('.app-row').classList.remove('is-resizing');
  state.sidebarWidth = resizeWidth;
  state.sidebarOpen = !resizeCollapsed;
  localStorage.setItem('cx-sidebar-width', String(resizeWidth));
  render();
}
elSidebarResizer.addEventListener('pointerup', finishSidebarResize);
elSidebarResizer.addEventListener('pointercancel', finishSidebarResize);
elSidebarResizer.addEventListener('keydown', (e) => {
  if (isMobile() || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
  e.preventDefault();
  const width = Math.max(240, Math.min(520, state.sidebarWidth + (e.key === 'ArrowRight' ? 24 : -24)));
  state.sidebarWidth = width;
  state.sidebarOpen = true;
  localStorage.setItem('cx-sidebar-width', String(width));
  render();
});

bindLongPress(elMain);

window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.emojiPicker) {
    e.preventDefault();
    H.closeEmojiPicker();
  }
});

let fileDragDepth = 0;
function hasDraggedFiles(e) {
  const transfer = e.dataTransfer;
  return !!(transfer && (
    Array.from(transfer.types || []).includes('Files') ||
    Array.from(transfer.items || []).some((item) => item.kind === 'file')
  ));
}
function setChatDropActive(active) {
  fileDragDepth = active ? Math.max(fileDragDepth, 1) : 0;
  if (state.fileDropActive !== active) setState({ fileDropActive: active });
}
elMain.addEventListener('dragenter', (e) => {
  if (!hasDraggedFiles(e) || !elMain.querySelector('.chat')) return;
  e.preventDefault();
  fileDragDepth += 1;
  if (!state.fileDropActive) setState({ fileDropActive: true });
});
elMain.addEventListener('dragover', (e) => {
  if (!hasDraggedFiles(e) || !elMain.querySelector('.chat')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'copy';
});
elMain.addEventListener('dragleave', (e) => {
  if (!hasDraggedFiles(e) || !state.fileDropActive) return;
  fileDragDepth = Math.max(0, fileDragDepth - 1);
  if (!fileDragDepth) setChatDropActive(false);
});
elMain.addEventListener('drop', (e) => {
  if (!hasDraggedFiles(e)) return;
  e.preventDefault();
  const files = e.dataTransfer.files;
  setChatDropActive(false);
  H.addFiles(files);
});
window.addEventListener('dragend', () => { if (state.fileDropActive) setChatDropActive(false); });
window.addEventListener('dragover', (e) => { if (hasDraggedFiles(e)) e.preventDefault(); });
window.addEventListener('drop', (e) => { if (hasDraggedFiles(e)) e.preventDefault(); });

/* Экранная клавиатура: iOS не уменьшает 100dvh, а сдвигает страницу. На телефоне высота
 * приложения берётся из visualViewport, чтобы композер оставался прямо над клавиатурой. */
const vv = window.visualViewport;
function syncViewport() {
  if (!vv || !isMobile()) {
    document.documentElement.style.removeProperty('--app-h');
    document.documentElement.classList.remove('kb-open');
    return;
  }
  document.documentElement.style.setProperty('--app-h', Math.round(vv.height) + 'px');
  // клавиатура открыта — отступ под полосу «домой» не нужен, композер прижимается к клавиатуре
  document.documentElement.classList.toggle('kb-open', vv.height < window.innerHeight - 120);
  if (window.scrollY) window.scrollTo(0, 0);
}
if (vv) { vv.addEventListener('resize', syncViewport); vv.addEventListener('scroll', syncViewport); }
syncViewport();
// переход через границу 768px (поворот, изменение окна) меняет раскладку
MOBILE_MQ.addEventListener('change', () => { syncViewport(); render(); });

document.getElementById('dc-file-input').addEventListener('change', H.onFiles);
document.getElementById('dc-avatar-input').addEventListener('change', H.onAvatarFile);
document.getElementById('dc-group-photo').addEventListener('change', H.onGroupPhoto);

render();
boot();
