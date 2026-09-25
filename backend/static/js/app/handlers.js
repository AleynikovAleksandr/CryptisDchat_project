/* Обработчики действий интерфейса (data-click / data-input / …).
 * Набор и поведение — как в макете cryptis.html, но вместо изменения демо-состояния
 * каждое действие идёт в API (data.js). */
'use strict';

const H = {};

/* ---------- экран PIN («шлюз»): разблокировка, создание, восстановление ---------- */
H.onGatePin = (e) => {
  const pin = (e.target.value || '').replace(/\D/g, '').slice(0, 4);
  e.target.value = pin;
  const g = state.gate;
  if (!g || g.busy) return;
  setState({ gate: Object.assign({}, g, { pin, error: pin ? '' : g.error }) });
  if (pin.length === 4) setTimeout(() => submitGate(pin), 120);
};
H.focusGate = () => {
  const el = document.getElementById('dc-gate-input');
  if (!el) return;
  if (!state.gate || !state.gate.pin) el.value = '';
  el.focus();
};
H.gateReset = () => setState({ gate: { mode: 'setup', pin: '', reset: true } });
H.gateOtherAccount = async () => { await VAULT.wipe(); sessionStorage.clear(); location.replace('/login?reauth=1'); };

async function submitGate(pin) {
  const g = state.gate;
  const fail = (error, mode) => setState({ gate: { mode: mode || g.mode, pin: '', error } });
  try {
    if (g.mode === 'unlock') {
      setState({ gate: Object.assign({}, g, { busy: true }) });
      try { await unlockWith(pin); } catch (err) {
        if (err.message === 'wiped') { location.replace('/login?reauth=1'); return; }
        fail('Wrong passcode. ' + VAULT.attemptsLeft() + ' attempts left before this device is wiped.');
      }
    } else if (g.mode === 'setup') {
      setState({ gate: { mode: 'setup-confirm', pin: '', first: pin, reset: g.reset } });
    } else if (g.mode === 'setup-confirm') {
      if (pin !== g.first) { fail('Passcodes don’t match. Try again.', 'setup'); return; }
      await setupWith(pin);
    } else if (g.mode === 'restore') {
      await restoreWith(pin);
    }
  } catch (err) {
    fail(errorText(err), g.mode === 'setup-confirm' ? 'setup' : g.mode);
  }
  setTimeout(H.focusGate, 50);
}

/* --- боковая панель --- */
H.toggleSidebar = () => set({ sidebarOpen: !state.sidebarOpen });
H.toggleFilter = () => set({ filterOpen: !state.filterOpen });
H.pickFilter = (e) => set({ filter: e.currentTarget.getAttribute('data-val'), filterOpen: false });
H.openSettings = () => { set({ view: 'settings', filterOpen: false }); loadSettings().catch(() => {}); };
H.closeSettings = () => set({ view: 'chat' });
H.markAllRead = async () => {
  set({ filterOpen: false });
  const unread = state.threads.filter((t) => t.unread > 0);
  unread.forEach((t) => { t.unread = 0; });
  render();
  await Promise.all(unread.map((t) => API.post('/api/threads/' + t.id + '/read', {}).catch(() => {})));
};
H.newChat = () => { set({ filterOpen: false, newOpen: true, newQuery: '' }); searchContacts(''); };

H.focusSearch = () => {
  const wasOpen = state.sidebarOpen;
  if (!wasOpen) set({ sidebarOpen: true });
  setTimeout(() => {
    const el = document.getElementById('dc-chat-search');
    if (el) el.focus();
  }, wasOpen ? 0 : 230);
};
H.onQuery = (e) => set({ query: e.target.value });
H.onSearchFocus = () => set({ searchFocus: true });
H.onSearchBlur = () => set({ searchFocus: false });
H.clearQuery = () => set({ query: '' });
H.pickThread = (e) => openThread(e.currentTarget.getAttribute('data-id'));

/* --- настройки --- */
H.goAutolock = () => set({ view: 'autolock' });
H.closeAutolock = () => set({ view: 'settings' });
H.goGroupPerm = () => { set({ view: 'groupperm' }); if (state.groupPerm === 'Invite link only') { loadAllowlist(); searchContacts(''); } };
H.closeGroupPerm = () => set({ view: 'settings' });
H.goBlacklist = () => { set({ view: 'blacklist', blacklistQuery: '' }); loadBlocked().catch(() => {}); };
H.closeBlacklist = () => set({ view: 'settings' });
H.goSessions = () => { set({ view: 'sessions' }); loadSessions().catch((e) => showToast(errorText(e))); };
H.closeSessions = () => set({ view: 'settings' });
H.pickAutolock = (e) => {
  const label = e.currentTarget.getAttribute('data-val');
  set({ autolock: label, view: 'settings' });
  patchSettings({ autolock: LABELS.autolock[label] });
};
H.pickGroupPerm = (e) => {
  const label = e.currentTarget.getAttribute('data-val');
  set({ groupPerm: label });
  patchSettings({ group_invite_policy: LABELS.groupPerm[label] });
  if (label === 'Invite link only') { loadAllowlist(); searchContacts(''); }
};
H.pickRetention = (e) => {
  const label = e.currentTarget.getAttribute('data-val');
  set({ retention: label });
  patchSettings({ media_retention: LABELS.retention[label] });
};
H.toggleMarkdown = () => { set({ markdownPreview: !state.markdownPreview }); patchSettings({ markdown_preview: state.markdownPreview }); };
H.toggleOnline = () => { set({ showOnline: !state.showOnline }); patchSettings({ show_online: state.showOnline }); };
H.toggleReceipts = () => { set({ readReceipts: !state.readReceipts }); patchSettings({ send_read_receipts: state.readReceipts }); };
H.openDeleteMedia = () => set({ deleteMediaOpen: true });
H.closeDeleteMedia = () => set({ deleteMediaOpen: false });
H.confirmDeleteMedia = async () => {
  set({ deleteMediaOpen: false });
  try {
    await API.post('/api/settings/delete-media');
    showToast('Media deletion scheduled');
    setTimeout(() => loadSettings().catch(() => {}), 1500);
  } catch (e) { showToast(errorText(e)); }
};

H.onGroupPermQuery = (e) => { set({ groupPermQuery: e.target.value }); searchContactsSoon(e.target.value); };
H.toggleGroupPermPerson = async (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  const on = state.allowlist.includes(id);
  set({ allowlist: on ? state.allowlist.filter((x) => x !== id) : state.allowlist.concat([id]) });
  try {
    if (on) await API.del('/api/settings/group-invite-allowlist/' + id);
    else await API.post('/api/settings/group-invite-allowlist/' + id);
  } catch (err) { showToast(errorText(err)); loadAllowlist(); }
};

H.onBlacklistQuery = (e) => { set({ blacklistQuery: e.target.value }); if (e.target.value.trim()) searchContactsSoon(e.target.value); };
H.onBlacklistFocus = () => set({ blacklistFocus: true });
H.onBlacklistBlur = () => set({ blacklistFocus: false });
H.blockPerson = async (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  try { await API.post('/api/blocklist/' + id); await loadBlocked(); showToast('Blocked'); } catch (err) { showToast(errorText(err)); }
};
H.unblockPerson = async (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  try { await API.del('/api/blocklist/' + id); await loadBlocked(); } catch (err) { showToast(errorText(err)); }
};

H.endSession = async (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  try { await API.del('/api/auth/sessions/' + id); await loadSessions(); showToast('Session ended'); } catch (err) { showToast(errorText(err)); }
};
H.logoutAll = () => logout(true);
H.logout = () => logout(false);
H.lockNow = () => lockApp();

/* --- смена код-пароля: текущий → новый → подтверждение (ТЗ 6.5, 6.6) --- */
H.goPasscode = () => {
  set({ view: 'passcode', pc: { step: 'current', value: '', first: '', error: '', busy: false } });
  setTimeout(() => { const el = document.getElementById('dc-passcode-input'); if (el) el.focus(); }, 50);
};
H.closePasscode = () => set({ view: 'settings' });
H.onPasscode = (e) => {
  const value = (e.target.value || '').replace(/\D/g, '').slice(0, 4);
  e.target.value = value;
  if (state.pc.busy) return;
  setState({ pc: Object.assign({}, state.pc, { value, error: value ? '' : state.pc.error }) });
  if (value.length === 4) setTimeout(() => submitPasscode(value), 120);
};

async function submitPasscode(value) {
  const pc = state.pc;
  const next = (patch) => {
    setState({ pc: Object.assign({}, pc, { value: '' }, patch) });
    const el = document.getElementById('dc-passcode-input');
    if (el) { el.value = ''; el.focus(); }
  };
  if (pc.step === 'current') {
    setState({ pc: Object.assign({}, pc, { busy: true }) });
    try { await VAULT.unlock(value); next({ step: 'new', busy: false, error: '' }); }
    catch (e) { next({ busy: false, error: 'Wrong passcode.' }); }
  } else if (pc.step === 'new') {
    next({ step: 'confirm', first: value });
  } else {
    if (value !== pc.first) { next({ step: 'new', first: '', error: 'Passcodes don’t match. Try again.' }); return; }
    setState({ pc: Object.assign({}, pc, { busy: true }) });
    try {
      await VAULT.changePin(value, vaultState());
      await backupKeys(value); // новые доли на realm-сервисах под новый PIN
      set({ view: 'settings' });
      showToast('Passcode changed');
    } catch (e) { next({ step: 'new', busy: false, error: errorText(e) }); }
  }
}

/* --- поиск по сообщениям --- */
H.openSearch = () => {
  set({ searchOpen: true, msgQuery: '', msgSearchResults: null });
  setTimeout(() => { const el = document.querySelector('.msg-search-box input'); if (el) el.focus(); }, 30);
};
H.closeSearch = () => set({ searchOpen: false, msgQuery: '', msgSearchResults: null });
let msgSearchTimer = null;
H.onMsgQuery = (e) => {
  set({ msgQuery: e.target.value });
  clearTimeout(msgSearchTimer);
  const tid = state.activeThread, q = e.target.value;
  msgSearchTimer = setTimeout(() => searchMessages(tid, q), 350);
};
H.onMsgSearchFocus = () => set({ msgSearchFocus: true });
H.onMsgSearchBlur = () => set({ msgSearchFocus: false });
H.clearMsgQuery = () => set({ msgQuery: '', msgSearchResults: null });
H.jumpToResult = (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  set({ searchOpen: false, msgQuery: '', msgSearchResults: null });
  jumpToMessage(id);
};

/* --- сообщения --- */
const activeMessages = () => state.messagesByThread[state.activeThread] || [];
const findMsg = (id) => activeMessages().find((m) => m.id === id);

H.hoverMsg = (e) => set({ hoverId: e.currentTarget.getAttribute('data-msg-id') });
H.unhoverMsg = () => set({ hoverId: null });
H.openMsgMenu = (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  const msg = findMsg(id);
  const btn = e.currentTarget;
  const rect = btn.getBoundingClientRect();
  const parentRect = btn.closest('main').getBoundingClientRect();
  const menuH = msg && msg.file ? 212 : 256;
  const below = rect.bottom - parentRect.top;
  const flipUp = below + menuH > parentRect.height - 16;
  setState({
    menuId: id, hoverId: id,
    menuPos: {
      top: flipUp ? Math.max(rect.bottom - parentRect.top - menuH, 8) : below,
      left: Math.min(rect.left - parentRect.left, parentRect.width - 260),
    },
  });
};
H.closeMsgMenu = () => set({ menuId: null });
H.react = () => showToast('Reactions are coming soon');
H.noop = () => {};
H.stop = (e) => e.stopPropagation();

H.downloadFile = (e) => {
  const msg = findMsg(e.currentTarget.getAttribute('data-id'));
  if (msg && msg.file && msg.file.key) saveFile(msg);
};

H.jumpToReply = (e) => {
  const rid = e.currentTarget.getAttribute('data-reply-id');
  if (rid) jumpToMessage(rid);
};

H.pickMenuItem = (e) => {
  const label = e.currentTarget.getAttribute('data-label');
  const msg = findMsg(state.menuId);
  set({ menuId: null });
  if (!msg || msg.pending) return;
  if (label === 'Forward') set({ forwardOpen: true, forwardMsgId: msg.id, forwardQuery: '' });
  if (label === 'Reply') {
    set({
      replyDraft: {
        id: msg.id,
        name: msg.mine ? 'You' : (peerOf().isGroup ? memberName(state.activeThread, msg.senderId) : peerOf().name) || 'Message',
        text: msg.file ? msg.file.base + msg.file.ext : CX.md.plain(msg.text).slice(0, 200),
        isFile: !!msg.file,
        size: msg.file ? msg.file.size : '',
      },
    });
    setTimeout(() => { const el = draftEl(); if (el) el.focus(); }, 60);
  }
  if (label === 'Copy text' && msg.text) {
    copyText(msg.text);
    setTimeout(() => showToast('Copied to clipboard'), 180);
  }
  if (label === 'Info') {
    set({ infoOpen: true, infoMsgId: msg.id, info: { loading: true } });
    loadProof(msg.id);
  }
};

H.cancelReply = () => set({ replyDraft: null });
H.closeInfo = () => set({ infoOpen: false, infoMsgId: null, info: null });
H.copyHash = async (e) => { await copyText(e.currentTarget.getAttribute('data-hash')); showToast('Hash copied'); };

/* --- пересылка --- */
H.closeForward = () => set({ forwardOpen: false });
H.onForwardQuery = (e) => set({ forwardQuery: e.target.value });
H.onForwardFocus = () => set({ forwardFocus: true });
H.onForwardBlur = () => set({ forwardFocus: false });
H.pickForward = async (e) => {
  const tid = e.currentTarget.getAttribute('data-id');
  const msg = findMsg(state.forwardMsgId);
  set({ forwardOpen: false });
  if (!msg) return;
  try { await forwardMessage(msg, tid); showToast('Forwarded'); } catch (err) { showToast(errorText(err)); }
};

/* --- композер --- */
H.onDraft = (e) => {
  set({ draft: e.target.value });
  autosizeDraft();
  notifyTyping(state.activeThread, !!e.target.value);
};
H.onDraftKey = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    H.send();
  }
};
H.send = () => {
  const text = state.draft.trim();
  const files = state.attachments || [];
  const tid = state.activeThread;
  if ((!text && !files.length) || !tid) return;
  const reply = state.replyDraft;
  set({ draft: '', attachments: [], replyDraft: null });
  hardResetDraft();
  notifyTyping(tid, false);
  (async () => {
    for (const f of files) await sendFile(tid, f, !text ? reply : null);
    if (text) await sendText(tid, text, reply);
  })();
};
H.pickFiles = () => { const el = document.getElementById('dc-file-input'); if (el) el.click(); };
H.onFiles = (e) => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  setState((st) => ({
    attachments: (st.attachments || []).concat(files.map((file, i) => {
      const n = splitName(file.name);
      return { id: 'a' + Date.now() + i, base: n.base, ext: n.ext, size: fmtBytes(file.size), bytes: file.size,
               mime: file.type || 'application/octet-stream', fileObj: file };
    })),
  }));
  e.target.value = '';
};
H.removeAttachment = (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  setState((st) => ({ attachments: st.attachments.filter((x) => x.id !== id) }));
};
H.onChatScroll = (e) => { if (e.currentTarget.scrollTop < 80) loadOlder(); };

/* --- новое сообщение и группа --- */
H.closeNew = () => set({ newOpen: false });
H.onNewQuery = (e) => { set({ newQuery: e.target.value }); searchContactsSoon(e.target.value); };
H.onNewSearchFocus = () => set({ newSearchFocus: true });
H.onNewSearchBlur = () => set({ newSearchFocus: false });
H.pickPerson = async (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  set({ newOpen: false });
  try {
    const detail = await API.post('/api/threads', { user_id: id });
    if (!threadById(detail.id)) state.threads = [toThread(detail)].concat(state.threads);
    await openThread(detail.id);
  } catch (err) { showToast(errorText(err)); }
};

H.openGroup = () => {
  set({ newOpen: false, groupOpen: true, groupQuery: '', groupSelected: [], groupStep: 'pick', groupName: '', groupPhoto: '', groupPhotoFile: null });
  searchContacts('');
};
H.closeGroup = () => set({ groupOpen: false });
H.onGroupQuery = (e) => { set({ groupQuery: e.target.value }); searchContactsSoon(e.target.value); };
H.onGroupSearchFocus = () => set({ groupSearchFocus: true });
H.onGroupSearchBlur = () => set({ groupSearchFocus: false });
H.toggleGroupPerson = (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  const on = state.groupSelected.includes(id);
  setState((st) => ({ groupSelected: on ? st.groupSelected.filter((x) => x !== id) : st.groupSelected.concat([id]) }));
};
H.groupNext = () => { if (state.groupSelected.length) set({ groupStep: 'name' }); };
H.groupBack = () => set({ groupStep: 'pick' });
H.onGroupName = (e) => set({ groupName: e.target.value });
H.pickGroupPhoto = () => { const el = document.getElementById('dc-group-photo'); if (el) el.click(); };
H.onGroupPhoto = (e) => {
  const file = (e.target.files || [])[0];
  if (!file) return;
  if (state.profileOpen && state.profileScreen === 'edit') { uploadGroupAvatar(file); e.target.value = ''; return; }
  set({ groupPhoto: URL.createObjectURL(file), groupPhotoFile: file });
  e.target.value = '';
};

async function uploadAvatarFile(file, kind) {
  const fd = new FormData();
  fd.append('file', file, file.name);
  fd.append('kind', kind);
  return API.post('/api/uploads', fd);
}

H.groupCreate = async () => {
  const name = (state.groupName || '').trim();
  if (!name) return;
  const members = state.groupSelected.slice();
  const photo = state.groupPhotoFile;
  set({ groupOpen: false, groupStep: 'pick', groupSelected: [], groupName: '', groupQuery: '', groupPhoto: '', groupPhotoFile: null });
  try {
    const avatar = photo ? await uploadAvatarFile(photo, 'group_avatar') : null;
    const detail = await API.post('/api/groups', { title: name, member_ids: members, avatar_upload_id: avatar ? avatar.id : null });
    state.members[detail.id] = detail.members;
    state.threads = [toThread(detail)].concat(state.threads.filter((t) => t.id !== detail.id));
    await openThread(detail.id);
    showToast('Group created — securing keys…');
  } catch (err) { showToast(errorText(err)); }
};

/* --- участники --- */
H.openMembers = () => {
  set({ membersOpen: true, moreOpen: false, profileOpen: false });
  loadMembers(state.activeThread).catch(() => {});
};
H.closeMembers = () => set({ membersOpen: false });
H.pickMember = async (e) => {
  const id = e.currentTarget.getAttribute('data-id');
  set({ membersOpen: false });
  if (id === S.userId) return;
  H.pickPerson({ currentTarget: { getAttribute: () => id } });
};

/* --- профиль чата / группы / свой --- */
H.openProfile = () => set({ profileOpen: true, profileClosing: false, moreOpen: false, profileScreen: 'details', navDir: 'none', profileTarget: 'thread' });
H.openMyProfile = () => {
  const me = state.me || {};
  set({
    profileOpen: true, profileClosing: false, moreOpen: false, profileScreen: 'edit', navDir: 'none',
    profileTarget: 'me', editName: me.display_name || '', editHandle: me.username ? '@' + me.username : '',
  });
};
H.closeProfile = () => dismissProfile();
H.goDetails = () => {
  if (state.profileTarget === 'me') { dismissProfile(); return; }
  set({ profileScreen: 'details', navDir: 'back' });
};
H.goEdit = () => {
  const t = peerOf();
  set({ editName: t.name || '', editHandle: t.handle || '', profileScreen: 'edit', navDir: 'forward' });
};
H.goDisappearing = () => set({ profileScreen: 'disappearing', navDir: 'forward' });
H.toggleMore = () => set({ moreOpen: !state.moreOpen });
H.searchFromProfile = () => dismissProfile(() => H.openSearch());
H.pickVanish = async (e) => {
  const label = e.currentTarget.getAttribute('data-val');
  const t = peerOf();
  const seconds = LABELS.vanish[label] || null;
  try {
    await API.patch('/api/threads/' + t.id + '/disappearing', { seconds });
    t.disappearing = seconds;
    render();
  } catch (err) { showToast(errorText(err)); }
};
H.clearThread = async () => {
  const tid = state.activeThread;
  if (state.profileOpen) dismissProfile();
  try {
    await API.post('/api/threads/' + tid + '/clear');
    state.messagesByThread[tid] = [];
    delete S.cache[tid];
    persist();
    const t = threadById(tid);
    if (t) { t.preview = 'No messages yet'; t.prefix = ''; }
    render();
  } catch (err) { showToast(errorText(err)); }
};
H.deleteThread = async () => {
  const t = peerOf();
  try {
    if (t.isGroup) await API.del('/api/groups/' + t.id + '/members/' + S.userId);  // группа: выйти
    else await API.del('/api/threads/' + t.id);
    const rest = state.threads.filter((x) => x.id !== t.id);
    delete state.messagesByThread[t.id];
    delete S.cache[t.id];
    persist();
    setState({ threads: rest, activeThread: null, profileOpen: false, profileClosing: false, moreOpen: false });
  } catch (err) { showToast(errorText(err)); }
};
H.saveProfile = async () => {
  const name = state.editName.trim();
  if (!name) return;
  try {
    if (state.profileTarget === 'me') {
      const handle = state.editHandle.trim().replace(/^@/, '');
      const me = await API.patch('/api/me', { display_name: name, username: handle });
      set({ me });
      showToast('Profile saved');
      dismissProfile();
      reloadThreadsSoon();
    } else {
      const t = peerOf();
      await API.patch('/api/groups/' + t.id, { title: name });
      t.name = name;
      t.initials = initialsOf(name);
      set({ profileScreen: 'details', navDir: 'back' });
    }
  } catch (err) { showToast(errorText(err)); }
};
H.onEditField = (e) => {
  const key = e.currentTarget.getAttribute('data-key');
  const max = Number(e.currentTarget.getAttribute('data-max'));
  set({ [key]: e.target.value.slice(0, max) });
};
H.onEditFocus = (e) => set({ focusField: e.currentTarget.getAttribute('data-key') });
H.onEditBlur = () => set({ focusField: null });
H.pickAvatar = () => {
  if (state.profileTarget === 'me') { const el = document.getElementById('dc-avatar-input'); if (el) el.click(); }
  else if (peerOf().isGroup) { const el = document.getElementById('dc-group-photo'); if (el) el.click(); }
};
H.onAvatarFile = async (e) => {
  const file = (e.target.files || [])[0];
  e.target.value = '';
  if (!file) return;
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    set({ me: await API.post('/api/me/avatar', fd) });
    showToast('Photo updated');
  } catch (err) { showToast(errorText(err)); }
};
async function uploadGroupAvatar(file) {
  const t = peerOf();
  try {
    const up = await uploadAvatarFile(file, 'group_avatar');
    const detail = await API.patch('/api/groups/' + t.id, { avatar_upload_id: up.id });
    Object.assign(t, toThread(detail, t));
    render();
  } catch (err) { showToast(errorText(err)); }
}
H.copyTon = async (e) => {
  if (e) { e.preventDefault(); e.stopPropagation(); }
  const addr = state.profileTarget === 'me' ? (state.me && state.me.ton_address_friendly) : peerOf().ton;
  if (!addr) return;
  await copyText(addr);
  showToast('Address copied');
};
