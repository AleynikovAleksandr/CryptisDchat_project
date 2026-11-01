"""Offline browser integration: real UI, WebCrypto and PIN vault; HTTP boundary fixture."""
import json
import base64
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.proto import wire
ORIGIN = 'http://localhost:3988'
ACCOUNT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
THREAD = 'thread-fixture'


def check_pairing(browser, mobile=False):
    errors, requests = [], {}
    me = {'id': ACCOUNT, 'display_name': 'Pairing account', 'username': 'pairing',
          'key_version': 1, 'has_backup': True}
    complete_calls, inspect_calls, cancel_calls = [], [], []
    messages = []
    peer_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    thread = {'id': THREAD, 'kind': 'dm', 'peer': {'id': peer_id, 'display_name': 'Chat partner', 'username': 'partner'},
              'key_epoch': 1, 'unread': 0, 'created_at': '2026-11-01T00:00:00Z', 'last_message_at': None}
    def route(r):
        path = urlparse(r.request.url).path
        method = r.request.method
        if path.startswith('/static/'):
            f = ROOT / 'backend' / path.lstrip('/')
            if f.is_file():
                r.fulfill(path=str(f)); return
            r.fulfill(status=404); return
        if path in ('/login', '/app') or path.startswith('/app/'):
            r.fulfill(path=str(ROOT / 'backend/templates' / ('login.html' if path == '/login' else 'cryptis.html')),
                      content_type='text/html'); return
        data = r.request.post_data_json if 'application/json' in r.request.headers.get('content-type', '') and r.request.post_data else {}
        result = []
        if path == '/api/auth/pairings':
            ident = str(uuid.uuid4())
            result = {'request_id': ident, 'poll_secret': 'PRIVATE-POLL-' + ident,
                      'expires_at': int(time.time()) + 120,
                      'qr_payload': {'version': 1, 'request_id': ident,
                                     'ephemeral_public_key': data['ephemeral_public_key']}}
            requests[ident] = dict(result, status='pending', device_name=data['device_name'])
        elif path.startswith('/api/auth/pairings/'):
            ident = path.split('/')[4]
            req = requests[ident]
            action = path.split('/')[-1]
            if method == 'GET' or method == 'DELETE' or action == 'complete':
                assert r.request.headers.get('x-pairing-secret') == req['poll_secret']
            if action in ('inspect', 'approve', 'reject'):
                assert data['request_id'] == ident
                assert data['ephemeral_public_key'] == req['qr_payload']['ephemeral_public_key']
                assert r.request.headers.get('authorization')
            if action == 'inspect':
                inspect_calls.append(ident)
                result = {'device_name': req['device_name'], 'expires_at': req['expires_at']}
            elif action == 'approve':
                req.update(status='approved', envelope=data['envelope']); result = {'status': 'approved'}
            elif action == 'reject':
                req['status'] = 'rejected'; result = {'status': 'rejected'}
            elif action == 'complete':
                assert data['operation_id'] == req['envelope']['operation_id']
                complete_calls.append(ident)
                result = {'access_token': 'paired-access', 'refresh_token': 'paired-refresh', 'user': me}
            elif method == 'DELETE':
                req['status'] = 'cancelled'; cancel_calls.append(ident); r.fulfill(status=204); return
            else:
                result = {'status': req['status'], 'expires_at': req['expires_at']}
                if req.get('envelope'): result['envelope'] = req['envelope']
        elif path == '/api/me': result = me
        elif path == '/api/threads': result = [thread]
        elif path == f'/api/threads/{THREAD}/messages' and method == 'GET':
            result = {'items': messages[:], 'next_before': None}
        elif path == f'/api/threads/{THREAD}/messages' and method == 'POST':
            packet = wire.decode('MessagePacket', r.request.post_data_buffer)
            item = {'id': str(uuid.uuid4()), 'thread_id': THREAD, 'seq': len(messages) + 1,
                    'sender_id': ACCOUNT, 'client_msg_id': packet['client_msg_id'], 'kind': packet['kind'] or 'text',
                    'ciphertext': base64.b64encode(packet['ciphertext']).decode(),
                    'nonce': base64.b64encode(packet['nonce']).decode(),
                    'signature': base64.b64encode(packet['signature']).decode(),
                    'key_epoch': packet['key_epoch'], 'sender_key_version': packet['sender_key_version'],
                    'reply_to_id': packet['reply_to_id'] or None, 'forwarded_from_id': None,
                    'attachment_id': None, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'expires_at': None, 'confirmed': False, 'reactions': []}
            messages.append(item); result = item
        elif path == '/api/settings':
            result = {'autolock': 'never', 'group_invite_policy': 'everyone', 'media_retention': '30d'}
        elif path == '/api/push/config': result = {'enabled': False}
        r.fulfill(json=result)

    opts = {'viewport': {'width': 390 if mobile else 1280, 'height': 844}, 'has_touch': mobile, 'is_mobile': mobile}
    old_context = browser.new_context(**opts)
    new_context = browser.new_context(**opts)
    new_context.add_init_script("""(() => {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const url = String(args[0]);
        const method = args[1]?.method || (args[0] instanceof Request ? args[0].method : 'GET');
        if (sessionStorage.getItem('__delayPairingCreate') === '1' && url.includes('/api/auth/pairings') && method === 'POST') {
          sessionStorage.removeItem('__delayPairingCreate');
          await new Promise(resolve => setTimeout(resolve, 1500));
        }
        return originalFetch(...args);
      };
    })();""")
    for context in (old_context, new_context):
        context.route('**/*', route)
        context.route_web_socket('**/ws', lambda ws: None)
    old, new = old_context.new_page(), new_context.new_page()
    for page in (old, new): page.on('pageerror', lambda e: errors.append(str(e)))
    old.add_init_script("""(() => {
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
        configurable: true, value: async () => {
          if (!window.__pairingCameraDeniedOnce) {
            window.__pairingCameraDeniedOnce = true;
            throw new DOMException('camera permission denied', 'NotAllowedError');
          }
          const canvas = document.createElement('canvas');
          canvas.width = 640; canvas.height = 480;
          const context = canvas.getContext('2d');
          context.fillStyle = '#28a8e0'; context.fillRect(0, 0, canvas.width, canvas.height);
          return canvas.captureStream(15);
        },
      });
      window.BarcodeDetector = class {
        async detect() { return []; }
      };
      HTMLMediaElement.prototype.play = () => Promise.resolve();
    })();""")
    new.goto(ORIGIN + '/login')
    expect(new.get_by_role('button', name='Connect with QR')).to_be_visible()
    # Cancel during the in-flight create response must wait for its request ID,
    # send DELETE, and refund the reservation before leaving the pairing page.
    new.evaluate("sessionStorage.setItem('__delayPairingCreate', '1')")
    new.get_by_role('button', name='Connect with QR').click()
    expect(new.locator('.pairing-card__generate')).to_have_text('Generating QR…')
    new.locator('.pairing-card__cancel').click()
    expect(new.get_by_role('button', name='Connect with QR')).to_be_visible(timeout=5000)
    assert len(cancel_calls) == 1 and requests[cancel_calls[0]]['status'] == 'cancelled'
    new.get_by_role('button', name='Connect with QR').click()
    expect(new.locator('#pairing-qr svg')).to_be_visible()
    code = new.locator('.pairing-card__code').inner_text()
    public = json.loads(code)
    assert set(public) == {'version', 'request_id', 'ephemeral_public_key'}
    assert len(public['ephemeral_public_key']) == 124
    assert requests[public['request_id']]['poll_secret'] not in new.content()
    assert 'poll_secret' not in new.url
    assert not new.evaluate('Object.values(sessionStorage).some(v => v.includes("PRIVATE-POLL"))')
    assert new.evaluate('document.documentElement.scrollWidth <= innerWidth')
    # Real existing-device app and vault; bypass only wallet authentication setup.
    old.goto(ORIGIN + '/login?reauth=1')
    old.evaluate("""me => sessionStorage.setItem('cx_login', JSON.stringify({access:'old-access', refresh:'old-refresh', user:me}))""", me)
    old.goto(ORIGIN + '/app')
    old.wait_for_function('typeof S !== "undefined" && S.userId !== null')
    old.evaluate("""async () => {
        const generated = await CXC.generateDeviceKeys();
        S.keys = {version:1, identityPkcs8:generated.identityPkcs8, signingPkcs8:generated.signingPkcs8,
          identityPub:generated.identityPub, signingPub:generated.signingPub};
        S.convKeys = {'thread-fixture': {'1': CXC.b64(CXC.random(32))}};
        S.cache = {'thread-fixture': [{text:'MESSAGE-CACHE-MUST-NOT-TRANSFER'}]};
        await VAULT.create('1234', vaultState()); await importKeys(); await startApp();
        await openThread('thread-fixture'); await sendText('thread-fixture', 'Message from before pairing', null);
        H.openSettings();
    }""")
    expect(old.get_by_role('button', name='Connect a device')).to_be_visible()
    old.get_by_role('button', name='Connect a device').click()
    expect(old.get_by_role('button', name='Scan QR code')).to_be_visible()
    old.get_by_role('button', name='Scan QR code').click()
    expect(old.locator('.pairing-scanner')).to_be_visible()
    expect(old.locator('.pairing-scanner__hint')).to_contain_text('Camera access is blocked')
    expect(old.locator('.pairing-scanner__retry')).to_have_css('border-top-color', 'rgb(244, 33, 46)')
    old.get_by_role('button', name='Try again').click()
    expect(old.locator('.pairing-scanner__hint')).to_contain_text('Align the QR code inside the frame')
    old.wait_for_function("(() => { const v = document.querySelector('#pairing-camera'); return v && !v.hidden && v.srcObject && v.srcObject.getVideoTracks().some(t => t.readyState === 'live') && v.videoWidth > 0 && v.videoHeight > 0; })()", timeout=8000)
    video_box = old.locator('#pairing-camera').bounding_box()
    assert video_box and video_box['width'] > 100 and video_box['height'] > 100, video_box
    old.evaluate('render()')
    old.wait_for_timeout(1200)
    expect(old.locator('.pairing-scanner')).to_be_visible()
    old.locator('.pairing-scanner__cancel').click()
    expect(old.locator('.pairing-scanner')).to_be_hidden()
    expect(old.locator('#pairing-scan-code')).to_be_visible()
    assert inspect_calls == [], 'canceling the scanner must not send an inspection request'
    old.locator('#pairing-scan-code').fill(code)
    old.get_by_role('button', name='Continue').click()
    expect(old.get_by_role('button', name='Allow', exact=True)).to_be_visible()
    old.get_by_role('button', name='Reject', exact=True).click()
    expect(new.locator('.pairing-card__status')).to_contain_text('rejected', timeout=10000)
    new.locator('.pairing-card__generate').click()
    new.wait_for_function('(id) => { try { return JSON.parse(document.querySelector(".pairing-card__code")?.textContent || "").request_id !== id; } catch { return false; } }', arg=public['request_id'])
    code = new.locator('.pairing-card__code').inner_text()
    ident = json.loads(code)['request_id']
    requests[ident]['status'] = 'expired'
    expect(new.locator('.pairing-card__status')).to_contain_text('expired', timeout=10000)
    new.locator('.pairing-card__generate').click()
    new.wait_for_function('(id) => { try { return JSON.parse(document.querySelector(".pairing-card__code")?.textContent || "").request_id !== id; } catch { return false; } }', arg=ident)
    code = new.locator('.pairing-card__code').inner_text()
    old.get_by_role('button', name='Connect another device').click()
    old.evaluate('(sample) => { window.jsQR = () => ({ data: sample }); }', code)
    old.get_by_role('button', name='Scan QR code').click()
    expect(old.get_by_role('button', name='Allow', exact=True)).to_be_visible(timeout=5000)
    expect(old.locator('.pairing-scanner')).to_be_hidden()
    assert inspect_calls[-1] == json.loads(code)['request_id']
    old.get_by_role('button', name='Allow', exact=True).click()
    expect(new.locator('.passcode-title')).to_have_text('Enter Your Passcode', timeout=10000)
    assert not complete_calls
    new.locator('#dc-gate-input').fill('1234')
    new.wait_for_function("(account) => S.userId === account && document.querySelector('.gate') === null", arg=ACCOUNT, timeout=15000)
    st = new.evaluate("async () => VAULT.unlock('1234')")
    assert st['user_id'] == ACCOUNT and st['refresh'] == 'paired-refresh'
    old_keys = old.evaluate('S.keys')
    assert st['keys'] == old_keys, {k: (len(st['keys'].get(k, '')) if isinstance(st['keys'].get(k), str) else st['keys'].get(k), len(old_keys.get(k, '')) if isinstance(old_keys.get(k), str) else old_keys.get(k)) for k in set(st['keys']) | set(old_keys)}
    assert st['conv'] == old.evaluate('S.convKeys')
    assert st['cache'] == {}
    assert len(complete_calls) == 1
    assert new.evaluate('S.userId') == ACCOUNT
    new.evaluate("openThread('thread-fixture')")
    expect(new.locator('.bubble__text').filter(has_text='Message from before pairing')).to_be_visible(timeout=10000)
    new.evaluate("sendText('thread-fixture', 'Reply from paired device', null)")
    assert len(messages) == 2
    old.evaluate("openThread('thread-fixture')")
    expect(old.locator('.bubble__text').filter(has_text='Reply from paired device')).to_be_visible(timeout=10000)
    rapid = [f'Paired burst {i}' for i in range(1, 5)]
    new.evaluate("async (texts) => await enqueueMessageBatch('thread-fixture', texts.map(text => ({type:'text', text, reply:null})))", rapid)
    assert [m['seq'] for m in messages] == list(range(1, 7))
    assert [m['seq'] for m in messages] == sorted(m['seq'] for m in messages)
    old.evaluate("loadMessages('thread-fixture')")
    expect(old.locator('.bubble__text').filter(has_text='Paired burst 4')).to_be_visible(timeout=15000)
    old_order = old.locator('.bubble__text').all_text_contents()
    assert [old_order.index(message) for message in rapid] == sorted(old_order.index(message) for message in rapid)
    assert not errors, errors
    old_context.close(); new_context.close()
    print('Pairing browser checks passed (' + ('mobile' if mobile else 'desktop') + ')')
