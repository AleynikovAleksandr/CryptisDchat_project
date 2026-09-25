/* Кодек wire-формата Protocol Buffers для proto/cryptis.proto (зеркало backend/app/proto/wire.py). */
(function (root) {
  'use strict';

  const SCHEMA = {
    MessagePacket: [
      [1, 'client_msg_id', 'string'], [2, 'thread_id', 'string'], [3, 'key_epoch', 'uint32'],
      [4, 'sender_key_version', 'uint32'], [5, 'nonce', 'bytes'], [6, 'ciphertext', 'bytes'],
      [7, 'signature', 'bytes'], [8, 'kind', 'string'], [9, 'reply_to_id', 'string'],
      [10, 'attachment_id', 'string'], [11, 'search_tokens', 'string', true], [12, 'push_preview', 'string'],
      [13, 'forwarded_from_id', 'string'],
    ],
    WsFrame: [
      [1, 'event', 'string'], [2, 'thread_id', 'string'], [3, 'message_id', 'string'], [4, 'seq', 'uint64'],
      [5, 'sender_id', 'string'], [6, 'ts', 'uint64'], [7, 'packet', 'message:MessagePacket'], [8, 'json', 'string'],
    ],
  };

  const enc = new TextEncoder();
  const dec = new TextDecoder('utf-8', { fatal: true });

  function pushVarint(out, value) {
    // числа до 2^53 — без побитовых операций, которые в JS 32-битные
    let v = Number(value);
    if (!Number.isSafeInteger(v) || v < 0) throw new Error('bad varint');
    while (v >= 0x80) {
      out.push((v % 0x80) | 0x80);
      v = Math.floor(v / 0x80);
    }
    out.push(v);
  }

  function readVarint(buf, pos) {
    let result = 0, mul = 1;
    for (;;) {
      if (pos >= buf.length) throw new Error('truncated varint');
      const b = buf[pos++];
      result += (b & 0x7f) * mul;
      if (!(b & 0x80)) return [result, pos];
      mul *= 0x80;
      if (mul > 2 ** 63) throw new Error('varint too long');
    }
  }

  function encode(type, data) {
    const out = [];
    for (const [num, name, ftype, repeated] of SCHEMA[type]) {
      const value = data[name];
      if (value === undefined || value === null) continue;
      const values = repeated ? value : [value];
      for (const v of values) {
        if (ftype === 'uint32' || ftype === 'uint64') {
          if (!v) continue;
          pushVarint(out, num * 8);
          pushVarint(out, v);
          continue;
        }
        let payload;
        if (ftype === 'string') {
          if (v === '' && !repeated) continue;
          payload = enc.encode(String(v));
        } else if (ftype === 'bytes') {
          if (!v || !v.length) continue;
          payload = v;
        } else {
          payload = encode(ftype.split(':')[1], v);
        }
        pushVarint(out, num * 8 + 2);
        pushVarint(out, payload.length);
        for (let i = 0; i < payload.length; i++) out.push(payload[i]);
      }
    }
    return Uint8Array.from(out);
  }

  function decode(type, buf) {
    buf = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    const fields = {};
    const result = {};
    for (const [num, name, ftype, repeated] of SCHEMA[type]) {
      fields[num] = [name, ftype, repeated];
      result[name] = repeated ? [] : ftype.startsWith('uint') ? 0 : ftype === 'string' ? '' :
        ftype === 'bytes' ? new Uint8Array(0) : null;
    }
    let pos = 0;
    while (pos < buf.length) {
      let key;
      [key, pos] = readVarint(buf, pos);
      const num = Math.floor(key / 8), wire = key % 8;
      let value;
      if (wire === 0) {
        [value, pos] = readVarint(buf, pos);
      } else if (wire === 2) {
        let len;
        [len, pos] = readVarint(buf, pos);
        if (pos + len > buf.length) throw new Error('truncated field');
        value = buf.subarray(pos, pos + len);
        pos += len;
      } else if (wire === 1) { pos += 8; continue; }
      else if (wire === 5) { pos += 4; continue; }
      else throw new Error('unsupported wire type ' + wire);
      if (!fields[num]) continue;
      const [name, ftype, repeated] = fields[num];
      let decoded;
      if (ftype.startsWith('uint')) decoded = value;
      else if (ftype === 'string') decoded = dec.decode(value);
      else if (ftype === 'bytes') decoded = new Uint8Array(value);
      else decoded = decode(ftype.split(':')[1], value);
      if (repeated) result[name].push(decoded); else result[name] = decoded;
    }
    return result;
  }

  root.CX = root.CX || {};
  root.CX.proto = { encode, decode, SCHEMA };
})(typeof window !== 'undefined' ? window : globalThis);
