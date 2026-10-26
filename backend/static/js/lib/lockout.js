/* PIN-блокировка: срок хранится абсолютным временем, часы отображаются в часовом поясе браузера. */
(function (root) {
  'use strict';

  function message(until, now = Date.now()) {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const localTime = new Date(until).toLocaleTimeString('en-US', {
      hour: 'numeric', minute: '2-digit', timeZoneName: 'short', timeZone: zone,
    });
    const seconds = Math.max(0, Math.ceil((until - now) / 1000));
    return 'Too many wrong passcodes. Try again at ' + localTime +
      ' (' + Math.floor(seconds / 60) + 'm ' + seconds % 60 + 's left).';
  }

  root.CX = root.CX || {};
  root.CX.lockout = { message };
})(typeof window !== 'undefined' ? window : globalThis);
