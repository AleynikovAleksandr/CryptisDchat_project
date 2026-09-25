/* Сборка HTML экранов (вёрстка и классы — из макета cryptis.html). */
'use strict';

/* ============================================================
   Экран PIN до разблокировки (ТЗ 4, 6.5, 6.6)
   ============================================================ */
const GATE_TEXT = {
  boot: ['CryptisDchat', 'Loading…'],
  unlock: ['Enter Passcode', 'Your passcode unlocks the encrypted messages and keys stored on this device.'],
  setup: ['Create Passcode', 'This passcode protects your encryption keys on this device and restores them on a new one. Without it, you will not be able to access your messages.'],
  'setup-confirm': ['Confirm Passcode', 'Enter the same passcode once more.'],
  restore: ['Restore Your Keys', 'Enter the passcode you set on your other device. Your keys will be reassembled from the independent backup services.'],
  reset: ['No Key Backup', 'This account has no backup that can be restored. You can create new encryption keys — messages sent before will stay unreadable on this device.'],
  busy: ['Please wait', ''],
};

function dotsHtml(value, error) {
  return '<div class="passcode-dots' + (error ? ' is-error' : '') + '">' + [0, 1, 2, 3].map((i) => {
    const filled = (value || '').length > i;
    const active = (value || '').length === i;
    return '<div class="passcode-dot' + (active ? ' is-active' : '') + '">' + (filled ? '●' : '') + '</div>';
  }).join('') + '</div>';
}

function gateHtml() {
  const g = state.gate || { mode: 'boot' };
  const [title, text] = GATE_TEXT[g.mode] || GATE_TEXT.boot;
  const needsPin = ['unlock', 'setup', 'setup-confirm', 'restore'].includes(g.mode);
  let body = '';
  if (needsPin) {
    body = dotsHtml(g.pin, g.error) +
      '<input id="dc-gate-input" class="passcode-input" value="' + esc(g.pin || '') + '" autocomplete="off"' +
      ' inputmode="numeric" pattern="[0-9]*" maxlength="4" data-input="onGatePin" autofocus>';
  } else if (g.mode === 'reset') {
    body = '<button class="btn-dark" type="button" data-click="gateReset">Create new keys</button>';
  } else {
    body = '<div class="gate-spinner"></div><div class="passcode-text">' + esc(g.text || '') + '</div>';
  }
  const foot = g.mode === 'unlock' || g.mode === 'restore' || g.mode === 'reset'
    ? '<button class="gate-link" type="button" data-click="gateOtherAccount">Use another wallet</button>' : '';
  return '<div class="passcode-screen gate" data-mk="gate-' + g.mode + '" data-click="focusGate">' +
    '<div class="passcode-body">' +
      I.lock +
      '<div class="passcode-title">' + esc(title) + '</div>' +
      (text ? '<div class="passcode-text">' + esc(text) + '</div>' : '') +
      body +
      '<div class="gate-error">' + esc(g.error || '') + '</div>' +
      foot +
    '</div>' +
    '<div class="gate-note">' + I.lockSm + ' End-to-end encrypted · keys never leave your device unencrypted</div>' +
  '</div>';
}

/* ============================================================
   Боковая панель
   ============================================================ */
function sidebarHtml() {
  const s = state;
  const size = props.compact ? 44 : 56;
  const font = props.compact ? 15 : 18;

  let head;
  if (s.sidebarOpen) {
    head = '<div class="sb-head" data-mk="head">' +
      '<div class="sb-head__title-slot">' +
        '<div class="sb-title">CryptisDchat</div>' +
        '<button class="sb-collapse" type="button" title="Chat" data-click="toggleSidebar">' + I.panel + '</button>' +
      '</div>' +
      '<button class="sb-pill" type="button" data-click="toggleFilter">' +
        '<span>' + esc(s.filter) + '</span>' + I.chevDown +
      '</button>' +
      '<button class="sb-round" type="button" title="New message" data-click="newChat">' + I.compose + '</button>' +
    '</div>';
  } else {
    head = '<div class="sb-rail" data-mk="rail">' +
      '<button class="sb-ghost" type="button" title="Expand" data-click="toggleSidebar">' + I.logo + '</button>' +
      '<button class="sb-round" type="button" title="' + esc(s.filter) + '" data-click="toggleFilter">' + I.lines + '</button>' +
      '<button class="sb-round" type="button" title="New message" data-click="newChat">' + I.composeSm + '</button>' +
    '</div>';
  }

  let filterMenu = '';
  if (s.filterOpen) {
    filterMenu = '<div class="filter-menu' + (s.sidebarOpen ? '' : ' is-rail') + '" data-mk="fmenu">' +
      ['All', 'Unread', 'Direct', 'Groups'].map((label) =>
        '<div class="filter-row" data-mk="f' + label + '" data-val="' + label + '" data-click="pickFilter">' +
          '<span style="flex:1;">' + label + '</span>' +
          (s.filter === label ? I.check : '') +
        '</div>').join('') +
      '<div class="filter-sep"></div>' +
      '<div class="filter-row" data-mk="fset" data-click="openSettings">Settings</div>' +
      '<div class="filter-row" data-mk="fread" data-click="markAllRead">Mark all as read</div>' +
    '</div>';
  }

  let search;
  if (s.sidebarOpen) {
    const active = s.searchFocus || !!s.query;
    search = '<div class="sb-search-wrap" data-mk="searchwrap">' +
      '<div class="sb-search' + (active ? ' is-active' : '') + '" data-click="focusSearch">' +
        I.search(18, active ? '#0f1419' : '#5b7083') +
        '<input id="dc-chat-search" value="' + esc(s.query) + '" placeholder="Search"' +
        ' data-input="onQuery" data-focus="onSearchFocus" data-blur="onSearchBlur">' +
        (active
          ? '<button class="sb-clear" type="button" data-click="clearQuery">' + I.x(16) + '</button>'
          : '') +
      '</div>' +
    '</div>';
  } else {
    search = '<div class="sb-search-rail" data-mk="searchrail">' +
      '<button class="sb-search-btn" type="button" title="Search" data-click="focusSearch">' + I.search(18, 'currentColor') + '</button>' +
    '</div>';
  }

  const q = s.query.toLowerCase();
  const filtered = s.threads
    .filter((t) => (t.name + ' ' + (t.handle || '')).toLowerCase().includes(q))
    .filter((t) => s.filter === 'All' || (s.filter === 'Unread' && t.unread > 0) ||
      (s.filter === 'Direct' && !t.isGroup) || (s.filter === 'Groups' && t.isGroup));

  const rows = filtered.map((t) => {
    const cls = 'thread' + (s.sidebarOpen ? '' : ' is-rail') + (t.id === s.activeThread ? ' is-active' : '');
    const typing = s.typing[t.id] && s.typing[t.id].until > Date.now();
    let body = '';
    if (s.sidebarOpen) {
      body = '<div class="thread__body">' +
        '<div class="thread__top">' +
          '<div class="thread__name-wrap">' +
            '<div class="thread__name">' + esc(t.name) + '</div>' +
            (t.muted ? I.mute : '') +
          '</div>' +
          '<div class="thread__time">' + esc(t.time) + '</div>' +
        '</div>' +
        '<div class="thread__bottom">' +
          '<div class="thread__preview">' +
            (typing
              ? '<span class="thread__typing">typing…</span>'
              : '<span class="thread__prefix">' + esc(t.prefix) + '</span><span>' + esc(t.preview) + '</span>') +
          '</div>' +
          (t.unread > 0 && t.id !== s.activeThread ? '<div class="thread__badge' + (t.muted ? ' is-muted' : '') + '">' + (t.unread > 99 ? '99+' : t.unread) + '</div>' : '') +
        '</div>' +
      '</div>';
    }
    const dot = !t.isGroup && t.online ? '<span class="thread__online"></span>' : '';
    return '<div class="' + cls + '" data-mk="t' + t.id + '" data-id="' + t.id + '" data-click="pickThread">' +
      '<div class="thread__avatar-cell">' + avatarHtml(size, t.tint, font, t.initials, t.avatarUrl) + dot + '</div>' +
      body +
    '</div>';
  }).join('');

  const empty = s.threadsLoaded && !filtered.length && s.sidebarOpen
    ? '<div class="sb-empty" data-mk="sbempty">' + (s.threads.length ? 'Nothing found.' : 'No conversations yet.') + '</div>'
    : '';
  const conn = s.connection !== 'online' && s.sidebarOpen
    ? '<div class="sb-conn" data-mk="conn">Reconnecting…</div>' : '';

  return head + filterMenu + search + conn + '<div class="sb-list" data-mk="list">' + rows + empty + '</div>';
}

/* ============================================================
   Экраны настроек
   ============================================================ */
const radioHtml = (on) => '<div class="radio' + (on ? ' is-on' : '') + '"></div>';
const trackHtml = (on, handler) =>
  '<div class="track' + (on ? ' is-on' : '') + '" data-click="' + handler + '"><div class="track__knob"></div></div>';

function settingsHtml() {
  const s = state;
  const me = s.me || {};
  const body = 'settings-body' + (s.sidebarOpen ? '' : ' is-rail');

  return '<div class="screen-scroll" data-mk="settings">' +
    '<div class="screen-head">' +
      '<button class="icon-back" type="button" data-click="closeSettings">' + I.back + '</button>' +
      '<div class="screen-title">Settings</div>' +
    '</div>' +
    '<div class="' + body + '">' +

      '<div class="set-h">Account</div>' +
      '<div class="set-row set-row--account" data-click="openMyProfile">' +
        avatarHtml(44, me.avatar_tint || BLUE, 16, initialsOf(me.display_name), me.avatar_url) +
        '<div class="set-row__label"><div style="font-weight:700;">' + esc(me.display_name || '') + '</div>' +
          '<div class="set-row__value mono">' + esc(shortAddr(me.ton_address_friendly)) + '</div></div>' +
        I.chevRight +
      '</div>' +
      '<div class="set-row" data-click="goSessions">' +
        '<div class="set-row__label">Active sessions</div>' + I.chevRight +
      '</div>' +

      '<div class="set-h set-h--gap">Encrypted messages</div>' +
      '<div class="set-row" data-click="goPasscode">' +
        '<div class="set-row__label">Change passcode</div>' + I.chevRight +
      '</div>' +
      '<div class="set-row set-row--static">' +
        '<div class="set-row__label">Key backup</div>' +
        '<div class="set-row__value">' + (me.has_backup ? '2 of 3 recovery services' : esc(me.backup_status || 'Not set up')) + '</div>' +
      '</div>' +

      '<div class="set-h set-h--gap">App auto-lock</div>' +
      '<div class="set-row" data-click="goAutolock">' +
        '<div class="set-row__label">Lock after inactivity</div>' +
        '<div class="set-row__value">' + esc(s.autolock) + '</div>' + I.chevRight +
      '</div>' +
      '<div class="set-row" data-click="lockNow"><div class="set-row__label">Lock now</div></div>' +

      '<div class="set-h set-h--gap">Privacy</div>' +
      '<div class="set-toggle-row set-toggle-row--flat">' +
        '<div style="flex:1;"><div class="set-toggle-row__title">Show when I’m online</div></div>' +
        trackHtml(s.showOnline, 'toggleOnline') +
      '</div>' +
      '<div class="set-toggle-row set-toggle-row--flat">' +
        '<div style="flex:1;"><div class="set-toggle-row__title">Send read receipts</div>' +
        '<div class="set-toggle-row__desc">Read receipts are also anchored in the TON delivery log.</div></div>' +
        trackHtml(s.readReceipts, 'toggleReceipts') +
      '</div>' +

      '<div class="set-h set-h--gap">Groups</div>' +
      '<div class="set-row" data-click="goGroupPerm">' +
        '<div class="set-row__label">Who can add me to groups</div>' +
        '<div class="set-row__value">' + esc(s.groupPerm) + '</div>' + I.chevRight +
      '</div>' +

      '<div class="set-h set-h--gap">Blocked TON addresses</div>' +
      '<div class="set-note">Blocked people can\'t message or add you to groups.</div>' +
      '<div class="set-row" data-click="goBlacklist">' +
        '<div class="set-row__label">Manage blocklist</div>' +
        '<div class="set-row__value">' + (s.blocked.length ? s.blocked.length + ' blocked' : 'None') + '</div>' + I.chevRight +
      '</div>' +

      '<div class="set-h set-h--gap">Local data &amp; storage</div>' +
      '<div class="set-note">Manage cached media and auto-delete rules.</div>' +
      '<div class="set-row set-row--static">' +
        '<div class="set-row__label">Total cached media</div>' +
        '<div class="set-row__value">' + fmtBytes(s.cachedMediaBytes || 0) + '</div>' +
      '</div>' +

      '<div class="set-sub">Auto-delete media older than</div>' +
      ['Keep all', '30 days', '60 days', '6 months'].map((label) =>
        '<div class="set-row" data-mk="ret' + label + '" data-val="' + label + '" data-click="pickRetention">' +
          '<div class="set-row__label">' + label + '</div>' + radioHtml(s.retention === label) +
        '</div>').join('') +
      '<div class="set-danger" data-click="openDeleteMedia">Delete all media</div>' +

      '<div class="set-toggle-row">' +
        '<div style="flex:1;">' +
          '<div class="set-toggle-row__title">Live Markdown preview</div>' +
          '<div class="set-toggle-row__desc">Preview formatting as you type a message.</div>' +
        '</div>' +
        trackHtml(s.markdownPreview, 'toggleMarkdown') +
      '</div>' +

      '<div class="set-danger" style="margin-top:26px;" data-click="logout">Log out of this device</div>' +
      '<div class="set-note" style="margin-top:6px;">Logging out permanently deletes the encrypted local data on this device.</div>' +
    '</div>' +
  '</div>';
}

function autolockHtml() {
  const s = state;
  return '<div class="screen-scroll" data-mk="autolock">' +
    '<div class="screen-head">' +
      '<button class="icon-back" type="button" data-click="closeAutolock">' + I.back + '</button>' +
      '<div class="screen-title">Lock after inactivity</div>' +
    '</div>' +
    '<div class="settings-body' + (s.sidebarOpen ? '' : ' is-rail') + '">' +
      ['1 minute', '5 minutes', '30 minutes', '1 hour', 'Never'].map((label) =>
        '<div class="set-row" data-mk="al' + label + '" data-val="' + label + '" data-click="pickAutolock">' +
          '<div class="set-row__label">' + label + '</div>' + radioHtml(s.autolock === label) +
        '</div>').join('') +
    '</div>' +
  '</div>';
}

function groupPermHtml() {
  const s = state;
  const people = s.directory.map((p) => {
    const on = s.allowlist.includes(p.id);
    return '<div class="person-row person-row--plain" data-mk="gp' + p.id + '" data-id="' + p.id + '" data-click="toggleGroupPermPerson">' +
      avatarHtml(40, p.tint, 14, p.initials, p.avatarUrl) +
      '<div class="person-row__grow">' +
        '<div class="person-row__name">' + esc(p.name) + '</div>' +
        '<div class="person-row__handle">' + esc(p.handle || shortAddr(p.ton)) + '</div>' +
      '</div>' +
      '<div class="check-circle' + (on ? ' is-on' : '') + '">' + (on ? I.checkWhite : '') + '</div>' +
    '</div>';
  }).join('');

  const exceptions = s.groupPerm === 'Invite link only'
    ? '<div class="set-sub" style="margin:22px 0 8px;">Allowed people</div>' +
      '<div class="pill-search pill-search--flat">' +
        I.search(17, '#536471') +
        '<input value="' + esc(s.groupPermQuery) + '" placeholder="Search name or username" data-input="onGroupPermQuery">' +
      '</div>' + people
    : '';

  return '<div class="screen-scroll" data-mk="groupperm">' +
    '<div class="screen-head">' +
      '<button class="icon-back" type="button" data-click="closeGroupPerm">' + I.back + '</button>' +
      '<div class="screen-title">Who can add me to groups</div>' +
    '</div>' +
    '<div class="settings-body' + (s.sidebarOpen ? '' : ' is-rail') + '">' +
      ['Everyone', 'No one', 'Invite link only'].map((label) =>
        '<div class="set-row" data-mk="gperm' + label + '" data-val="' + label + '" data-click="pickGroupPerm">' +
          '<div class="set-row__label">' + label + '</div>' + radioHtml(s.groupPerm === label) +
        '</div>').join('') +
      exceptions +
    '</div>' +
  '</div>';
}

function blacklistHtml() {
  const s = state;
  const blockedIds = s.blocked.map((p) => p.id);

  const matches = s.blacklistQuery.trim()
    ? s.directory
        .filter((p) => !blockedIds.includes(p.id))
        .map((p) =>
          '<div class="person-row" data-mk="bm' + p.id + '">' +
            avatarHtml(40, p.tint, 14, p.initials, p.avatarUrl) +
            '<div class="person-row__grow">' +
              '<div class="person-row__name">' + esc(p.name) + '</div>' +
              '<div class="person-row__handle">' + esc(p.handle || '') + '</div>' +
            '</div>' +
            '<button class="btn-block" type="button" data-id="' + p.id + '" data-click="blockPerson">Block</button>' +
          '</div>').join('')
    : '';

  const blocked = s.blocked.length
    ? s.blocked.map((p) =>
        '<div class="person-row" data-mk="bb' + p.id + '">' +
          avatarHtml(40, p.tint, 14, p.initials, p.avatarUrl) +
          '<div class="person-row__grow">' +
            '<div class="person-row__name">' + esc(p.name) + '</div>' +
            '<div class="person-row__ton">' + esc(p.ton || '') + '</div>' +
          '</div>' +
          '<button class="btn-unblock" type="button" data-id="' + p.id + '" data-click="unblockPerson">Unblock</button>' +
        '</div>').join('')
    : '<div style="font-size:14px;color:#536471;padding:8px 2px;">No blocked addresses yet.</div>';

  return '<div class="screen-scroll" data-mk="blacklist">' +
    '<div class="screen-head">' +
      '<button class="icon-back" type="button" data-click="closeBlacklist">' + I.back + '</button>' +
      '<div class="screen-title">Blocked TON addresses</div>' +
    '</div>' +
    '<div class="settings-body' + (s.sidebarOpen ? '' : ' is-rail') + '">' +
      '<div class="pill-search' + (s.blacklistFocus ? ' is-focus' : '') + '">' +
        I.search(17, '#536471') +
        '<input value="' + esc(s.blacklistQuery) + '" placeholder="Search name, username or TON address"' +
        ' data-input="onBlacklistQuery" data-focus="onBlacklistFocus" data-blur="onBlacklistBlur">' +
      '</div>' +
      matches +
      '<div class="set-sub" style="margin:20px 0 8px;">Blocked</div>' +
      blocked +
    '</div>' +
  '</div>';
}

function sessionsHtml() {
  const s = state;
  const rows = s.sessions.map((d) =>
    '<div class="person-row" data-mk="ss' + d.device_id + '">' +
      '<div class="person-row__grow">' +
        '<div class="person-row__name">' + esc(d.name) + (d.current ? ' <span class="pill-current">This device</span>' : '') + '</div>' +
        '<div class="person-row__handle">Last active ' + esc(relTime(d.last_seen_at)) + ' · ' + esc(d.ip_address || '') + '</div>' +
      '</div>' +
      (d.current ? '' : '<button class="btn-unblock" type="button" data-id="' + d.device_id + '" data-click="endSession">End session</button>') +
    '</div>').join('');
  return '<div class="screen-scroll" data-mk="sessions">' +
    '<div class="screen-head">' +
      '<button class="icon-back" type="button" data-click="closeSessions">' + I.back + '</button>' +
      '<div class="screen-title">Active sessions</div>' +
    '</div>' +
    '<div class="settings-body' + (s.sidebarOpen ? '' : ' is-rail') + '">' +
      '<div class="set-note">Each device keeps its own refresh token. Ending a session revokes it immediately.</div>' +
      rows +
      '<div class="set-danger" data-click="logoutAll">Log out of all devices</div>' +
    '</div>' +
  '</div>';
}

function passcodeHtml() {
  const pc = state.pc;
  const titles = {
    current: ['Change Passcode', 'Enter your current passcode.'],
    new: ['New Passcode', 'This passcode should be memorable and kept private. Without it, you will not be able to access your messages.'],
    confirm: ['Confirm Passcode', 'Enter the new passcode once more.'],
  };
  const [title, text] = titles[pc.step];
  return '<div class="passcode-screen" data-mk="passcode">' +
    '<button class="icon-back passcode-back" type="button" data-click="closePasscode">' + I.back + '</button>' +
    '<div class="passcode-body" data-mk="pc-' + pc.step + '">' +
      I.lock +
      '<div class="passcode-title">' + title + '</div>' +
      '<div class="passcode-text">' + text + '</div>' +
      (pc.busy ? '<div class="gate-spinner"></div>' : dotsHtml(pc.value, pc.error)) +
      '<input id="dc-passcode-input" class="passcode-input" value="' + esc(pc.value) + '" autocomplete="off"' +
      ' inputmode="numeric" pattern="[0-9]*" maxlength="4" data-input="onPasscode">' +
      '<div class="gate-error">' + esc(pc.error || '') + '</div>' +
    '</div>' +
  '</div>';
}

/* ============================================================
   Чат
   ============================================================ */
function msgSearchHtml() {
  const s = state;
  const q = (s.msgQuery || '').trim();
  const peer = peerOf();
  const local = (s.messagesByThread[s.activeThread] || [])
    .filter((m) => !m.bad && (m.text || '').toLowerCase().includes(q.toLowerCase()));
  const remote = s.msgSearchResults && s.msgSearchResults.threadId === s.activeThread ? s.msgSearchResults.items : [];
  const byId = new Map();
  local.concat(remote.filter((m) => !m.bad)).forEach((m) => byId.set(m.id, m));
  const list = Array.from(byId.values()).sort((a, b) => b.seq - a.seq);

  let results = '';
  if (q) {
    results =
      '<div class="msg-found">' + list.length + (list.length === 1 ? ' message found' : ' messages found') + '</div>' +
      '<div class="msg-results">' +
        list.map((m) => {
          const text = CX.md.plain(m.text || '');
          const i = text.toLowerCase().indexOf(q.toLowerCase());
          const html = i < 0 ? esc(text) : esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length));
          const name = m.mine ? 'You' : peer.isGroup ? memberName(s.activeThread, m.senderId) || 'Member' : peer.name;
          return '<div class="msg-result" data-mk="res' + m.id + '" data-id="' + m.id + '" data-click="jumpToResult">' +
            avatarHtml(44, m.mine ? BLUE : peer.tint, 15, m.mine ? 'Me' : initialsOf(name)) +
            '<div style="flex:1;min-width:0;">' +
              '<div class="msg-result__top">' +
                '<div class="msg-result__name">' + esc(name) + '</div>' +
                '<div class="msg-result__time">' + esc(m.meta) + '</div>' +
              '</div>' +
              '<div class="msg-result__text">' + html + '</div>' +
            '</div>' +
          '</div>';
        }).join('') +
      '</div>';
  }

  return '<div class="msg-search-layer" data-mk="msgsearch">' +
    '<div class="msg-search-shell">' +
      '<div class="msg-search-row">' +
        '<div class="msg-search-box' + (s.msgSearchFocus ? ' is-focus' : '') + '">' +
          I.search(18, '#5b7083') +
          '<input value="' + esc(s.msgQuery) + '" placeholder="Search messages…"' +
          ' data-input="onMsgQuery" data-focus="onMsgSearchFocus" data-blur="onMsgSearchBlur">' +
          (q ? '<button class="msg-search-clear" type="button" data-click="clearMsgQuery">Clear</button>' : '') +
        '</div>' +
        '<button class="msg-search-close" type="button" data-click="closeSearch">' + I.x(15) + '</button>' +
      '</div>' +
      results +
    '</div>' +
  '</div>';
}

/* Статус своего сообщения: отправлено → доставлено → прочитано, + подтверждение в TON. */
function statusHtml(m, t) {
  if (m.pending) return '<span class="msg-status" title="Sending">' + I.clockSm + '</span>';
  if (m.failed) return '<span class="msg-status is-failed" title="Not sent">' + I.warn + '</span>';
  const read = t.peerLastRead >= m.seq;
  const delivered = t.peerLastDelivered >= m.seq;
  const tick = read ? I.readTick : delivered ? I.tickDelivered : I.tickSent;
  const chain = m.confirmed ? '<span class="msg-chain" title="Anchored in TON">' + I.chain + '</span>' : '';
  return chain + '<span class="msg-status" title="' + (read ? 'Read' : delivered ? 'Delivered' : 'Sent') + '">' + tick + '</span>';
}

function messagesHtml() {
  const s = state;
  const t = peerOf();
  const showMeta = props.showTimestamps;
  let lastDay = '';

  return (s.messagesByThread[s.activeThread] || []).map((m) => {
    const hovered = s.hoverId === m.id;
    let out = '';

    const day = dayLabel(m.time);
    if (day !== lastDay) {
      lastDay = day;
      out += '<div class="chat-day" data-mk="day' + m.id + '">' + esc(day) + '</div>';
    }

    if (t.isGroup && !m.mine) {
      out += '<div class="msg-author" data-mk="au' + m.id + '">' + esc(memberName(s.activeThread, m.senderId) || 'Member') + '</div>';
    }

    if (m.reply) {
      out += '<div class="reply-wrap' + (m.mine ? ' is-mine' : '') + '" data-mk="rw' + m.id + '"' +
        ' data-reply-id="' + esc(m.reply.id) + '" data-click="jumpToReply">' +
        '<div class="reply-head">' + I.replyArrow + '<span>' + esc(m.reply.name) + '</span></div>' +
        '<div class="reply-body">' +
          (m.reply.isFile ? I.doc(17, 'flex-shrink:0;opacity:0.8;') : '') +
          '<span>' + esc(m.reply.text) + '</span>' +
          (m.reply.isFile && m.reply.size ? '<span class="reply-size">(' + esc(m.reply.size) + ')</span>' : '') +
        '</div>' +
      '</div>';
    }

    const actions = m.file
      ? '<div class="msg-actions msg-actions--file' + (m.mine ? ' is-mine' : '') + (hovered ? ' is-visible' : '') + '">' +
          '<button class="msg-icon" type="button" data-id="' + m.id + '" data-click="openMsgMenu">' + I.dotsSm + '</button>' +
          '<button class="msg-icon" type="button" title="Download" data-id="' + m.id + '" data-click="downloadFile">' + I.download + '</button>' +
          '<button class="msg-icon" type="button" title="React" data-click="react">' + I.reactPlus + '</button>' +
        '</div>'
      : '<div class="msg-actions' + (m.mine ? ' is-mine' : '') + (hovered ? ' is-visible' : '') + '">' +
          '<button class="msg-icon" type="button" data-id="' + m.id + '" data-click="openMsgMenu">' + I.dotsSm + '</button>' +
          '<button class="msg-icon" type="button" data-click="react">' + I.emojiSm + '</button>' +
        '</div>';

    const meta = '<span class="bubble__meta"' + (showMeta ? '' : ' style="display:none;"') + '>' +
      (m.expiresAt ? '<span title="Disappearing">' + I.clockSm + '</span>' : '') +
      '<span>' + esc(m.meta) + '</span>' + (m.mine ? statusHtml(m, t) : '') +
    '</span>';

    let content;
    if (m.file) {
      content = '<div class="file-card' + (m.pending ? ' is-pending' : '') + '" title="Tap to download" data-id="' + m.id + '" data-click="downloadFile">' +
        I.doc(20, 'flex-shrink:0;opacity:0.75;') +
        '<div class="file-card__body">' +
          '<div class="file-card__name"><span>' + esc(m.file.base) + '</span><span>' + esc(m.file.ext) + '</span></div>' +
          '<div class="file-card__sub"><span>' + (m.pending ? 'Encrypting…' : 'Tap to download') + '</span><span>·</span><span>' + esc(m.file.size) + '</span>' +
            (m.mine ? '<span class="file-card__status">' + statusHtml(m, t) + '</span>' : '') + '</div>' +
        '</div>' +
      '</div>';
    } else if (m.bad) {
      content = '<div class="bubble is-bad">' + I.warn + '<span class="bubble__text">' + esc(m.text) + '</span>' + meta + '</div>';
    } else {
      const rich = CX.md.hasMarkup(m.text);
      content = '<div class="bubble' + (m.mine ? ' is-mine' : '') + (m.pending ? ' is-pending' : '') + (rich ? ' is-rich' : '') + '">' +
        (m.forwarded ? '<div class="bubble__fwd">Forwarded</div>' : '') +
        (rich ? '<div class="bubble__text md">' + CX.md.render(m.text) + '</div>' : '<span class="bubble__text">' + esc(m.text) + '</span>') +
        meta +
      '</div>';
    }

    out += '<div class="msg-row' + (m.mine ? ' is-mine' : '') + '" data-mk="m' + m.id + '" data-msg-id="' + m.id + '"' +
      ' data-mouseenter="hoverMsg" data-mouseleave="unhoverMsg">' + actions + content + '</div>';

    return out;
  }).join('');
}

function msgMenuHtml() {
  const s = state;
  if (!s.menuId) return '';
  const activeMsg = (s.messagesByThread[s.activeThread] || []).find((m) => m.id === s.menuId);
  const isFile = !!(activeMsg && activeMsg.file);
  const pos = s.menuPos || { top: 0, left: 0 };

  return '<div class="msg-menu-scrim" data-mk="menuscrim" data-click="closeMsgMenu">' +
    '<div class="msg-menu" style="top:' + pos.top + 'px;left:' + pos.left + 'px;">' +
      ['Reply', 'Forward', 'Copy text', 'Info']
        .filter((label) => !(label === 'Copy text' && isFile))
        .map((label) =>
          '<div class="msg-menu__item" data-mk="mi' + label + '" data-label="' + label + '" data-click="pickMenuItem">' +
            '<span style="display:flex;align-items:center;color:#000;flex-shrink:0;">' + MENU_ICONS[label] + '</span>' +
            '<span>' + label + '</span>' +
          '</div>').join('') +
    '</div>' +
  '</div>';
}

function composerHtml() {
  const s = state;
  const ready = !!(s.draft.trim() || (s.attachments || []).length);

  const attachments = (s.attachments || []).length
    ? '<div class="attach-strip">' +
        s.attachments.map((a) =>
          '<div class="attach" data-mk="at' + a.id + '">' +
            '<div class="attach__card">' +
              I.doc(20, 'flex-shrink:0;color:#5b7083;') +
              '<div class="attach__body">' +
                '<div class="attach__name"><span>' + esc(a.base) + '</span><span>' + esc(a.ext) + '</span></div>' +
                '<div class="attach__sub"><span>Document</span><span>·</span><span>' + esc(a.size) + '</span></div>' +
              '</div>' +
            '</div>' +
            '<button class="attach__x" type="button" title="Remove" data-id="' + a.id + '" data-click="removeAttachment">' +
              I.xFill(12) +
            '</button>' +
          '</div>').join('') +
      '</div>'
    : '';

  // ТЗ 5: живое превью форматирования во время набора
  const preview = s.markdownPreview && CX.md.hasMarkup(s.draft)
    ? '<div class="md-preview md" data-mk="mdpreview">' + CX.md.render(s.draft) + '</div>'
    : '';

  return '<div class="composer' + (s.sidebarOpen ? '' : ' is-rail') + '" data-mk="composer">' +
    '<button class="comp-btn comp-attach" type="button" title="Add attachment" data-click="pickFiles">' + I.plus + '</button>' +
    '<button class="comp-btn comp-emoji" type="button" title="Emoji" data-click="noop">' + I.emoji + '</button>' +
    '<div class="comp-field">' +
      preview +
      attachments +
      '<div class="comp-input-row">' +
        '<textarea id="dc-draft" class="comp-textarea" rows="1" placeholder="Message"' +
        ' data-mk="draft" data-input="onDraft" data-keydown="onDraftKey"></textarea>' +
        '<button class="comp-send' + (ready ? ' is-ready' : '') + '" type="button" title="Send" data-click="send">' +
          I.sendArrow +
        '</button>' +
      '</div>' +
    '</div>' +
  '</div>';
}

function chatSubtitle(t) {
  const typing = state.typing[t.id];
  if (typing && typing.until > Date.now()) {
    return t.isGroup ? (memberName(t.id, typing.userId) || 'Someone') + ' is typing…' : 'typing…';
  }
  if (t.isGroup) {
    if (t.rotationPending || !t.keyEpoch) return 'Securing group keys…';
    return t.membersCount + ' members';
  }
  return t.online ? 'online' : 'last seen recently';
}

function chatHtml() {
  const s = state;
  const peer = peerOf();

  const replyDraft = s.replyDraft
    ? '<div class="reply-draft-layer" data-mk="rd">' +
        '<div class="reply-draft">' +
          '<div style="flex:1;min-width:0;">' +
            '<div class="reply-draft__name">' + esc(s.replyDraft.name) + '</div>' +
            '<div class="reply-draft__text">' + esc(s.replyDraft.text) + '</div>' +
          '</div>' +
          '<button class="reply-draft__x" type="button" title="Cancel reply" data-click="cancelReply">' + I.xFill(16) + '</button>' +
        '</div>' +
      '</div>'
    : '';

  const list = s.messagesByThread[s.activeThread];
  const intro = '<div class="chat-joined">' + I.lockSm +
    ' Messages are end-to-end encrypted. No one outside this chat — not even CryptisDchat — can read them.</div>';
  const loadingOlder = s.hasMore[s.activeThread]
    ? '<div class="chat-older" data-mk="older">' + (s.loadingOlder ? 'Loading…' : 'Scroll up for older messages') + '</div>' : '';
  const sub = chatSubtitle(peer);

  return '<div class="chat" data-mk="chat">' +
    '<div class="chat-head">' +
      '<div class="chat-head__blur"></div>' +
      '<div class="chat-head__peer" data-click="openProfile">' +
        avatarHtml(36, peer.tint, 14, peer.initials, peer.avatarUrl) +
        '<div><div class="chat-head__name">' + esc(peer.name) + '</div>' +
        (sub ? '<div class="chat-head__sub' + (!peer.isGroup && peer.online && sub === 'online' ? ' is-online' : '') + '">' + esc(sub) + '</div>' : '') + '</div>' +
      '</div>' +
      '<div style="flex:1;"></div>' +
      '<button class="chat-head__more" type="button" title="Profile settings" data-click="openProfile">' + I.dots + '</button>' +
    '</div>' +
    '<div class="chat-fade"></div>' +

    (s.searchOpen ? msgSearchHtml() : '') +

    '<div id="chatScroll" class="chat-scroll" data-mk="scroll-' + s.activeThread + '" data-scroll="onChatScroll">' +
      loadingOlder +
      intro +
      (list ? '' : '<div class="chat-day">Loading…</div>') +
      '<div class="chat-list">' + messagesHtml() + '</div>' +
    '</div>' +

    msgMenuHtml() +
    replyDraft +
    composerHtml() +
  '</div>';
}

function emptyChatHtml() {
  return '<div class="empty" data-mk="empty">' +
    '<div class="empty__circle">' + I.bubbleBig + '</div>' +
    '<div class="empty__title">Start Conversation</div>' +
    '<div class="empty__text">Choose from your existing conversations, or start a new one.</div>' +
    '<button class="btn-dark" type="button" data-click="newChat">New chat</button>' +
  '</div>';
}

function mainHtml() {
  const s = state;
  if (s.view === 'settings') return settingsHtml();
  if (s.view === 'autolock') return autolockHtml();
  if (s.view === 'groupperm') return groupPermHtml();
  if (s.view === 'blacklist') return blacklistHtml();
  if (s.view === 'sessions') return sessionsHtml();
  if (s.view === 'passcode') return passcodeHtml();
  if (s.view === 'chat' && (!s.activeThread || !threadById(s.activeThread))) return emptyChatHtml();
  return chatHtml();
}

/* ============================================================
   Окна поверх страницы
   ============================================================ */
function personRowsHtml(people, mkPrefix, click, selectable) {
  if (!people.length) return '<div class="sheet-empty">No people found.</div>';
  return people.map((p) => {
    const on = selectable && state.groupSelected.includes(p.id);
    return '<div class="dir-row" data-mk="' + mkPrefix + p.id + '" data-id="' + p.id + '" data-click="' + click + '">' +
      avatarHtml(48, p.tint, 16, p.initials, p.avatarUrl) +
      '<div style="min-width:0;flex:1;">' +
        '<div class="dir-row__name">' + esc(p.name) + '</div>' +
        '<div class="dir-row__handle">' + esc(p.handle || shortAddr(p.ton)) + '</div>' +
      '</div>' +
      (selectable ? '<div class="check-circle' + (on ? ' is-on' : '') + '">' + (on ? I.checkWhite : '') + '</div>' : '') +
    '</div>';
  }).join('');
}

function newSheetHtml() {
  const s = state;
  return '<div class="ov ov--new" data-mk="ovnew" data-click="closeNew">' +
    '<div class="sheet" data-click="stop">' +
      '<div class="sheet-head">' +
        '<div class="sheet-head__title">New message</div>' +
        '<button class="sheet-x" type="button" data-click="closeNew">' + I.x(20) + '</button>' +
      '</div>' +
      '<div class="new-search-wrap">' +
        '<div class="new-search' + (s.newSearchFocus ? ' is-focus' : '') + '">' +
          I.search(19, '#536471') +
          '<input value="' + esc(s.newQuery) + '" placeholder="Search name, username or TON address"' +
          ' data-input="onNewQuery" data-focus="onNewSearchFocus" data-blur="onNewSearchBlur">' +
        '</div>' +
      '</div>' +
      '<div class="create-group" data-click="openGroup">' +
        I.people + '<div class="create-group__label">Create a group</div>' +
      '</div>' +
      '<div class="sheet-scroll">' + personRowsHtml(s.directory, 'np', 'pickPerson', false) + '</div>' +
    '</div>' +
  '</div>';
}

function groupSheetHtml() {
  const s = state;
  const naming = s.groupStep === 'name';

  const picking = '<div class="sheet-sub">Add people</div>' +
    '<div style="padding:0 24px 14px;">' +
      '<button class="btn-next' + (s.groupSelected.length ? ' is-on' : '') + '" type="button"' +
      (s.groupSelected.length ? '' : ' disabled') + ' data-click="groupNext">Next</button>' +
    '</div>' +
    '<div class="group-search-wrap">' +
      '<div class="group-search' + (s.groupSearchFocus ? ' is-focus' : '') + '">' +
        I.search(19, '#536471') +
        '<input value="' + esc(s.groupQuery) + '" placeholder="Search name or username"' +
        ' data-input="onGroupQuery" data-focus="onGroupSearchFocus" data-blur="onGroupSearchBlur">' +
      '</div>' +
    '</div>' +
    '<div class="sheet-scroll" style="padding:4px 0 16px;">' +
      personRowsHtml(s.directory, 'gp', 'toggleGroupPerson', true) +
    '</div>';

  const nameStep = '<div class="sheet-sub">Name this group</div>' +
    '<div class="group-name-body">' +
      '<div class="group-photo" data-click="pickGroupPhoto"' +
      (s.groupPhoto ? ' style="background-image:url(' + s.groupPhoto + ');"' : '') + '>' +
        (s.groupPhoto ? '' : I.camera) +
      '</div>' +
      '<div class="group-name-box">' +
        '<div class="group-name-box__label">Group name</div>' +
        '<input value="' + esc(s.groupName) + '" placeholder="e.g. Design crew" maxlength="80" data-input="onGroupName">' +
      '</div>' +
      '<div class="group-count">' + s.groupSelected.length +
        (s.groupSelected.length === 1 ? ' member added' : ' members added') + '</div>' +
      '<div class="set-note" style="width:100%;">Group membership is recorded on the TON blockchain; encryption keys rotate automatically when members change.</div>' +
    '</div>' +
    '<div class="group-foot">' +
      '<button class="btn-back" type="button" data-click="groupBack">Back</button>' +
      '<button class="btn-create' + ((s.groupName || '').trim() ? ' is-on' : '') + '" type="button" data-click="groupCreate">Create</button>' +
    '</div>';

  return '<div class="ov ov--group" data-mk="ovgroup" data-click="closeGroup">' +
    '<div class="sheet" data-click="stop">' +
      '<div class="sheet-head" style="padding:22px 24px 4px;">' +
        '<div class="sheet-head__title">Create a group</div>' +
        '<button class="sheet-x" type="button" data-click="closeGroup">' + I.xBold + '</button>' +
      '</div>' +
      (naming ? nameStep : picking) +
    '</div>' +
  '</div>';
}

function membersSheetHtml() {
  const members = state.members[state.activeThread] || [];
  const rows = members.map((m) => {
    const p = toPerson(m.user);
    return '<div class="member-row" data-mk="mem' + p.id + '" data-id="' + p.id + '" data-click="pickMember">' +
      avatarHtml(44, p.tint, 15, p.initials, p.avatarUrl) +
      '<div style="flex:1;min-width:0;">' +
        '<div class="member-row__name">' + esc(p.name) + (p.id === S.userId ? ' (you)' : '') + '</div>' +
        '<div class="member-row__handle">' + esc(m.role !== 'member' ? m.role : (p.handle || shortAddr(p.ton))) + '</div>' +
      '</div>' + I.chevRightSm +
    '</div>';
  }).join('');

  return '<div class="ov ov--members" data-mk="ovmembers" data-click="closeMembers">' +
    '<div class="sheet sheet--members" data-click="stop">' +
      '<div class="sheet-head" style="padding:20px 20px 10px;">' +
        '<div class="sheet-head__title" style="font-size:20px;">Members</div>' +
        '<button class="sheet-x sheet-x--sm" type="button" data-click="closeMembers">' + I.xBold + '</button>' +
      '</div>' +
      '<div class="members-count">' + members.length + (members.length === 1 ? ' person' : ' people') + '</div>' +
      '<div class="members-list">' + rows + '</div>' +
    '</div>' +
  '</div>';
}

function forwardSheetHtml() {
  const s = state;
  const rows = s.threads
    .filter((t) => (t.name + ' ' + (t.handle || '')).toLowerCase().includes((s.forwardQuery || '').toLowerCase()))
    .map((t) =>
      '<div class="fwd-row" data-mk="fw' + t.id + '" data-id="' + t.id + '" data-click="pickForward">' +
        avatarHtml(40, t.tint, 14, t.initials, t.avatarUrl) +
        '<div style="flex:1;min-width:0;">' +
          '<div class="fwd-row__name">' + esc(t.name) + '</div>' +
          '<div class="fwd-row__handle">' + esc(t.handle || (t.isGroup ? t.membersCount + ' members' : '')) + '</div>' +
        '</div>' +
      '</div>').join('');

  return '<div class="ov ov--small" data-mk="ovfwd" data-click="closeForward">' +
    '<div class="sheet sheet--forward" data-click="stop">' +
      '<div class="fwd-head">' +
        '<button class="sheet-x sheet-x--tiny" type="button" data-click="closeForward">' + I.backSm + '</button>' +
        '<div class="fwd-head__title">Forward</div>' +
        '<div style="width:32px;"></div>' +
      '</div>' +
      '<div class="fwd-search-wrap">' +
        '<div class="fwd-search' + (s.forwardFocus ? ' is-focus' : '') + '">' +
          I.search(17, '#536471') +
          '<input value="' + esc(s.forwardQuery) + '" placeholder="Search"' +
          ' data-input="onForwardQuery" data-focus="onForwardFocus" data-blur="onForwardBlur">' +
        '</div>' +
      '</div>' +
      '<div class="fwd-list">' + rows + '</div>' +
    '</div>' +
  '</div>';
}

function deleteMediaHtml() {
  return '<div class="ov ov--small" data-mk="ovdel" data-click="closeDeleteMedia">' +
    '<div class="sheet sheet--dialog" data-click="stop">' +
      '<button class="dialog-x" type="button" data-click="closeDeleteMedia">' + I.xFill(16) + '</button>' +
      '<div class="dialog-title">Delete all media?</div>' +
      '<div class="dialog-text">This will delete all encrypted files you have uploaded across all conversations. Recipients will no longer be able to download them.</div>' +
      '<div class="dialog-foot">' +
        '<button class="btn-cancel" type="button" data-click="closeDeleteMedia">Cancel</button>' +
        '<button class="btn-delete" type="button" data-click="confirmDeleteMedia">Delete</button>' +
      '</div>' +
    '</div>' +
  '</div>';
}

/* «Информация о сообщении»: хэш, батч, транзакция и локальная проверка Merkle-proof (ТЗ 2). */
function infoSheetHtml() {
  const s = state;
  const info = s.info || {};
  const LABEL = { 'message.sent': 'Sent', 'message.delivered': 'Delivered', 'message.read': 'Read' };
  let body;
  if (info.loading) body = '<div class="info-empty">Loading proof…</div>';
  else if (info.error) body = '<div class="info-empty">' + esc(info.error) + '</div>';
  else {
    const events = info.events || [];
    body = '<div class="info-row"><div class="info-row__label">Ciphertext hash</div>' +
      '<div class="info-row__value mono" data-hash="' + esc(info.content_hash) + '" data-click="copyHash">' + esc(info.content_hash) + '</div></div>' +
      (events.length ? events.map((ev, i) => {
        const b = ev.batch;
        const state_ = ev.status === 'confirmed' ? '<span class="info-ok">' + I.chain + ' Confirmed on TON</span>'
          : ev.status === 'batched' ? '<span class="info-wait">Waiting for confirmation</span>'
          : '<span class="info-wait">Queued for the next batch</span>';
        const who = ev.actor_id === S.userId ? 'you' : (peerOf().isGroup ? memberName(s.activeThread, ev.actor_id) : peerOf().name) || 'member';
        return '<div class="info-event" data-mk="ev' + i + '">' +
          '<div class="info-event__head"><b>' + esc(LABEL[ev.event_type] || ev.event_type) + '</b> by ' + esc(who) +
          ' · ' + esc(relTime(ev.created_at)) + '</div>' +
          '<div class="info-event__state">' + state_ + (b && ev.verified ? ' <span class="info-ok">✓ Merkle proof verified on this device</span>' : '') + '</div>' +
          '<div class="info-row"><div class="info-row__label">Event hash</div><div class="info-row__value mono" data-hash="' + esc(ev.event_hash) + '" data-click="copyHash">' + esc(ev.event_hash) + '</div></div>' +
          (b ? '<div class="info-row"><div class="info-row__label">Merkle root</div><div class="info-row__value mono" data-hash="' + esc(b.merkle_root) + '" data-click="copyHash">' + esc(b.merkle_root) + '</div></div>' +
               '<div class="info-row"><div class="info-row__label">Transaction</div><div class="info-row__value mono" data-hash="' + esc(b.tx_hash || '') + '" data-click="copyHash">' + esc(b.tx_hash || '—') + '</div></div>' : '') +
        '</div>';
      }).join('') : '<div class="info-empty">No blockchain events yet.</div>');
  }
  return '<div class="ov ov--small" data-mk="ovinfo" data-click="closeInfo">' +
    '<div class="sheet sheet--dialog sheet--info" data-click="stop">' +
      '<button class="dialog-x" type="button" data-click="closeInfo">' + I.xFill(16) + '</button>' +
      '<div class="dialog-title">Message info</div>' +
      '<div class="dialog-text">Only a hash of the encrypted message is anchored on the TON blockchain — never its content.</div>' +
      body +
    '</div>' +
  '</div>';
}

/* ---------- карточка профиля ---------- */
const navClass = () => (state.navDir === 'none' ? '' : state.navDir === 'forward' ? ' is-forward' : ' is-back');

function profileDetailsHtml() {
  const s = state;
  const peer = peerOf();
  const vanishLabel = labelOf('vanish', peer.disappearing || 0);

  const more = s.moreOpen
    ? '<div class="pmore" data-mk="pmore">' +
        (peer.isGroup
          ? '<div class="pmore__item" data-mk="pmembers" data-click="openMembers">' + I.peopleSm + '<span>Show members</span></div>'
          : '') +
        '<div class="pmore__item pmore__item--danger" data-mk="pclear" data-click="clearThread">' + I.trash + '<span>Clear conversation</span></div>' +
        '<div class="pmore__item pmore__item--danger" data-mk="pdel" data-click="deleteThread">' + I.trash + '<span>' + (peer.isGroup ? 'Leave group' : 'Delete conversation') + '</span></div>' +
      '</div>'
    : '';

  return '<div class="pscreen' + navClass() + '" data-mk="pdetails">' +
    '<div style="display:flex;align-items:center;padding:14px 16px;">' +
      '<button class="pback" type="button" data-click="closeProfile">' + I.back + '</button>' +
      '<div style="flex:1;"></div>' +
    '</div>' +
    '<div class="pbody">' +
      '<div class="phero">' +
        avatarHtml(124, peer.tint, 38, peer.initials, peer.avatarUrl) +
        '<div class="phero__name">' + esc(peer.name) + '</div>' +
        '<div class="phero__handle">' + esc(peer.isGroup ? peer.membersCount + ' members' : (peer.handle || shortAddr(peer.ton))) + '</div>' +
      '</div>' +

      '<div class="pactions">' +
        '<div class="paction" data-click="goEdit">' +
          '<div class="paction__circle">' + I.person + '</div>' +
          '<div class="paction__label">Profile</div>' +
        '</div>' +
        '<div class="paction" data-click="searchFromProfile">' +
          '<div class="paction__circle">' + I.searchDark + '</div>' +
          '<div class="paction__label">Search</div>' +
        '</div>' +
        '<div class="paction" data-click="toggleMore">' +
          '<div class="paction__circle' + (s.moreOpen ? ' is-open' : '') + '">' + I.dotsBig + '</div>' +
          '<div class="paction__label">More</div>' +
        '</div>' +
        more +
      '</div>' +

      '<div class="pgroup">' +
        '<div class="pgroup__row" data-click="goDisappearing">' +
          I.vanishIcon +
          '<div class="pgroup__label">Disappearing messages</div>' +
          '<div class="pgroup__value">' + (vanishLabel === 'None' ? 'Off' : esc(vanishLabel)) + '</div>' +
          I.chevRightSm +
        '</div>' +
      '</div>' +
    '</div>' +
  '</div>';
}

function profileDisappearingHtml() {
  const current = labelOf('vanish', peerOf().disappearing || 0);
  return '<div class="pscreen' + navClass() + '" data-mk="pvanish">' +
    '<div class="pscreen-head">' +
      '<button class="pback" type="button" data-click="goDetails">' + I.back + '</button>' +
      '<div class="pscreen-head__title">Set disappearing messages</div>' +
      '<div class="pscreen-head__spacer"></div>' +
    '</div>' +
    '<div style="flex:1;min-height:0;overflow-y:auto;padding:14px 22px 8px;">' +
      '<div class="vanish-note">' + I.clock +
        '<div class="vanish-note__text">After messages in this chat get read, they will vanish after the selected time below.</div>' +
      '</div>' +
      '<div class="vanish-list">' +
        Object.keys(LABELS.vanish).map((label) =>
          '<div class="vanish-row" data-mk="v' + label + '" data-val="' + label + '" data-click="pickVanish">' +
            '<div class="vanish-row__label">' + label + '</div>' +
            (current === label ? I.checkBlue : '') +
          '</div>').join('') +
      '</div>' +
    '</div>' +
    '<div class="vanish-foot">' +
      '<button class="btn-clear-all" type="button" data-click="clearThread">Clear all messages</button>' +
    '</div>' +
  '</div>';
}

/* Редактирование: своего профиля (из настроек) или группы (для админов);
 * для собеседника в личном чате — только просмотр. */
function profileEditHtml() {
  const s = state;
  const mine = s.profileTarget === 'me';
  const peer = peerOf();
  const me = s.me || {};
  const editable = mine || (peer.isGroup && ['owner', 'admin'].includes(peer.myRole));
  const tint = mine ? me.avatar_tint || BLUE : peer.tint;
  const url = mine ? me.avatar_url : peer.avatarUrl;
  const initials = mine ? initialsOf(me.display_name) : peer.initials;

  const fields = editable
    ? ((mine ? [{ key: 'editName', label: 'Name', max: 50 }, { key: 'editHandle', label: 'Username', max: 16 }]
             : [{ key: 'editName', label: 'Name', max: 80 }])
      ).map((f) => {
        const focused = s.focusField === f.key;
        const val = s[f.key] || '';
        return '<div class="pfield' + (focused ? ' is-focus' : '') + '" data-mk="pf' + f.key + '">' +
          '<div class="pfield__top">' +
            '<div class="pfield__label">' + f.label + '</div>' +
            '<div style="flex:1;"></div>' +
            '<div class="pfield__count">' + val.length + ' / ' + f.max + '</div>' +
          '</div>' +
          '<input value="' + esc(val) + '" placeholder="' + f.label + '" data-key="' + f.key + '" data-max="' + f.max + '"' +
          ' data-input="onEditField" data-focus="onEditFocus" data-blur="onEditBlur">' +
        '</div>';
      }).join('')
    : '<div class="pview"><div class="pview__name">' + esc(peer.name) + '</div>' +
      '<div class="pview__handle">' + esc(peer.handle || '') + '</div></div>';

  const tonAddress = mine ? me.ton_address_friendly : peer.ton;
  const tonBlock = peer.isGroup && !mine ? '' :
    '<div class="ton-block">' +
      '<div class="ton-block__label">TON address</div>' +
      '<div class="ton-row">' +
        '<div class="ton-row__addr">' + esc(tonAddress ? shortAddr(tonAddress) : 'Not connected') + '</div>' +
        '<button class="ton-copy" type="button" data-click="copyTon">' + I.copy + '<span>Copy</span></button>' +
      '</div>' +
      '<div class="ton-hint">' + (mine ? 'Linked to your wallet — read-only.' : 'Verified with ton_proof when this person signed in.') + '</div>' +
    '</div>';

  return '<div class="pscreen' + navClass() + '" data-mk="pedit' + (mine ? 'me' : '') + '">' +
    '<div class="pedit-head">' +
      '<button class="pback" type="button" style="position:relative;z-index:1;" data-click="goDetails">' + I.back + '</button>' +
      '<div class="pedit-head__title">' + (mine ? 'Edit profile' : peer.isGroup ? 'Group info' : 'Profile') + '</div>' +
      '<div style="flex:1;"></div>' +
      (editable ? '<button class="pill-btn' + (s.editName.trim() ? ' is-on' : '') + '" type="button" data-click="saveProfile">Save</button>' : '') +
    '</div>' +
    '<div style="flex:1;min-height:0;overflow-y:auto;">' +
      '<div class="pedit-avatar-row">' +
        '<div class="pedit-avatar"' + (editable ? ' data-click="pickAvatar"' : '') +
        ' style="' + (url ? 'background-image:url(' + esc(url) + ');' : 'background:' + esc(tint) + ';') + '">' +
          '<span>' + (url ? '' : esc(initials)) + '</span>' +
          (editable ? '<div class="pedit-avatar__veil" data-click="pickAvatar">' + I.camera + '</div>' : '') +
        '</div>' +
      '</div>' +
      '<div class="pedit-fields">' +
        fields +
        tonBlock +
      '</div>' +
    '</div>' +
  '</div>';
}

function profileHtml() {
  const s = state;
  let screen = '';
  if (s.profileScreen === 'details') screen = profileDetailsHtml();
  else if (s.profileScreen === 'disappearing') screen = profileDisappearingHtml();
  else if (s.profileScreen === 'edit') screen = profileEditHtml();

  return '<div class="ov ov--profile' + (s.profileClosing ? ' is-closing' : '') + '" data-mk="ovprofile" data-click="closeProfile">' +
    '<div class="sheet sheet--profile' + (s.profileClosing ? ' is-closing' : '') + '" data-click="stop">' +
      screen +
    '</div>' +
  '</div>';
}

function overlaysHtml() {
  const s = state;
  let out = '';
  if (s.deleteMediaOpen) out += deleteMediaHtml();
  if (s.forwardOpen) out += forwardSheetHtml();
  if (s.newOpen) out += newSheetHtml();
  if (s.groupOpen) out += groupSheetHtml();
  if (s.membersOpen) out += membersSheetHtml();
  if (s.infoOpen) out += infoSheetHtml();
  if (s.profileOpen) out += profileHtml();
  if (s.toast) {  // последним в слое окон и с наибольшим z-index — поверх любых модалок
    out += '<div class="toast' + (s.toastVisible ? ' is-visible' : '') + '" data-mk="toast">' + esc(s.toast) + '</div>';
  }
  return out;
}
