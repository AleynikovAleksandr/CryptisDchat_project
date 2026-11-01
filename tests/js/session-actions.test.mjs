import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

function app({ postError = null } = {}) {
  const effects = { wiped: 0, locked: 0, redirects: [], requests: [], toasts: [] };
  const access = 'header.' + Buffer.from(JSON.stringify({ did: 'current' })).toString('base64url') + '.signature';
  const api = {
    session: { access, refresh: 'refresh' },
    post: async (path) => { effects.requests.push(path); if (postError) throw postError; },
    get: async () => [{ device_id: 'current', current: true }],
  };
  const context = vm.createContext({
    CX: { crypto: {}, api, vault: { wipe: async () => { effects.wiped++; }, lock: () => { effects.locked++; } }, ws: { stop() {} } },
    router: {}, state: { connection: 'online', sessions: [], messagesByThread: {} },
    document: { addEventListener() {} },
    window: { addEventListener() {} },
    setInterval() {}, clearInterval() {}, setTimeout() {}, clearTimeout() {},
    sessionStorage: { clear() {} }, location: { replace: (url) => effects.redirects.push(url) },
    atob: (s) => Buffer.from(s, 'base64').toString('binary'),
    showToast: (message) => effects.toasts.push(message), errorText: (e) => e.message,
    set: (value) => Object.assign(context.state, value),
    esc: (s) => s, relTime: () => 'now', I: { back: '' },
  });
  for (const file of ['data.js', 'handlers.js', 'views.js']) {
    vm.runInContext(readFileSync(new URL('../../backend/static/js/app/' + file, import.meta.url), 'utf8'), context);
  }
  return { context, effects, api, run: (code) => vm.runInContext(code, context) };
}

test('last-device rejection keeps local credentials and vault', async () => {
  const a = app({ postError: { status: 409, code: 'last_device', message: 'Connect another device first.' } });
  await a.run('H.logout()');
  assert.equal(a.effects.wiped, 0);
  assert.equal(a.effects.locked, 0);
  assert.equal(a.api.session.refresh, 'refresh');
  assert.deepEqual(a.effects.redirects, []);
  assert.deepEqual(a.effects.toasts, ['Connect another device first.']);
});

test('bulk logout keeps caller unlocked and reloads sessions', async () => {
  const a = app();
  await a.run('H.logoutAll()');
  assert.deepEqual(a.effects.requests, ['/api/auth/logout-all']);
  assert.equal(a.effects.wiped, 0);
  assert.equal(a.effects.locked, 0);
  assert.equal(a.api.session.refresh, 'refresh');
  assert.deepEqual(a.effects.redirects, []);
  assert.equal(a.context.state.sessions[0].device_id, 'current');
});

test('successful single logout removes local vault', async () => {
  const a = app();
  await a.run('H.logout()');
  assert.equal(a.effects.wiped, 1);
  assert.equal(a.api.session.refresh, null);
  assert.deepEqual(a.effects.redirects, ['/login?reauth=1']);
});

test('revocation of another device leaves current session open', () => {
  const a = app();
  a.run("onWsFrame({ event: 'session.revoked' }, { device_id: 'other' })");
  assert.deepEqual(a.effects.redirects, []);
  assert.equal(a.effects.locked, 0);
});

test('revocation of current or every device ends current session', () => {
  for (const id of ['current', '*']) {
    const a = app();
    a.run(`onWsFrame({ event: 'session.revoked' }, { device_id: '${id}' })`);
    assert.deepEqual(a.effects.redirects, ['/login?reauth=1']);
    assert.equal(a.effects.wiped, 0);
  }
});

test('last-session action is disabled with an explanation', () => {
  const a = app();
  a.context.state.sessions = [{ device_id: 'current', current: true, name: 'iPadOS tablet · Safari' }];
  const html = a.run('sessionsHtml()');
  assert.match(html, /<button[^>]*disabled[^>]*>End session<\/button>/);
  assert.match(html, /last active device/i);
  assert.match(html, /Log out of other devices/);
  assert.match(html, /iPadOS tablet · Safari/);
});
