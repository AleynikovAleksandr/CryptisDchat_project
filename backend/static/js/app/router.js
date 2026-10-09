/* ============================================================
   Адреса экранов: /app/settings, /app/new-message, /app/chat/<id> …

   Источник истины — по-прежнему `state`: после каждой отрисовки адрес
   выводится из состояния (pathOf) и записывается в историю. Кнопки
   «назад/вперёд» и открытие ссылки работают в обратную сторону —
   адрес разбирается и применяется к состоянию (applyPath).
   ============================================================ */
const ROUTE_VIEWS = {
  settings: '/app/settings',
  autolock: '/app/settings/autolock',
  groupperm: '/app/settings/group-invites',
  blacklist: '/app/settings/blocked',
  sessions: '/app/settings/sessions',
  passcode: '/app/settings/passcode',
};
const SETTINGS_OPENERS = {
  autolock: () => H.goAutolock(),
  'group-invites': () => H.goGroupPerm(),
  blocked: () => H.goBlacklist(),
  sessions: () => H.goSessions(),
  passcode: () => H.goPasscode(),
};

const router = { armed: false, applying: false, expectPop: false };

function pathOf(s) {
  if (s.groupOpen) return '/app/new-group';
  if (s.newOpen) return '/app/new-message';
  if (ROUTE_VIEWS[s.view]) return ROUTE_VIEWS[s.view];
  if (s.activeThread && threadById(s.activeThread)) return '/app/chat/' + encodeURIComponent(s.activeThread);
  return '/app';
}

/* Вызывается из setState после каждой отрисовки. */
function routeSync() {
  if (!router.armed || router.applying || state.gate) return;
  const next = pathOf(state);
  if (next === location.pathname) return;
  const cur = history.state || {};
  if (cur.prev === next) {
    // шаг «назад» внутри приложения (закрыли настройки, вернулись к списку) — не плодим историю
    router.expectPop = true;
    history.back();
  } else {
    history.pushState({ prev: location.pathname }, '', next);
  }
}

/* Применяет адрес к состоянию: открытие ссылки, обновление страницы, «назад/вперёд». */
function applyPath(path) {
  router.applying = true;
  try {
    set({ newOpen: false, groupOpen: false, profileOpen: false, profileClosing: false, moreOpen: false,
          filterOpen: false, menuId: null, forwardOpen: false, infoOpen: false });
    const parts = path.replace(/\/+$/, '').split('/').slice(2).map(decodeURIComponent); // после /app
    if (parts[0] === 'settings') {
      H.openSettings();
      const open = SETTINGS_OPENERS[parts[1]];
      if (open) open();
    } else if (parts[0] === 'new-message') {
      set({ view: 'chat' });
      H.newChat();
    } else if (parts[0] === 'new-group') {
      set({ view: 'chat' });
      H.openGroup();
    } else if (parts[0] === 'chat' && parts[1] && threadById(parts[1])) {
      openThread(parts[1]);
    } else {
      set({ view: 'chat', activeThread: null });
    }
  } finally {
    router.applying = false;
  }
  // неизвестный адрес или чужой чат — исправляем адрес на тот, что реально показан
  const shown = pathOf(state);
  if (shown !== location.pathname) history.replaceState(history.state, '', shown);
}

/* После разблокировки (и после каждой повторной) — показать экран из адреса. */
function routeStart() {
  router.armed = true;
  applyPath(location.pathname);
}

window.addEventListener('popstate', () => {
  if (router.expectPop) { router.expectPop = false; return; }
  if (!router.armed || state.gate) return; // под PIN-экраном адрес применится после разблокировки
  applyPath(location.pathname);
});
