/* Экран входа (ТЗ 4.1–4.2): TonConnect + ton_proof.
 *
 * 1. «Connect TON Wallet» → сервер выдаёт одноразовый payload;
 * 2. кошелёк (Tonkeeper, MyTonWallet, TON Space в Telegram …) подписывает его при подключении;
 * 3. сервер проверяет подпись и выдаёт JWT + Refresh; управление переходит в /app,
 *    где пользователь задаёт или вводит PIN (ключи шифрования переписки, ТЗ 6).
 *
 * Dev-режим (DEV_WALLET_LOGIN=true на сервере): вместо внешнего кошелька используется
 * локальный Ed25519-ключ браузера, подпись ton_proof проверяется сервером так же строго.
 * ?dev=new — создать ещё одного тестового пользователя.
 */
(function () {
  'use strict';

  const C = window.CX.crypto;
  const form = document.getElementById('login-form');
  const errorEl = document.getElementById('login-error');
  const btn = document.getElementById('submit-btn');
  const DEFAULT_LABEL = btn.textContent;
  const TIMEOUT_MS = 120000;
  const params = new URLSearchParams(location.search);
  let tonConnect = null;
  let timer = null;
  let errTimer = null;

  function waiting() {
    clearTimeout(errTimer);
    errorEl.style.display = 'none';
    btn.disabled = true;
    btn.style.opacity = '0.7';
    btn.textContent = 'Waiting for wallet confirmation…';
    clearTimeout(timer);
    timer = setTimeout(fail, TIMEOUT_MS);
  }

  function reset() {
    clearTimeout(timer);
    btn.disabled = false;
    btn.style.opacity = '1';
    btn.textContent = DEFAULT_LABEL;
  }

  function fail(reason) {
    if (reason) console.warn('login failed:', reason);
    reset();
    errorEl.style.display = 'block';
    clearTimeout(errTimer);
    errTimer = setTimeout(() => { errorEl.style.display = 'none'; }, 4000);
  }

  async function challenge() {
    const r = await fetch('/api/auth/ton-proof/challenge', { method: 'POST' });
    if (!r.ok) throw new Error('challenge ' + r.status);
    return r.json();
  }

  async function verify(body) {
    body.device_name = deviceName();
    const r = await fetch('/api/auth/ton-proof/verify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'verify ' + r.status);
    // токены передаются странице приложения через sessionStorage вкладки;
    // там refresh-токен сразу переезжает в зашифрованное PIN-хранилище (ТЗ 4.2, 6.5)
    sessionStorage.setItem('cx_login', JSON.stringify({
      access: data.access_token, refresh: data.refresh_token, user: data.user,
      is_new_user: data.is_new_user, at: Date.now(),
    }));
    location.replace('/app');
  }

  function deviceName() {
    const ua = navigator.userAgent;
    const browser = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox'
      : /Safari\//.test(ua) ? 'Safari' : 'Browser';
    const os = /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android'
      : /iPhone|iPad/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : '';
    return (browser + (os ? ' on ' + os : '')).slice(0, 100);
  }

  /* ---------------- TonConnect ---------------- */
  // UI-библиотека TonConnect грузится лениво, чтобы медленный CDN не задерживал экран входа
  const TC_SOURCES = [
    'https://cdn.jsdelivr.net/npm/@tonconnect/ui@3.0.2/dist/tonconnect-ui.min.js',
    'https://unpkg.com/@tonconnect/ui@3.0.2/dist/tonconnect-ui.min.js',
  ];
  let tcLoading = null;
  function loadTonConnect() {
    if (window.TON_CONNECT_UI) return Promise.resolve();
    if (!tcLoading) {
      tcLoading = TC_SOURCES.reduce((prev, src) => prev.catch(() => new Promise((ok, fail) => {
        const el = document.createElement('script');
        el.src = src;
        el.async = true;
        el.onload = ok;
        el.onerror = () => { el.remove(); fail(new Error('cannot load ' + src)); };
        document.head.appendChild(el);
      })), Promise.reject(new Error('start'))).catch((e) => { tcLoading = null; throw e; });
    }
    return tcLoading;
  }

  function getTonConnect() {
    if (!tonConnect && window.TON_CONNECT_UI) {
      tonConnect = new window.TON_CONNECT_UI.TonConnectUI({
        manifestUrl: location.origin + '/tonconnect-manifest.json',
      });
    }
    return tonConnect;
  }

  async function tonConnectLogin(ch) {
    await loadTonConnect();
    const ui = getTonConnect();
    if (!ui) throw new Error('TonConnect UI is not loaded');
    if (ui.connected) await ui.disconnect();
    ui.setConnectRequestParameters({ state: 'ready', value: { tonProof: ch.payload } });

    let done = false;
    const unsubscribe = ui.onStatusChange(async (wallet) => {
      if (!wallet || done) return;
      done = true;
      unsubscribe();
      const item = wallet.connectItems && wallet.connectItems.tonProof;
      if (!item || !('proof' in item)) { fail('wallet did not return ton_proof'); return; }
      try {
        await verify({
          address: wallet.account.address,
          network: wallet.account.chain,
          public_key: String(wallet.account.publicKey || '').replace(/^0x/, ''),
          proof: Object.assign({}, item.proof, { state_init: wallet.account.walletStateInit }),
        });
      } catch (e) {
        fail(e.message);
        try { await ui.disconnect(); } catch (err) { /* уже отключён */ }
      }
    }, (err) => { if (!done) { done = true; fail(err && err.message); } });

    ui.onModalStateChange((state) => {
      if (!done && state.status === 'closed' && state.closeReason === 'action-cancelled') {
        done = true;
        unsubscribe();
        fail('cancelled');
      }
    });
    await ui.openModal();
  }

  /* ---------------- dev-кошелёк ---------------- */
  function devSeed() {
    let seed = params.get('dev') === 'new' ? null : localStorage.getItem('cx_dev_wallet_seed');
    if (!seed) {
      seed = C.b64(C.random(32));
      localStorage.setItem('cx_dev_wallet_seed', seed);
    }
    return C.unb64(seed);
  }

  async function devLogin(ch) {
    const kp = window.nacl.sign.keyPair.fromSeed(devSeed());
    const addrHash = await C.sha256(kp.publicKey);
    const ts = Math.floor(Date.now() / 1000);
    const domain = C.utf8(ch.domain);
    const head = new Uint8Array(4 + 32 + 4);
    const dv = new DataView(head.buffer);
    dv.setInt32(0, 0, false);                 // workchain 0, big-endian
    head.set(addrHash, 4);
    dv.setUint32(36, domain.length, true);    // длина домена, little-endian
    const tsBytes = new Uint8Array(8);
    new DataView(tsBytes.buffer).setUint32(0, ts, true);
    const message = C.concat(C.utf8('ton-proof-item-v2/'), head, domain, tsBytes, C.utf8(ch.payload));
    const digest = await C.sha256(C.concat(Uint8Array.of(0xff, 0xff), C.utf8('ton-connect'), await C.sha256(message)));
    const signature = window.nacl.sign.detached(digest, kp.secretKey);
    await verify({
      address: '0:' + C.hex(addrHash),
      network: '-239',
      public_key: C.hex(kp.publicKey),
      proof: { timestamp: ts, domain: { lengthBytes: domain.length, value: ch.domain }, signature: C.b64(signature),
               payload: ch.payload },
    });
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    waiting();
    try {
      const ch = await challenge();
      // локальная разработка: реальные кошельки не подключаются к http://localhost, поэтому dev-ключ
      if (ch.dev_wallet_login) await devLogin(ch);
      else await tonConnectLogin(ch);
    } catch (err) {
      fail(err && err.message);
    }
  });

  // заранее подгрузить TonConnect в фоне, уже после отрисовки экрана
  window.addEventListener('load', () => {
    setTimeout(() => loadTonConnect().catch(() => {}), 0);
  });

  // уже есть локальное зашифрованное хранилище — сразу к разблокировке по PIN
  (async () => {
    if (!params.has('reauth') && !params.has('dev') && await window.CX.vault.exists()) location.replace('/app');
  })();
})();
