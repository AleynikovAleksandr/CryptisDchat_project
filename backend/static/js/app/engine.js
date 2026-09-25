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
  if (el.__bound) return;
  el.__bound = true;
  Object.keys(EVENTS).forEach((type) => {
    const name = el.getAttribute(EVENTS[type]);
    if (name && H[name]) el.addEventListener(type, H[name]);
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
    elSidebar.classList.toggle('is-collapsed', !state.sidebarOpen);
    if (!state.gate) patch(elSidebar, sidebarHtml());
    patch(elMain, state.gate ? gateHtml() : mainHtml());
    patch(elOverlays, overlaysHtml());
  } finally {
    rendering = false;
  }
  if (dirty) { dirty = false; render(); return; }
  if (state.gate) {
    const pin = document.getElementById('dc-gate-input');
    if (pin && document.activeElement !== pin) pin.focus();
  }
}

document.getElementById('dc-file-input').addEventListener('change', H.onFiles);
document.getElementById('dc-avatar-input').addEventListener('change', H.onAvatarFile);
document.getElementById('dc-group-photo').addEventListener('change', H.onGroupPhoto);

render();
boot();
