/* Pairing wire format. Only public request metadata may be rendered or copied. */
(function (root) {
  'use strict';
  const C = root.CX.crypto;
  const id = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const invalid = () => { throw new Error('Invalid pairing data. Generate a new QR and try again.'); };
  const object = (v) => v && typeof v === 'object' && !Array.isArray(v);

  async function parse(code) {
    if (typeof code !== 'string' || code.length > 2048) invalid();
    let value;
    try { value = JSON.parse(code.trim()); } catch (e) { invalid(); }
    if (!object(value) || Object.keys(value).sort().join(',') !== 'ephemeral_public_key,request_id,version' ||
        value.version !== 1 || !id.test(value.request_id) || typeof value.ephemeral_public_key !== 'string' ||
        value.ephemeral_public_key.length !== 124) invalid();
    const key = await C.importEcdhPub(value.ephemeral_public_key);
    if (C.b64(await crypto.subtle.exportKey('spki', key)) !== value.ephemeral_public_key) invalid();
    return { request_id: value.request_id, ephemeral_public_key: value.ephemeral_public_key };
  }

  // Whitelist transferable state: never send session credentials or decrypted message cache.
  function transfer(state) {
    const k = state.keys;
    return { v: 1, user_id: state.user_id,
      keys: { version: k.version, identityPkcs8: k.identityPkcs8, signingPkcs8: k.signingPkcs8,
        identityPub: k.identityPub, signingPub: k.signingPub },
      conv: state.conv || {}, trees: state.trees || {} };
  }

  async function validateKeys(k) {
    if (!object(k) || !Number.isInteger(k.version) || k.version < 1) invalid();
    for (const [privName, pubName, name, usage] of [
      ['identityPkcs8', 'identityPub', 'ECDH', 'deriveBits'],
      ['signingPkcs8', 'signingPub', 'ECDSA', 'sign'],
    ]) {
      if (typeof k[privName] !== 'string' || typeof k[pubName] !== 'string') invalid();
      const algorithm = { name, namedCurve: 'P-256' };
      const priv = await crypto.subtle.importKey('pkcs8', C.unb64(k[privName]), algorithm, true, [usage]);
      const pub = await crypto.subtle.importKey('spki', C.unb64(k[pubName]), algorithm, true, name === 'ECDSA' ? ['verify'] : []);
      const a = await crypto.subtle.exportKey('jwk', priv), b = await crypto.subtle.exportKey('jwk', pub);
      if (a.x !== b.x || a.y !== b.y) invalid();
    }
  }

  async function seal(scan, state, operationId, pinVerifier) {
    const binding = { ...scan, account_id: state.user_id, operation_id: operationId };
    return { ...binding, ciphertext: await C.eciesSeal(scan.ephemeral_public_key,
      C.utf8(JSON.stringify({ v: 1, ...binding, state: transfer(state), pin_verifier: pinVerifier }))) };
  }

  async function open(privateKey, scan, envelope) {
    if (!object(envelope) || envelope.request_id !== scan.request_id ||
        envelope.ephemeral_public_key !== scan.ephemeral_public_key ||
        !id.test(envelope.account_id) || !id.test(envelope.operation_id)) invalid();
    const plain = JSON.parse(C.fromUtf8(await C.eciesOpen(privateKey, envelope.ciphertext)));
    if (!object(plain) || plain.v !== 1 || !object(plain.pin_verifier) ||
        plain.pin_verifier.v !== 1 || typeof plain.pin_verifier.salt !== 'string' ||
        typeof plain.pin_verifier.iv !== 'string' || typeof plain.pin_verifier.ciphertext !== 'string') invalid();
    for (const field of ['request_id', 'ephemeral_public_key', 'account_id', 'operation_id']) {
      if (plain[field] !== envelope[field]) invalid();
    }
    const state = plain.state;
    if (!object(state) || state.v !== 1 || state.user_id !== envelope.account_id ||
        !object(state.conv) || !object(state.trees)) invalid();
    await validateKeys(state.keys);
    return { ...transfer(state), pin_verifier: plain.pin_verifier, refresh: null, cache: {} };
  }

  function svg(code) {
    const qr = qrcode(0, 'M');
    qr.addData(code, 'Byte'); qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 16, scalable: true, alt: 'Scan to connect this device' });
  }
  root.CX.pairing = { parse, seal, open, svg };
})(window);
