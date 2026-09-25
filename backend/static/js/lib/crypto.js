/* Клиентская криптография CryptisDchat (ТЗ, раздел 6).
 *
 *  Identity keypair   — ECDH P-256: получение обёрнутых Conversation key (ECIES);
 *  Signing keypair    — ECDSA P-256: подпись каждого исходящего шифротекста;
 *  Conversation key   — 32 байта: XSalsa20-Poly1305 (nacl.secretbox) для содержимого чата;
 *  ECIES              — эфемерный ECDH + HKDF-SHA256 + AES-128-GCM (формат = realm/ecies.py);
 *  резервная копия    — PBKDF2(PIN) → доступ к realm; ключ K делится по Шамиру 2-из-3 (GF(256)).
 *
 * Все форматы байт-в-байт совпадают с серверной/эталонной реализацией (tests/client_crypto.py).
 */
(function (root) {
  'use strict';

  const subtle = root.crypto.subtle;
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const ECDH = { name: 'ECDH', namedCurve: 'P-256' };
  const ECDSA = { name: 'ECDSA', namedCurve: 'P-256' };
  const ECIES_INFO = enc.encode('cryptis-ecies-v1');
  const SEARCH_INFO = enc.encode('cryptis-search-v1');

  /* ---------- байты ---------- */
  const utf8 = (s) => enc.encode(s);
  const fromUtf8 = (b) => dec.decode(b);
  function b64(bytes) {
    bytes = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function unb64(str) {
    const s = atob(str);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  const hex = (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, '0')).join('');
  const unhex = (h) => Uint8Array.from(h.match(/.{2}/g) || [], (x) => parseInt(x, 16));
  function concat(...parts) {
    const len = parts.reduce((n, p) => n + p.length, 0);
    const out = new Uint8Array(len);
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }
  const random = (n) => root.crypto.getRandomValues(new Uint8Array(n));
  const uuid = () => (root.crypto.randomUUID ? root.crypto.randomUUID() : (() => {
    const b = random(16); b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = hex(b); return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  })());
  const sha256 = async (bytes) => new Uint8Array(await subtle.digest('SHA-256', bytes));

  /* ---------- ключи устройства (6.1) ---------- */
  async function generateDeviceKeys() {
    const identity = await subtle.generateKey(ECDH, true, ['deriveBits']);
    const signing = await subtle.generateKey(ECDSA, true, ['sign', 'verify']);
    return {
      identity, signing,
      identityPub: b64(await subtle.exportKey('spki', identity.publicKey)),
      signingPub: b64(await subtle.exportKey('spki', signing.publicKey)),
      identityPkcs8: b64(await subtle.exportKey('pkcs8', identity.privateKey)),
      signingPkcs8: b64(await subtle.exportKey('pkcs8', signing.privateKey)),
    };
  }
  const importEcdhPriv = (pkcs8B64) => subtle.importKey('pkcs8', unb64(pkcs8B64), ECDH, false, ['deriveBits']);
  const importEcdsaPriv = (pkcs8B64) => subtle.importKey('pkcs8', unb64(pkcs8B64), ECDSA, false, ['sign']);
  const importEcdhPub = (spkiB64) => subtle.importKey('spki', unb64(spkiB64), ECDH, true, []);
  const importEcdsaPub = (spkiB64) => subtle.importKey('spki', unb64(spkiB64), ECDSA, false, ['verify']);

  /* ---------- подпись пакета (6.2) ---------- */
  function signingBytes(threadId, clientMsgId, epoch, nonce, ciphertext) {
    const e = new Uint8Array(4);
    new DataView(e.buffer).setUint32(0, epoch, false);
    return concat(utf8('cryptis-msg-v1\u0000' + threadId + '\u0000' + clientMsgId + '\u0000'), e, nonce, ciphertext);
  }
  async function sign(privKey, data) {
    return new Uint8Array(await subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, privKey, data)); // r||s, 64 байта
  }
  async function verify(pubSpkiB64, signature, data) {
    try {
      const key = await importEcdsaPub(pubSpkiB64);
      return await subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, signature, data);
    } catch (e) { return false; }
  }

  /* ---------- XSalsa20-Poly1305 (6.2) ---------- */
  function secretboxSeal(key, plaintext) {
    const nonce = random(24);
    return { nonce, ciphertext: root.nacl.secretbox(plaintext, nonce, key) };
  }
  function secretboxOpen(key, nonce, ciphertext) {
    const out = root.nacl.secretbox.open(ciphertext, nonce, key);
    if (!out) throw new Error('decryption failed');
    return out;
  }

  /* ---------- ECIES (6.3) ---------- */
  async function eciesKey(sharedBits, ephRaw, usage) {
    const ikm = await subtle.importKey('raw', sharedBits, 'HKDF', false, ['deriveKey']);
    return subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: ephRaw, info: ECIES_INFO }, ikm,
      { name: 'AES-GCM', length: 128 }, false, [usage]);
  }
  async function eciesSeal(recipientSpkiB64, plaintext) {
    const recipient = await importEcdhPub(recipientSpkiB64);
    const eph = await subtle.generateKey(ECDH, true, ['deriveBits']);
    const ephRaw = new Uint8Array(await subtle.exportKey('raw', eph.publicKey));
    const shared = await subtle.deriveBits({ name: 'ECDH', public: recipient }, eph.privateKey, 256);
    const key = await eciesKey(shared, ephRaw, 'encrypt');
    const iv = random(12);
    const ct = new Uint8Array(await subtle.encrypt({ name: 'AES-GCM', iv }, key, plaintext));
    return b64(concat(Uint8Array.of(1), ephRaw, iv, ct));
  }
  async function eciesOpen(privKey, blobB64) {
    const blob = unb64(blobB64);
    if (blob.length < 94 || blob[0] !== 1) throw new Error('bad ECIES blob');
    const ephRaw = blob.subarray(1, 66), iv = blob.subarray(66, 78), ct = blob.subarray(78);
    const eph = await subtle.importKey('raw', ephRaw, ECDH, false, []);
    const shared = await subtle.deriveBits({ name: 'ECDH', public: eph }, privKey, 256);
    const key = await eciesKey(shared, ephRaw, 'decrypt');
    return new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv }, key, ct));
  }

  /* ---------- слепой индекс для поиска ---------- */
  async function searchKey(convKey) {
    const ikm = await subtle.importKey('raw', convKey, 'HKDF', false, ['deriveBits']);
    const bits = await subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(0), info: SEARCH_INFO }, ikm, 256);
    return subtle.importKey('raw', bits, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  }
  async function searchToken(hmacKey, word) {
    return hex(await subtle.sign('HMAC', hmacKey, utf8(word.toLowerCase()))).slice(0, 32);
  }
  function words(text) {
    return Array.from(new Set(
      String(text || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').split(' ').filter((w) => w.length >= 2)
    ));
  }
  async function wordTokens(convKey, text) {
    const key = await searchKey(convKey);
    const prefixes = new Set();
    for (const w of words(text)) for (let n = 2; n <= Math.min(w.length, 12); n++) prefixes.add(w.slice(0, n));
    const out = [];
    for (const p of prefixes) out.push(await searchToken(key, p));
    return out.sort();
  }

  /* ---------- AES-GCM, PBKDF2, HMAC ---------- */
  async function aesGcmSeal(keyBytes, plaintext) {
    const key = await subtle.importKey('raw', keyBytes, 'AES-GCM', false, ['encrypt']);
    const iv = random(12);
    return concat(iv, new Uint8Array(await subtle.encrypt({ name: 'AES-GCM', iv }, key, plaintext)));
  }
  async function aesGcmOpen(keyBytes, blob) {
    const key = await subtle.importKey('raw', keyBytes, 'AES-GCM', false, ['decrypt']);
    return new Uint8Array(await subtle.decrypt({ name: 'AES-GCM', iv: blob.subarray(0, 12) }, key, blob.subarray(12)));
  }
  async function pbkdf2(pin, salt, iterations) {
    const base = await subtle.importKey('raw', utf8(pin), 'PBKDF2', false, ['deriveBits']);
    return new Uint8Array(await subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, base, 256));
  }
  async function hmac(keyBytes, message) {
    const key = await subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return new Uint8Array(await subtle.sign('HMAC', key, message));
  }
  const realmAuth = (pinKey, realmIndex) => hmac(pinKey, utf8('realm-auth:' + realmIndex));

  /* ---------- Шамир 2-из-3 над GF(256), полином 0x11B ---------- */
  function gfMul(a, b) {
    let p = 0;
    while (b) {
      if (b & 1) p ^= a;
      a <<= 1;
      if (a & 0x100) a ^= 0x11b;
      b >>= 1;
    }
    return p;
  }
  function gfInv(a) { for (let x = 1; x < 256; x++) if (gfMul(a, x) === 1) return x; throw new Error('no inverse'); }
  function shamirSplit(secret, n, k) {
    const shares = Array.from({ length: n }, (_, i) => [i + 1]);
    for (const byte of secret) {
      const coeffs = [byte, ...random(k - 1)];
      for (let i = 0; i < n; i++) {
        let acc = 0, power = 1;
        for (const c of coeffs) { acc ^= gfMul(c, power); power = gfMul(power, i + 1); }
        shares[i].push(acc);
      }
    }
    return shares.map((s) => Uint8Array.from(s));
  }
  function shamirCombine(shares) {
    const xs = shares.map((s) => s[0]);
    const out = new Uint8Array(shares[0].length - 1);
    for (let pos = 1; pos < shares[0].length; pos++) {
      let acc = 0;
      shares.forEach((s, i) => {
        let num = 1, den = 1;
        xs.forEach((xj, j) => { if (i !== j) { num = gfMul(num, xj); den = gfMul(den, xs[i] ^ xj); } });
        acc ^= gfMul(s[pos], gfMul(num, gfInv(den)));
      });
      out[pos - 1] = acc;
    }
    return out;
  }

  /* ---------- проверка Merkle-proof из блокчейна (раздел 2) ---------- */
  async function merkleVerify(eventHashHex, proof, rootHex) {
    let acc = await sha256(concat(Uint8Array.of(0), unhex(eventHashHex)));
    for (const step of proof || []) {
      const sib = unhex(step.hash);
      acc = await sha256(step.pos === 'L' ? concat(Uint8Array.of(1), sib, acc) : concat(Uint8Array.of(1), acc, sib));
    }
    return hex(acc) === rootHex;
  }

  root.CX = root.CX || {};
  root.CX.crypto = {
    utf8, fromUtf8, b64, unb64, hex, unhex, concat, random, uuid, sha256,
    generateDeviceKeys, importEcdhPriv, importEcdsaPriv, importEcdhPub, importEcdsaPub,
    signingBytes, sign, verify, secretboxSeal, secretboxOpen, eciesSeal, eciesOpen,
    searchKey, searchToken, words, wordTokens, aesGcmSeal, aesGcmOpen, pbkdf2, hmac, realmAuth,
    shamirSplit, shamirCombine, merkleVerify,
  };
})(typeof window !== 'undefined' ? window : globalThis);
