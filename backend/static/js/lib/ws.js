/* Постоянное WebSocket-соединение (ТЗ 6.4): бинарные кадры WsFrame, авторизация первым кадром,
 * heartbeat для присутствия, переподключение с экспоненциальной задержкой. */
(function (root) {
  'use strict';

  const HEARTBEAT_MS = 25000;
  let ws = null, hb = null, retry = 0, stopped = true, handlers = null;

  function url() {
    return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';
  }

  function send(event, fields) {
    if (!ws || ws.readyState !== 1) return false;
    ws.send(root.CX.proto.encode('WsFrame', Object.assign({ event }, fields || {})));
    return true;
  }

  function sendAuth() {
    send('auth', { json: JSON.stringify({ token: root.CX.api.session.access }) });
  }

  function open() {
    if (stopped) return;
    ws = new WebSocket(url());
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { sendAuth(); };
    ws.onmessage = (e) => {
      let frame;
      try { frame = root.CX.proto.decode('WsFrame', new Uint8Array(e.data)); } catch (err) { return; }
      if (frame.event === 'ready') {
        retry = 0;
        clearInterval(hb);
        hb = setInterval(() => send('heartbeat'), HEARTBEAT_MS);
        send('heartbeat');
      }
      let data = {};
      if (frame.json) { try { data = JSON.parse(frame.json); } catch (err) { data = {}; } }
      if (handlers && handlers.onFrame) handlers.onFrame(frame, data);
    };
    ws.onclose = async (e) => {
      clearInterval(hb);
      ws = null;
      if (stopped) return;
      if (e.code === 4001) { if (handlers.onRevoked) handlers.onRevoked(); return; }
      if (e.code === 4003 || e.code === 1008) {
        try { await root.CX.api.refresh(); } catch (err) { return; }
      }
      const delay = Math.min(30000, 500 * 2 ** retry++) + Math.random() * 400;
      setTimeout(open, delay);
      if (handlers.onReconnecting) handlers.onReconnecting();
    };
  }

  root.CX = root.CX || {};
  root.CX.ws = {
    start(h) { handlers = h; stopped = false; retry = 0; open(); },
    stop() { stopped = true; clearInterval(hb); if (ws) ws.close(1000); ws = null; },
    send,
    reauth: sendAuth,  // после обновления JWT — продлеваем сессию сокета без переподключения
  };
})(typeof window !== 'undefined' ? window : globalThis);
