import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const staticJs = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'backend', 'static', 'js');
const values = new Map();
globalThis.localStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: (key) => values.delete(key),
};
globalThis.CX = {};
for (const file of ['lib/crypto.js', 'lib/vault.js', 'lib/lockout.js']) {
  vm.runInThisContext(readFileSync(join(staticJs, file), 'utf8'), { filename: file });
}

const vault = CX.vault;
let now = Date.parse('2026-10-26T00:00:00Z');
Date.now = () => now;
await vault.create('1234', { user_id: 'u1', secret: 'preserved' });
vault.lock();
for (let i = 1; i < 10; i++) {
  await assert.rejects(vault.unlock('0000'), /wrong_pin/);
  assert.equal(vault.attemptsLeft(), 10 - i);
}
await assert.rejects(vault.unlock('0000'), /locked/);
assert.equal(vault.lockedUntil(), now + 15 * 60 * 1000);
assert.equal(await vault.exists(), true);
await assert.rejects(vault.unlock('1234'), /locked/);
assert.equal(vault.attemptsLeft(), 0);
vm.runInThisContext(readFileSync(join(staticJs, 'lib', 'vault.js'), 'utf8'), { filename: 'lib/vault.js' });
const reloadedVault = CX.vault;
await assert.rejects(reloadedVault.unlock('1234'), /locked/);

process.env.TZ = 'Asia/Vladivostok';
const message = CX.lockout.message(vault.lockedUntil(), now);
assert.match(message, /10:15 AM/);
assert.match(message, /15m 0s/);

globalThis.state = {
  gate: { mode: 'unlock', pin: '', lockedUntil: vault.lockedUntil() },
  pc: { step: 'current', value: '', lockedUntil: vault.lockedUntil() },
};
globalThis.I = { lock: '', lockSm: '', back: '' };
globalThis.esc = (value) => String(value);
vm.runInThisContext(readFileSync(join(staticJs, 'app', 'views.js'), 'utf8'), { filename: 'app/views.js' });
assert.match(gateHtml(), /id="dc-gate-input"[^>]* disabled/);
assert.match(passcodeHtml(), /id="dc-passcode-input"[^>]* disabled/);

now += 15 * 60 * 1000;
assert.doesNotMatch(gateHtml(), /id="dc-gate-input"[^>]* disabled/);
assert.deepEqual(await reloadedVault.unlock('1234'), { user_id: 'u1', secret: 'preserved' });
assert.equal(reloadedVault.attemptsLeft(), 10);
assert.equal(reloadedVault.lockedUntil(), 0);
