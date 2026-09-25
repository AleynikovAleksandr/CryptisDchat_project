/* CryptisDchat — слой данных клиента.
 *
 *  • сессия: вход (handoff со страницы логина), PIN-хранилище, авто-вход по refresh-токену;
 *  • ключи: генерация, публикация, резервная копия по Шамиру и восстановление (ТЗ 6.1, 6.6);
 *  • сообщения: проверка ECDSA-подписи → расшифровка XSalsa20-Poly1305 (ТЗ 6.2);
 *  • Conversation key личных чатов через ECIES, групповые ключи через TreeKEM (ТЗ 6.3, 6.8);
 *  • события реального времени по WebSocket (ТЗ 6.4).
 */
'use strict';

const CXC = CX.crypto;
const API = CX.api;
const VAULT = CX.vault;
const PBKDF2_ITERATIONS = 310000;
const CACHE_PER_THREAD = 100;

/* ============================================================
   Сессия в памяти (открытые ключи живут только пока приложение разблокировано)
   ============================================================ */
const S = {
  userId: null,
  keys: null,          // { version, identityPkcs8, signingPkcs8, identityPub, signingPub }
  identityPriv: null,  // CryptoKey ECDH
  signingPriv: null,   // CryptoKey ECDSA
  convKeys: {},        // { threadId: { epoch: b64 } } — Conversation key личных чатов
  trees: {},           // { threadId: { privs, epochs, processed } } — дерево ключей группы
  cache: {},           // { threadId: [сообщения] } — локальная расшифрованная история (ТЗ 6.5)
  peerKeys: {},        // { "userId:version": SigningPub }
  handoff: null,       // токены, пришедшие со страницы входа
};

function vaultState() {
  return { v: 1, user_id: S.userId, refresh: API.session.refresh, keys: S.keys,
           conv: S.convKeys, trees: S.trees, cache: S.cache };
}
const persist = (immediate) => VAULT.save(vaultState(), immediate);

API.session.onRefresh = () => { persist(); CX.ws.reauth(); };
API.session.onExpired = () => sessionEnded();

async function importKeys() {
  S.identityPriv = await CXC.importEcdhPriv(S.keys.identityPkcs8);
  S.signingPriv = await CXC.importEcdsaPriv(S.keys.signingPkcs8);
}

function setGate(gate) { setState({ gate }); }

/* ============================================================
   Запуск: какой экран PIN показать
   ============================================================ */
async function boot() {
  let handoff = null;
  try { handoff = JSON.parse(sessionStorage.getItem('cx_login') || 'null'); } catch (e) { handoff = null; }
  const hasVault = await VAULT.exists();

  if (!handoff) {
    if (hasVault) setGate({ mode: 'unlock', pin: '' });
    else location.replace('/login');
    return;
  }
  S.handoff = handoff;
  API.session.access = handoff.access;
  API.session.refresh = handoff.refresh;
  try {
    const me = await API.get('/api/me');
    S.userId = me.id;
    state.me = me;
    if (hasVault && VAULT.userId() === me.id) setGate({ mode: 'unlock', pin: '' });
    else if (me.key_version == null) setGate({ mode: 'setup', pin: '' });
    else if (me.has_backup) setGate({ mode: 'restore', pin: '' });
    else setGate({ mode: 'reset', pin: '' });
  } catch (e) {
    sessionStorage.removeItem('cx_login');
    location.replace('/login?reauth=1');
  }
}

async function unlockWith(pin) {
  const st = await VAULT.unlock(pin); // wrong_pin | wiped
  S.userId = st.user_id;
  S.keys = st.keys;
  S.convKeys = st.conv || {};
  S.trees = st.trees || {};
  S.cache = st.cache || {};
  await importKeys();
  if (S.handoff) {
    API.session.access = S.handoff.access;
    API.session.refresh = S.handoff.refresh;
    S.handoff = null;
    sessionStorage.removeItem('cx_login');
    await persist(true);
  } else {
    API.session.refresh = st.refresh;
    try { await API.refresh(); } catch (e) { sessionEnded(); return; }
  }
  await startApp();
}

async function publishNewKeys() {
  const k = await CXC.generateDeviceKeys();
  const res = await API.post('/api/keys', { identity_pub: k.identityPub, signing_pub: k.signingPub });
  S.keys = { version: res.keys.version, identityPkcs8: k.identityPkcs8, signingPkcs8: k.signingPkcs8,
             identityPub: k.identityPub, signingPub: k.signingPub };
  S.convKeys = {}; S.trees = {}; S.cache = {};
  return res.refresh_groups || [];
}

/* Новый аккаунт / сброс ключей: генерация ключей устройства, PIN-хранилище, резервная копия. */
async function setupWith(pin) {
  setGate({ mode: 'busy', text: 'Generating encryption keys…' });
  const refreshGroups = await publishNewKeys();
  await VAULT.wipe();
  await VAULT.create(pin, vaultState());
  S.handoff = null;
  sessionStorage.removeItem('cx_login');
  await importKeys();
  backupKeys(pin).catch((e) => showToast('Key backup failed: ' + errorText(e)));
  await startApp();
  for (const gid of refreshGroups) rotateGroup(gid, { periodic: true });
}

/* ТЗ 6.6: ключ K шифрует пакет ключей; K делится по Шамиру 2-из-3 на realm-сервисы.
 * Каждая доля и PIN-производный ключ доступа запечатаны под ключ своего realm. */
async function backupKeys(pin) {
  const realms = await API.get('/api/keys/realms');
  const K = CXC.random(32);
  const bundle = CXC.utf8(JSON.stringify({ v: 1, user_id: S.userId, keys: S.keys }));
  const ciphertext = await CXC.aesGcmSeal(K, bundle);
  const salt = CXC.random(16);
  const pinKey = await CXC.pbkdf2(pin, salt, PBKDF2_ITERATIONS);
  const parts = CXC.shamirSplit(K, realms.length, 2);
  const shares = [];
  for (let i = 0; i < realms.length; i++) {
    const auth = await CXC.realmAuth(pinKey, realms[i].index);
    const payload = CXC.utf8(JSON.stringify({ v: 1, share: CXC.b64(parts[i]), auth: CXC.b64(auth) }));
    shares.push({ realm_index: realms[i].index, sealed: await CXC.eciesSeal(realms[i].public_key, payload) });
  }
  await API.post('/api/keys/backup', {
    ciphertext: CXC.b64(ciphertext), kdf_salt: CXC.b64(salt), kdf_iterations: PBKDF2_ITERATIONS,
    threshold: 2, shares,
  });
}

/* Новое устройство: PIN → доступ к долям на realm → K → пакет ключей. */
async function restoreWith(pin) {
  setGate({ mode: 'busy', text: 'Restoring your keys…' });
  const meta = await API.get('/api/keys/backup');
  const pinKey = await CXC.pbkdf2(pin, CXC.unb64(meta.kdf_salt), meta.kdf_iterations);
  const eph = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
  const ephPub = CXC.b64(await crypto.subtle.exportKey('spki', eph.publicKey));
  const requests = [];
  for (const r of meta.realms) {
    const auth = await CXC.realmAuth(pinKey, r.index);
    const payload = CXC.utf8(JSON.stringify({ v: 1, auth: CXC.b64(auth), client_pub: ephPub }));
    requests.push({ realm_index: r.index, sealed_request: await CXC.eciesSeal(r.public_key, payload) });
  }
  const started = await API.post('/api/keys/recovery', { requests });
  let res = started;
  for (let i = 0; i < 90 && res.status === 'pending'; i++) {
    await new Promise((ok) => setTimeout(ok, 700));
    res = await API.get('/api/keys/recovery/' + started.id);
  }
  if (res.status === 'wrong_pin') {
    setGate({ mode: 'restore', pin: '', error: 'Wrong passcode. ' + res.attempts_left + ' attempts left.' });
    return;
  }
  if (res.status === 'destroyed') {
    setGate({ mode: 'reset', pin: '', error: 'Too many wrong attempts — the backup was destroyed.' });
    return;
  }
  if (res.status !== 'done') {
    setGate({ mode: 'restore', pin: '', error: 'Recovery services are unavailable. Try again later.' });
    return;
  }
  const parts = [];
  for (const s of res.shares) {
    const opened = JSON.parse(CXC.fromUtf8(await CXC.eciesOpen(eph.privateKey, s.sealed_share)));
    parts.push(CXC.unb64(opened.share));
  }
  const K = CXC.shamirCombine(parts.slice(0, 2));
  const bundle = JSON.parse(CXC.fromUtf8(await CXC.aesGcmOpen(K, CXC.unb64(meta.ciphertext))));
  S.keys = bundle.keys;
  S.convKeys = {}; S.trees = {}; S.cache = {};
  await API.del('/api/keys/recovery/' + started.id);
  await VAULT.wipe();
  await VAULT.create(pin, vaultState());
  S.handoff = null;
  sessionStorage.removeItem('cx_login');
  await importKeys();
  await startApp();
}

/* ============================================================
   Жизненный цикл приложения
   ============================================================ */
let expireTimer = null;

async function startApp() {
  setState({ gate: null, view: 'chat' });
  await Promise.all([loadMe(), loadSettings(), loadThreads(), loadBlocked()]).catch((e) => showToast(errorText(e)));
  CX.ws.start({ onFrame: onWsFrame, onRevoked: sessionEnded, onReconnecting: () => set({ connection: 'reconnecting' }) });
  startAutolock();
  clearInterval(expireTimer);
  expireTimer = setInterval(purgeExpired, 5000);
  for (const t of state.threads) if (t.isGroup && t.rotationPending) rotateGroup(t.id);
}

function wipeMemory() {
  CX.ws.stop();
  clearInterval(expireTimer);
  VAULT.lock();
  Object.assign(S, { keys: null, identityPriv: null, signingPriv: null, convKeys: {}, trees: {}, cache: {}, peerKeys: {} });
  API.session.access = null;
  API.session.refresh = null;
  state.threads = [];
  state.messagesByThread = {};
  state.activeThread = null;
  state.profileOpen = false;
}

/* Авто-блокировка по бездействию (настройка «Lock after inactivity»). */
let lockTimer = null;
function startAutolock() {
  const reset = () => {
    clearTimeout(lockTimer);
    const ms = AUTOLOCK_MS[LABELS.autolock[state.autolock]];
    if (ms && !state.gate) lockTimer = setTimeout(lockApp, ms);
  };
  if (!startAutolock.bound) {
    ['mousemove', 'keydown', 'click', 'touchstart', 'scroll'].forEach((e) =>
      window.addEventListener(e, reset, { passive: true, capture: true }));
    startAutolock.bound = true;
  }
  startAutolock.reset = reset;
  reset();
}

async function lockApp() {
  await persist(true);
  wipeMemory();
  setState({ gate: { mode: 'unlock', pin: '' }, view: 'chat' });
}

function sessionEnded() {
  // сессию отозвали (другое устройство / админ): ключи остаются в PIN-хранилище,
  // после повторного входа кошельком их можно будет разблокировать тем же PIN
  wipeMemory();
  location.replace('/login?reauth=1');
}

async function logout(all) {
  try { await API.post(all ? '/api/auth/logout-all' : '/api/auth/logout'); } catch (e) { /* уже недействительна */ }
  wipeMemory();
  await VAULT.wipe(); // ТЗ 6.5: при выходе локальные данные удаляются безвозвратно
  sessionStorage.clear();
  location.replace('/login?reauth=1');
}

/* ============================================================
   Профиль и настройки
   ============================================================ */
async function loadMe() {
  const me = await API.get('/api/me');
  set({ me });
}

async function loadSettings() {
  const s = await API.get('/api/settings');
  set({
    autolock: labelOf('autolock', s.autolock), groupPerm: labelOf('groupPerm', s.group_invite_policy),
    retention: labelOf('retention', s.media_retention), markdownPreview: s.markdown_preview,
    showOnline: s.show_online, readReceipts: s.send_read_receipts, cachedMediaBytes: s.cached_media_bytes,
  });
  if (state.groupPerm === 'Invite link only') loadAllowlist();
}

async function patchSettings(patch) {
  try {
    await API.patch('/api/settings', patch);
    if (startAutolock.reset) startAutolock.reset();
  } catch (e) { showToast(errorText(e)); loadSettings(); }
}

const toPerson = (u) => ({
  id: u.id, name: u.display_name || 'Unnamed', handle: u.username ? '@' + u.username : '',
  initials: initialsOf(u.display_name), tint: u.avatar_tint, avatarUrl: u.avatar_url,
  ton: u.ton_address_friendly || '', online: !!u.online,
});

let contactsSeq = 0;
async function searchContacts(query) {
  const seq = ++contactsSeq;
  try {
    const list = await API.get('/api/contacts?query=' + encodeURIComponent(query || ''));
    if (seq === contactsSeq) set({ directory: list.map(toPerson) });
  } catch (e) { /* поиск не критичен */ }
}
let contactsTimer = null;
function searchContactsSoon(query) {
  clearTimeout(contactsTimer);
  contactsTimer = setTimeout(() => searchContacts(query), 220);
}

async function loadBlocked() {
  set({ blocked: (await API.get('/api/blocklist')).map(toPerson) });
}
async function loadAllowlist() {
  set({ allowlist: (await API.get('/api/settings/group-invite-allowlist')).map((u) => u.id) });
}
async function loadSessions() {
  set({ sessions: await API.get('/api/auth/sessions') });
}

/* ============================================================
   Чаты
   ============================================================ */
function toThread(t, prev) {
  const isGroup = t.kind === 'group';
  const name = isGroup ? (t.title || 'Group') : (t.peer ? t.peer.display_name : 'Unknown');
  return Object.assign({}, prev || {}, {
    id: t.id, kind: t.kind, isGroup, name,
    handle: !isGroup && t.peer && t.peer.username ? '@' + t.peer.username : '',
    ton: t.peer ? t.peer.ton_address_friendly || '' : '',
    initials: initialsOf(name), tint: t.avatar_tint, avatarUrl: t.avatar_url,
    peerId: t.peer ? t.peer.id : null, online: !!(t.peer && t.peer.online),
    muted: t.muted, unread: t.unread, time: relTime(t.last_message_at || t.created_at),
    lastMessageAt: t.last_message_at, keyEpoch: t.key_epoch, rotationPending: t.rotation_pending,
    disappearing: t.disappearing_seconds, screenshotBlock: t.screenshot_block, myRole: t.my_role,
    membersCount: t.members_count, peerLastRead: t.peer_last_read_seq, peerLastDelivered: t.peer_last_delivered_seq,
    lastMessage: t.last_message,
    prefix: (prev && prev.prefix) || '', preview: (prev && prev.preview) || (t.last_message ? '…' : 'No messages yet'),
  });
}

async function loadThreads() {
  const list = await API.get('/api/threads');
  const byId = {};
  state.threads.forEach((t) => { byId[t.id] = t; });
  set({ threads: list.map((t) => toThread(t, byId[t.id])), threadsLoaded: true });
  for (const t of state.threads) if (t.lastMessage) refreshPreview(t, t.lastMessage);
}

let threadsTimer = null;
function reloadThreadsSoon() {
  clearTimeout(threadsTimer);
  threadsTimer = setTimeout(() => loadThreads().catch(() => {}), 300);
}

const membersLoading = {};
function ensureMembers(threadId) {
  if (state.members[threadId]) return Promise.resolve();
  if (!membersLoading[threadId]) {
    membersLoading[threadId] = loadMembers(threadId).catch(() => {}).finally(() => { delete membersLoading[threadId]; });
  }
  return membersLoading[threadId];
}

async function refreshPreview(t, raw) {
  if (t.isGroup) await ensureMembers(t.id);  // имя автора в превью «Имя: текст»
  const m = await decodeMessage(t, raw);
  const tt = threadById(t.id);
  if (!tt) return;
  const who = m.mine ? 'You: ' : tt.isGroup ? (memberName(tt.id, m.senderId) || 'Someone') + ': ' : '';
  tt.prefix = who;
  tt.preview = m.file ? m.file.base + m.file.ext : (m.bad ? '🔒 Encrypted message' : CX.md.plain(m.text));
  render();
}

function memberName(threadId, userId) {
  const members = state.members[threadId];
  const m = members && members.find((x) => x.user.id === userId);
  return m ? m.user.display_name : '';
}

async function loadMembers(threadId) {
  const detail = await API.get('/api/threads/' + threadId);
  state.members[threadId] = detail.members;
  const t = threadById(threadId);
  if (t) Object.assign(t, toThread(detail, t));
  render();
  return detail;
}

async function openThread(threadId) {
  set({ activeThread: threadId, view: 'chat', searchOpen: false, msgQuery: '', replyDraft: null, menuId: null });
  const t = threadById(threadId);
  if (!t) return;
  if (!state.messagesByThread[threadId] && S.cache[threadId]) {
    // сначала — локальная зашифрованная история, затем свежая с сервера
    state.messagesByThread[threadId] = S.cache[threadId].map(reviveMessage);
    render();
    scrollToBottom();
  }
  if (t.isGroup) ensureMembers(threadId);
  try {
    await loadMessages(threadId);
  } catch (e) { showToast(errorText(e)); }
  scrollToBottom();
  markReadSoon(threadId);
}

function reviveMessage(m) {
  return Object.assign({}, m, { time: new Date(m.time) });
}

async function loadMessages(threadId, before) {
  const t = threadById(threadId);
  const page = await API.get('/api/threads/' + threadId + '/messages?limit=50' + (before ? '&before=' + before : ''));
  const decoded = [];
  for (const raw of page.items) decoded.push(await decodeMessage(t, raw));
  const existing = state.messagesByThread[threadId] || [];
  const byId = new Map();
  (before ? existing : existing.filter((m) => m.pending)).forEach((m) => byId.set(m.id, m));
  decoded.forEach((m) => byId.set(m.id, m));
  if (before) existing.forEach((m) => byId.set(m.id, m));
  const list = Array.from(byId.values()).sort((a, b) => a.seq - b.seq);
  state.messagesByThread[threadId] = list;
  state.hasMore[threadId] = page.next_before;
  cacheMessages(threadId);
  render();
}

function cacheMessages(threadId) {
  const list = (state.messagesByThread[threadId] || []).filter((m) => !m.pending && !m.bad);
  S.cache[threadId] = list.slice(-CACHE_PER_THREAD).map((m) => Object.assign({}, m, { time: m.time.getTime() }));
  persist();
}

async function loadOlder() {
  const tid = state.activeThread;
  const before = state.hasMore[tid];
  if (!tid || !before || state.loadingOlder) return;
  const sc = scrollEl();
  const prevHeight = sc ? sc.scrollHeight : 0;
  state.loadingOlder = true;
  try { await loadMessages(tid, before); } finally { state.loadingOlder = false; }
  requestAnimationFrame(() => { if (sc) sc.scrollTop = sc.scrollHeight - prevHeight; });
}

/* ============================================================
   Ключи чатов
   ============================================================ */
async function peerSigningKey(userId, version) {
  const k = userId + ':' + version;
  if (!S.peerKeys[k]) {
    if (userId === S.userId && version === S.keys.version) S.peerKeys[k] = S.keys.signingPub;
    else S.peerKeys[k] = (await API.get('/api/keys/' + userId + '?version=' + version)).signing_pub;
  }
  return S.peerKeys[k];
}

async function syncTree(threadId) {
  const tree = S.trees[threadId] || (S.trees[threadId] = { privs: {}, epochs: {}, processed: 0 });
  const commits = await API.get('/api/groups/' + threadId + '/keys/commits?since=' + (tree.processed || 0));
  for (const c of commits) {
    try { await CX.treekem.processCommit(tree, c, S.keys.identityPkcs8); } catch (e) { console.warn('commit', c.epoch, e); }
  }
  if (commits.length) persist();
  return tree;
}

/* Conversation key эпохи: личный чат — из обёрнутых ECIES ключей, группа — из дерева. */
async function convKey(t, epoch) {
  if (t.isGroup) {
    let tree = S.trees[t.id];
    if (!tree || !tree.epochs[epoch]) tree = await syncTree(t.id);
    return tree.epochs[epoch] ? CXC.unb64(tree.epochs[epoch]) : null;
  }
  const mine = S.convKeys[t.id] || (S.convKeys[t.id] = {});
  if (!mine[epoch]) {
    const list = await API.get('/api/threads/' + t.id + '/keys');
    for (const k of list) {
      if (mine[k.epoch]) continue;
      try { mine[k.epoch] = CXC.b64(await CXC.eciesOpen(S.identityPriv, k.wrapped_key)); } catch (e) { /* другой Identity-ключ */ }
    }
    persist();
  }
  return mine[epoch] ? CXC.unb64(mine[epoch]) : null;
}

/* ТЗ 6.3: новый Conversation key оборачивается ECIES для каждого участника личного чата. */
async function createDmKey(t, epoch) {
  const peer = await API.get('/api/keys/' + t.peerId);
  const key = CXC.random(32);
  const keys = [
    { recipient_id: S.userId, recipient_key_version: S.keys.version, wrapped_key: await CXC.eciesSeal(S.keys.identityPub, key) },
    { recipient_id: t.peerId, recipient_key_version: peer.version, wrapped_key: await CXC.eciesSeal(peer.identity_pub, key) },
  ];
  await API.post('/api/threads/' + t.id + '/keys', { epoch, keys });
  (S.convKeys[t.id] || (S.convKeys[t.id] = {}))[epoch] = CXC.b64(key);
  t.keyEpoch = epoch;
  persist();
  return { epoch, key };
}

/* Ключ для отправки. Несколько сообщений подряд в новом чате не должны параллельно
 * создавать одну и ту же эпоху ключа — запросы по одному чату выполняются по очереди. */
const keyQueue = {};
function sendKey(t) {
  const run = (keyQueue[t.id] || Promise.resolve()).catch(() => {}).then(() => sendKeyNow(t));
  keyQueue[t.id] = run;
  return run;
}

async function sendKeyNow(t) {
  if (t.isGroup) {
    if (!t.keyEpoch) throw new API.ApiError(409, { error: 'no_conversation_key' });
    const key = await convKey(t, t.keyEpoch);
    if (!key) throw new API.ApiError(409, { error: 'no_conversation_key' });
    return { epoch: t.keyEpoch, key };
  }
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      if (!t.keyEpoch) return await createDmKey(t, 1);
      const key = await convKey(t, t.keyEpoch);
      if (key) return { epoch: t.keyEpoch, key };
      return await createDmKey(t, t.keyEpoch + 1); // наш Identity-ключ сменился — новая эпоха
    } catch (e) {
      if (e.code !== 'stale_epoch' || attempt) throw e;
      await loadMembers(t.id);  // обновить key_epoch и повторить
    }
  }
  throw new Error('no key');
}

/* Перешифровка ветки дерева группы после подтверждённого в блокчейне изменения состава (ТЗ 6.8).
 * Коммитит инициатор изменения (preferred); остальные участники ждут и подключаются, только если
 * ротация так и не выполнена (инициатор офлайн). Гонку разрешает сервер: побеждает первый коммит эпохи. */
const rotating = {};
async function rotateGroup(threadId, opts) {
  opts = opts || {};
  if (rotating[threadId]) return;
  rotating[threadId] = true;
  try {
    const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
    if (opts.preferred || opts.periodic) await sleep(150);
    else {
      await sleep(8000 + Math.random() * 4000);
      const detail = await API.get('/api/threads/' + threadId);
      if (!detail.rotation_pending) return;
    }
    const q = opts.periodic ? '?periodic=true' : '';
    let plan;
    try { plan = await API.get('/api/groups/' + threadId + '/keys/plan' + q); } catch (e) { return; }
    const { body } = await CX.treekem.buildCommit(plan);
    try { await API.post('/api/groups/' + threadId + '/keys/commit' + q, body); } catch (e) { /* другой участник успел раньше */ }
    await syncTree(threadId);
    await loadMembers(threadId).catch(() => {});
  } catch (e) {
    console.warn('key rotation', threadId, e);
  } finally {
    rotating[threadId] = false;
  }
}

/* ============================================================
   Сообщения: проверка подписи → расшифровка
   ============================================================ */
async function decodeMessage(t, raw) {
  const time = parseTime(raw.created_at);
  const base = {
    id: raw.id, seq: raw.seq, clientId: raw.client_msg_id, senderId: raw.sender_id,
    mine: raw.sender_id === S.userId, time, meta: formatMsgTime(time), epoch: raw.key_epoch,
    attachmentId: raw.attachment_id, forwarded: !!raw.forwarded_from_id, replyToId: raw.reply_to_id,
    expiresAt: raw.expires_at ? parseTime(raw.expires_at).getTime() : null, confirmed: !!raw.confirmed,
    text: '',
  };
  try {
    // 1) ТЗ 6.2: сначала подпись — сообщение с неверной подписью отбрасывается до расшифровки
    const nonce = CXC.unb64(raw.nonce), ct = CXC.unb64(raw.ciphertext);
    const pub = await peerSigningKey(raw.sender_id, raw.sender_key_version);
    const ok = await CXC.verify(pub, CXC.unb64(raw.signature), CXC.signingBytes(t.id, raw.client_msg_id, raw.key_epoch, nonce, ct));
    if (!ok) return Object.assign(base, { bad: 'signature', text: 'Message signature is invalid — content hidden.' });
    // 2) расшифровка Conversation key эпохи
    const key = await convKey(t, raw.key_epoch);
    if (!key) return Object.assign(base, { bad: 'key', text: 'Encrypted with a key this device doesn’t have.' });
    const content = JSON.parse(CXC.fromUtf8(CXC.secretboxOpen(key, nonce, ct)));
    return Object.assign(base, contentToUi(content));
  } catch (e) {
    return Object.assign(base, { bad: 'decrypt', text: 'Unable to decrypt this message.' });
  }
}

function contentToUi(c) {
  const out = { reply: c.reply || null };
  if (c.type === 'file' && c.file) {
    const n = splitName(String(c.file.name || 'file'));
    out.file = { base: n.base, ext: n.ext, size: fmtBytes(Number(c.file.size) || 0), bytes: c.file.size,
                 mime: c.file.mime || 'application/octet-stream', key: c.file.key };
  } else {
    out.text = String(c.text || '');
  }
  return out;
}

async function sealPacket(t, content, extra) {
  const { epoch, key } = await sendKey(t);
  const clientId = (extra && extra.clientId) || CXC.uuid();
  const { nonce, ciphertext } = CXC.secretboxSeal(key, CXC.utf8(JSON.stringify(content)));
  const signature = await CXC.sign(S.signingPriv, CXC.signingBytes(t.id, clientId, epoch, nonce, ciphertext));
  const searchable = content.type === 'text' ? CX.md.plain(content.text) : (content.file ? content.file.name : '');
  return {
    client_msg_id: clientId, thread_id: t.id, key_epoch: epoch, sender_key_version: S.keys.version,
    nonce, ciphertext, signature, kind: content.type === 'file' ? 'file' : 'text',
    reply_to_id: (extra && extra.replyToId) || '', attachment_id: (extra && extra.attachmentId) || '',
    search_tokens: await CXC.wordTokens(key, searchable),
  };
}

const packetJson = (p) => ({
  client_msg_id: p.client_msg_id, key_epoch: p.key_epoch, sender_key_version: p.sender_key_version,
  nonce: CXC.b64(p.nonce), ciphertext: CXC.b64(p.ciphertext), signature: CXC.b64(p.signature), kind: p.kind,
  reply_to_id: p.reply_to_id || null, attachment_id: p.attachment_id || null, search_tokens: p.search_tokens,
});

function addLocal(threadId, msg) {
  const list = state.messagesByThread[threadId] || [];
  state.messagesByThread[threadId] = list.concat([msg]);
  render();
  scrollToBottom();
}

function replaceLocal(threadId, localId, msg) {
  const list = (state.messagesByThread[threadId] || []).filter((m) => m.id !== localId && m.id !== msg.id);
  state.messagesByThread[threadId] = list.concat([msg]).sort((a, b) => a.seq - b.seq);
  cacheMessages(threadId);
}

async function postPacket(t, content, extra, optimistic) {
  const clientId = CXC.uuid();
  const localId = 'local-' + clientId;
  const time = new Date();
  addLocal(t.id, Object.assign({ id: localId, seq: Number.MAX_SAFE_INTEGER, mine: true, pending: true, time,
                                 meta: formatMsgTime(time), senderId: S.userId, text: '' }, optimistic));
  try {
    const packet = await sealPacket(t, content, Object.assign({ clientId }, extra));
    const saved = await API.request('POST', '/api/threads/' + t.id + '/messages',
      CX.proto.encode('MessagePacket', packet), { headers: { 'Content-Type': 'application/x-protobuf' } });
    const ui = Object.assign(await decodeMessage(t, saved), contentToUi(content));
    replaceLocal(t.id, localId, ui);
    t.prefix = 'You: ';
    t.preview = ui.file ? ui.file.base + ui.file.ext : CX.md.plain(ui.text);
    t.time = 'now';
    t.lastMessageAt = saved.created_at;
    bumpThread(t.id);
  } catch (e) {
    const list = state.messagesByThread[t.id] || [];
    const m = list.find((x) => x.id === localId);
    if (m) { m.pending = false; m.failed = true; }
    showToast(errorText(e));
  }
  render();
}

function bumpThread(threadId) {
  const t = threadById(threadId);
  if (!t) return;
  state.threads = [t].concat(state.threads.filter((x) => x.id !== threadId));
}

async function sendText(threadId, text, reply) {
  const t = threadById(threadId);
  const content = { v: 1, type: 'text', text };
  if (reply) content.reply = reply;
  await postPacket(t, content, { replyToId: reply ? reply.id : '' }, { text, reply });
}

/* Файл шифруется на устройстве случайным ключом; ключ файла — внутри зашифрованного сообщения. */
async function encryptAndUpload(bytes) {
  const fileKey = CXC.random(32);
  const { nonce, ciphertext } = CXC.secretboxSeal(fileKey, bytes);
  const fd = new FormData();
  fd.append('file', new Blob([CXC.concat(nonce, ciphertext)], { type: 'application/octet-stream' }), 'blob.bin');
  fd.append('kind', 'file');
  const up = await API.post('/api/uploads', fd);
  return { id: up.id, key: CXC.b64(fileKey) };
}

async function sendFile(threadId, att, reply) {
  const t = threadById(threadId);
  const optimistic = { file: { base: att.base, ext: att.ext, size: att.size }, reply };
  const clientId = CXC.uuid();
  const localId = 'local-' + clientId;
  const time = new Date();
  addLocal(t.id, Object.assign({ id: localId, seq: Number.MAX_SAFE_INTEGER, mine: true, pending: true, time,
                                 meta: formatMsgTime(time), senderId: S.userId, text: '' }, optimistic));
  try {
    const bytes = new Uint8Array(await att.fileObj.arrayBuffer());
    const up = await encryptAndUpload(bytes);
    state.messagesByThread[t.id] = (state.messagesByThread[t.id] || []).filter((m) => m.id !== localId);
    const content = { v: 1, type: 'file', file: { name: att.base + att.ext, size: att.bytes, mime: att.mime, key: up.key } };
    if (reply) content.reply = reply;
    await postPacket(t, content, { attachmentId: up.id, replyToId: reply ? reply.id : '' }, optimistic);
  } catch (e) {
    const m = (state.messagesByThread[t.id] || []).find((x) => x.id === localId);
    if (m) { m.pending = false; m.failed = true; }
    showToast(errorText(e));
    render();
  }
}

async function downloadAttachment(msg) {
  const bytes = await API.bytes('/api/messages/' + msg.id + '/attachment');
  return CXC.secretboxOpen(CXC.unb64(msg.file.key), bytes.subarray(0, 24), bytes.subarray(24));
}

async function saveFile(msg) {
  try {
    const plain = await downloadAttachment(msg);
    const url = URL.createObjectURL(new Blob([plain], { type: msg.file.mime || 'application/octet-stream' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = msg.file.base + msg.file.ext;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (e) {
    showToast(e.code === 'attachment_deleted' ? 'This file was deleted from the server.' : errorText(e));
  }
}

/* Пересылка: содержимое перешифровывается ключом целевого чата (файл — перезаливается). */
async function forwardMessage(msg, targetId) {
  const target = threadById(targetId);
  let content, extra = {};
  if (msg.file) {
    const plain = await downloadAttachment(msg);
    const up = await encryptAndUpload(plain);
    content = { v: 1, type: 'file', file: { name: msg.file.base + msg.file.ext, size: msg.file.bytes,
                                            mime: msg.file.mime, key: up.key } };
    extra.attachmentId = up.id;
  } else {
    content = { v: 1, type: 'text', text: msg.text };
  }
  const packet = await sealPacket(target, content, extra);
  await API.post('/api/messages/' + msg.id + '/forward', { target_thread_id: targetId, packet: packetJson(packet) });
  await openThread(targetId);
}

/* Поиск: сначала по локально расшифрованным сообщениям, затем — по слепому индексу на сервере. */
let searchSeq = 0;
async function searchMessages(threadId, query) {
  const seq = ++searchSeq;
  const words = CXC.words(query);
  const t = threadById(threadId);
  if (!words.length || !t) { set({ msgSearchResults: null }); return; }
  const epochs = t.isGroup ? Object.keys((S.trees[t.id] || {}).epochs || {}) : Object.keys(S.convKeys[t.id] || {});
  const params = [];
  for (const w of words.slice(0, 8)) {
    const tokens = [];
    for (const e of epochs) {
      const key = await convKey(t, Number(e));
      if (key) tokens.push(await CXC.searchToken(await CXC.searchKey(key), w.slice(0, 12)));
    }
    if (tokens.length) params.push('q=' + tokens.join(','));
  }
  if (!params.length) return;
  try {
    const found = await API.get('/api/threads/' + threadId + '/messages/search?' + params.join('&'));
    const decoded = [];
    for (const raw of found) decoded.push(await decodeMessage(t, raw));
    if (seq === searchSeq) set({ msgSearchResults: { threadId, query, items: decoded } });
  } catch (e) { /* локальных результатов достаточно */ }
}

async function loadProof(msgId) {
  set({ info: { loading: true } });
  try {
    const proof = await API.get('/api/messages/' + msgId + '/proof');
    for (const ev of proof.events) {
      ev.verified = ev.batch ? await CXC.merkleVerify(ev.event_hash, ev.merkle_proof, ev.batch.merkle_root) : false;
    }
    if (state.infoMsgId === msgId) set({ info: proof });
  } catch (e) {
    set({ info: { error: errorText(e) } });
  }
}

/* ============================================================
   Прочтение и доставка
   ============================================================ */
let readTimer = null;
function markReadSoon(threadId) {
  clearTimeout(readTimer);
  readTimer = setTimeout(async () => {
    const t = threadById(threadId);
    if (!t || document.hidden || state.activeThread !== threadId) return;
    t.unread = 0;
    render();
    try { await API.post('/api/threads/' + threadId + '/read', {}); } catch (e) { /* повторим при следующем открытии */ }
  }, 400);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state.activeThread && !state.gate) markReadSoon(state.activeThread);
});

function purgeExpired() {
  const now = Date.now();
  let changed = false;
  for (const tid of Object.keys(state.messagesByThread)) {
    const list = state.messagesByThread[tid];
    const kept = list.filter((m) => !m.expiresAt || m.expiresAt > now);
    if (kept.length !== list.length) { state.messagesByThread[tid] = kept; changed = true; cacheMessages(tid); }
  }
  if (changed) render();
}

/* ============================================================
   События WebSocket
   ============================================================ */
function onWsFrame(frame, data) {
  if (state.connection !== 'online') set({ connection: 'online' });
  switch (frame.event) {
    case 'message.new': onIncoming(frame, data); break;
    case 'message.delivered':
    case 'message.read': {
      const t = threadById(frame.thread_id);
      if (!t || frame.sender_id === S.userId) break;
      if (frame.event === 'message.read') t.peerLastRead = Math.max(t.peerLastRead || 0, frame.seq);
      t.peerLastDelivered = Math.max(t.peerLastDelivered || 0, frame.seq);
      render();
      break;
    }
    case 'message.confirmed': {
      const list = state.messagesByThread[data.thread_id] || [];
      const ids = new Set(data.message_ids || []);
      list.forEach((m) => { if (ids.has(m.id)) m.confirmed = true; });
      render();
      break;
    }
    case 'typing.start':
    case 'typing.stop': {
      const until = frame.event === 'typing.start' ? Date.now() + 6000 : 0;
      state.typing[frame.thread_id] = { userId: data.user_id, until };
      render();
      if (until) setTimeout(render, 6100);
      break;
    }
    case 'thread.updated':
      if (data.removed_user_id === S.userId) {
        if (state.activeThread === data.thread_id) state.activeThread = null;
      }
      if (data.thread_id && state.members[data.thread_id] && data.members_changed) loadMembers(data.thread_id).catch(() => {});
      reloadThreadsSoon();
      break;
    case 'key.rotation': {
      const t = threadById(data.thread_id);
      if (data.phase === 'requested') rotateGroup(data.thread_id, { preferred: data.actor_id === S.userId });
      else if (t) {
        t.keyEpoch = Math.max(t.keyEpoch || 0, data.epoch || 0);
        t.rotationPending = false;
        if (t.isGroup) syncTree(t.id).then(render).catch(() => {});
      }
      reloadThreadsSoon();
      break;
    }
    case 'session.revoked':
      sessionEnded();
      break;
    default:
      break;
  }
}

async function onIncoming(frame, data) {
  const p = frame.packet || {};
  const raw = {
    id: frame.message_id, thread_id: frame.thread_id, seq: frame.seq, sender_id: frame.sender_id,
    client_msg_id: p.client_msg_id, kind: p.kind, key_epoch: p.key_epoch, sender_key_version: p.sender_key_version,
    nonce: CXC.b64(p.nonce), ciphertext: CXC.b64(p.ciphertext), signature: CXC.b64(p.signature),
    reply_to_id: p.reply_to_id || null, attachment_id: p.attachment_id || null,
    forwarded_from_id: p.forwarded_from_id || null, created_at: data.created_at, expires_at: data.expires_at,
  };
  let t = threadById(raw.thread_id);
  let fresh = false;  // чат только что подгружен с сервера — его счётчик уже учитывает это сообщение
  if (!t) { await loadThreads(); t = threadById(raw.thread_id); if (!t) return; fresh = true; }
  if (raw.sender_id !== S.userId) CX.ws.send('ack.delivered', { thread_id: raw.thread_id, seq: raw.seq });

  const list = state.messagesByThread[raw.thread_id];
  const already = list && list.some((m) => m.id === raw.id || (m.clientId === raw.client_msg_id && !m.pending));
  if (already) return;
  if (t.isGroup) await ensureMembers(t.id);
  const msg = await decodeMessage(t, raw);
  if (list) {
    const stick = isNearBottom();
    state.messagesByThread[raw.thread_id] = list.filter((m) => m.clientId !== raw.client_msg_id)
      .concat([msg]).sort((a, b) => a.seq - b.seq);
    cacheMessages(raw.thread_id);
    if (stick) scrollToBottom();
  }
  t.prefix = msg.mine ? 'You: ' : t.isGroup ? (memberName(t.id, msg.senderId) || 'Someone') + ': ' : '';
  t.preview = msg.file ? msg.file.base + msg.file.ext : msg.bad ? '🔒 Encrypted message' : CX.md.plain(msg.text);
  t.time = 'now';
  t.lastMessageAt = data.created_at;
  if (!msg.mine) {
    if (state.activeThread === t.id && !document.hidden) markReadSoon(t.id);
    else if (!fresh) t.unread = (t.unread || 0) + 1;
    state.typing[t.id] = { until: 0 };
  }
  bumpThread(t.id);
  render();
}

/* Индикатор «печатает…»: не чаще раза в 3 секунды. */
let typingSentAt = 0;
function notifyTyping(threadId, active) {
  if (!threadId) return;
  const now = Date.now();
  if (active && now - typingSentAt < 3000) return;
  typingSentAt = active ? now : 0;
  CX.ws.send(active ? 'typing.start' : 'typing.stop', { thread_id: threadId });
}
