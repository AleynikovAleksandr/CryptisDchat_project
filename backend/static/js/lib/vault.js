/* Локальное зашифрованное хранилище (ТЗ 6.5).
 *
 * Приватные ключи, Conversation keys, ключи дерева групп, refresh-токен и кэш
 * расшифрованных сообщений лежат в Origin Private File System одним файлом,
 * зашифрованным AES-256-GCM ключом из PBKDF2(PIN). PIN на сервер не уходит.
 * Если OPFS недоступна (старый браузер), используется localStorage с тем же шифрованием.
 * При выходе из аккаунта файл удаляется безвозвратно.
 */
(function (root) {
  'use strict';

  const C = () => root.CX.crypto;
  const FILE = 'cryptis-vault.bin';
  const LS_KEY = 'cx_vault';
  const META = 'cx_vault_meta';      // незашифрованное: user_id и счётчик неверных PIN
  const ITERATIONS = 310000;
  const MAX_LOCAL_ATTEMPTS = 10;

  let key = null;       // CryptoKey AES-GCM, живёт только пока хранилище разблокировано
  let salt = null;
  let saveTimer = null;
  let pending = null;

  async function opfs() {
    try {
      if (!root.navigator || !navigator.storage || !navigator.storage.getDirectory) return null;
      return await navigator.storage.getDirectory();
    } catch (e) { return null; }
  }

  async function readRaw() {
    const dir = await opfs();
    if (dir) {
      try {
        const fh = await dir.getFileHandle(FILE);
        return await (await fh.getFile()).text();
      } catch (e) { /* файла нет — пробуем localStorage */ }
    }
    try { return localStorage.getItem(LS_KEY); } catch (e) { return null; }
  }

  async function writeRaw(text) {
    const dir = await opfs();
    if (dir) {
      try {
        const fh = await dir.getFileHandle(FILE, { create: true });
        const w = await fh.createWritable();
        await w.write(text);
        await w.close();
        return;
      } catch (e) { /* OPFS без createWritable (Safari) — запасной путь */ }
    }
    localStorage.setItem(LS_KEY, text);
  }

  function meta() {
    try { return JSON.parse(localStorage.getItem(META) || '{}'); } catch (e) { return {}; }
  }
  function setMeta(m) { localStorage.setItem(META, JSON.stringify(m)); }

  async function deriveKey(pin, saltBytes) {
    const bits = await C().pbkdf2(pin, saltBytes, ITERATIONS);
    return root.crypto.subtle.importKey('raw', bits, 'AES-GCM', false, ['encrypt', 'decrypt']);
  }

  async function encryptState(state) {
    const iv = C().random(12);
    const pt = C().utf8(JSON.stringify(state));
    const ct = new Uint8Array(await root.crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, pt));
    return JSON.stringify({ v: 1, salt: C().b64(salt), iter: ITERATIONS, iv: C().b64(iv), ct: C().b64(ct) });
  }

  const vault = {
    async exists() { return !!(await readRaw()); },
    userId() { return meta().user_id || null; },
    attemptsLeft() { return MAX_LOCAL_ATTEMPTS - (meta().failed || 0); },
    isUnlocked() { return !!key; },

    async create(pin, state) {
      salt = C().random(16);
      key = await deriveKey(pin, salt);
      await writeRaw(await encryptState(state));
      setMeta({ user_id: state.user_id, failed: 0 });
    },

    async unlock(pin) {
      const raw = await readRaw();
      if (!raw) throw new Error('no_vault');
      const box = JSON.parse(raw);
      const s = C().unb64(box.salt);
      const k = await deriveKey(pin, s);
      try {
        const pt = await root.crypto.subtle.decrypt({ name: 'AES-GCM', iv: C().unb64(box.iv) }, k, C().unb64(box.ct));
        key = k; salt = s;
        setMeta({ ...meta(), failed: 0 });
        return JSON.parse(C().fromUtf8(new Uint8Array(pt)));
      } catch (e) {
        const m = meta();
        m.failed = (m.failed || 0) + 1;
        setMeta(m);
        if (m.failed >= MAX_LOCAL_ATTEMPTS) {  // защита от перебора PIN на украденном устройстве
          await vault.wipe();
          throw new Error('wiped');
        }
        throw new Error('wrong_pin');
      }
    },

    /* Сохранение с дебаунсом: состояние меняется на каждое сообщение. */
    save(state, immediate) {
      if (!key) return Promise.resolve();
      pending = state;
      clearTimeout(saveTimer);
      const run = async () => {
        const s = pending;
        pending = null;
        if (s && key) await writeRaw(await encryptState(s));
      };
      if (immediate) return run();
      saveTimer = setTimeout(run, 800);
      return Promise.resolve();
    },

    async changePin(newPin, state) {
      salt = C().random(16);
      key = await deriveKey(newPin, salt);
      await writeRaw(await encryptState(state));
    },

    lock() { key = null; salt = null; clearTimeout(saveTimer); pending = null; },

    async wipe() {
      vault.lock();
      const dir = await opfs();
      if (dir) { try { await dir.removeEntry(FILE); } catch (e) { /* нечего удалять */ } }
      try { localStorage.removeItem(LS_KEY); localStorage.removeItem(META); } catch (e) { /* ignore */ }
    },
  };

  root.CX = root.CX || {};
  root.CX.vault = vault;
})(typeof window !== 'undefined' ? window : globalThis);
