import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

function detect(ua, touchPoints = 0) {
  const context = vm.createContext({});
  vm.runInContext(readFileSync(new URL('../../backend/static/js/lib/device-info.js', import.meta.url), 'utf8'), context);
  return JSON.parse(JSON.stringify(context.CX.deviceInfo.detect(ua, touchPoints)));
}

test('detects_android_mobile', () => {
  assert.deepEqual(detect('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36'), {
    platform: 'Android', deviceType: 'phone', browser: 'Chrome', label: 'Android phone · Chrome',
  });
  assert.deepEqual(detect('Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36'), {
    platform: 'Android', deviceType: 'tablet', browser: 'Chrome', label: 'Android tablet · Chrome',
  });
});

test('detects_ios', () => {
  for (const device of ['iPhone', 'iPod']) {
    assert.deepEqual(detect(`Mozilla/5.0 (${device}; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1`), {
      platform: 'iOS', deviceType: 'phone', browser: 'Safari', label: 'iOS phone · Safari',
    });
  }
  assert.deepEqual(detect('Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1'), {
    platform: 'iPadOS', deviceType: 'tablet', browser: 'Safari', label: 'iPadOS tablet · Safari',
  });
});

test('detects_ipados_masquerading_as_mac', () => {
  assert.deepEqual(detect('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15', 5), {
    platform: 'iPadOS', deviceType: 'tablet', browser: 'Safari', label: 'iPadOS tablet · Safari',
  });
});

test('detects_desktop_operating_systems', () => {
  const cases = [
    ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0', 'Windows', 'Edge'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15', 'macOS', 'Safari'],
    ['Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0', 'Linux', 'Firefox'],
  ];
  for (const [ua, platform, browser] of cases) {
    assert.deepEqual(detect(ua), { platform, deviceType: 'desktop', browser, label: `${platform} computer · ${browser}` });
  }
});

test('falls_back_for_unknown_user_agent', () => {
  for (const ua of ['', 'Unrecognized client', 'Mozilla/5.0 Chrome/124.0.0.0']) {
    const info = detect(ua);
    assert.equal(info.platform, 'unknown');
    assert.equal(info.deviceType, 'unknown');
    assert.equal(info.label, 'Web browser');
  }
});
