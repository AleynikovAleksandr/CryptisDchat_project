// Загружает браузерные библиотеки (backend/static/js) в Node.js и выполняет задания из
// tests/test_js_interop.py: читает JSON со stdin, пишет результаты в stdout.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const staticJs = join(here, '..', '..', 'backend', 'static', 'js');
globalThis.self = globalThis;
for (const f of ['vendor/nacl-fast.min.js', 'lib/proto.js', 'lib/crypto.js', 'lib/markdown.js', 'lib/treekem.js']) {
  vm.runInThisContext(readFileSync(join(staticJs, f), 'utf8'), { filename: f });
}
const { crypto: C, proto, md, treekem } = globalThis.CX;

const input = JSON.parse(readFileSync(0, 'utf8'));
const out = {};

// ECIES: открыть блоб Python и запечатать свой под ключ Python
const pyPriv = await C.importEcdhPriv(input.ecdh.priv_pkcs8);
out.opened_py = C.fromUtf8(await C.eciesOpen(pyPriv, input.ecdh.sealed));
out.sealed_by_js = await C.eciesSeal(input.ecdh.pub, C.utf8('js-secret'));

// ECDSA: проверить подпись Python и подписать самим
const data = C.unb64(input.ecdsa.data);
out.sig_ok = await C.verify(input.ecdsa.pub, C.unb64(input.ecdsa.sig), data);
out.sig_bad = await C.verify(input.ecdsa.pub, C.unb64(input.ecdsa.sig), C.concat(data, Uint8Array.of(1)));
const keys = await C.generateDeviceKeys();
out.js_sig = { pub: keys.signingPub, sig: C.b64(await C.sign(keys.signing.privateKey, data)) };
const sb = input.signing_bytes;
out.signing_bytes = C.b64(C.signingBytes(sb.thread_id, sb.client_msg_id, sb.epoch, C.unb64(sb.nonce), C.unb64(sb.ct)));

// XSalsa20-Poly1305
const boxKey = C.unb64(input.box.key);
out.box_open = C.fromUtf8(C.secretboxOpen(boxKey, C.unb64(input.box.nonce), C.unb64(input.box.ct)));
const jsBox = C.secretboxSeal(boxKey, C.utf8('from js'));
out.js_box = { nonce: C.b64(jsBox.nonce), ct: C.b64(jsBox.ciphertext) };

// слепой индекс
const convKey = C.unb64(input.search.key);
out.tokens = await C.wordTokens(convKey, input.search.text);
out.token_word = await C.searchToken(await C.searchKey(convKey), input.search.word);

// Шамир
out.combined = C.b64(C.shamirCombine(input.shamir.shares.slice(1).map(C.unb64)));
out.js_shares = C.shamirSplit(C.unb64(input.shamir.secret), 3, 2).map(C.b64);

// Меркл
out.merkle_ok = await C.merkleVerify(input.merkle.hash, input.merkle.proof, input.merkle.root);
out.merkle_bad = await C.merkleVerify(input.merkle.hash, input.merkle.proof, '00'.repeat(32));

// protobuf
const frame = proto.decode('WsFrame', C.unb64(input.proto.frame));
out.proto_decoded = { event: frame.event, seq: frame.seq, ts: frame.ts, nonce: C.b64(frame.packet.nonce),
  tokens: frame.packet.search_tokens, preview: frame.packet.push_preview };
out.proto_js = C.b64(proto.encode('MessagePacket', {
  client_msg_id: 'c1', key_epoch: 7, nonce: C.unb64(input.proto.nonce), search_tokens: ['a', 'b'], push_preview: 'Ёж',
}));

// TreeKEM: построить коммит по плану Python и обработать коммит Python
const built = await treekem.buildCommit(input.tree.plan);
out.tree_commit = built.body;
out.tree_secret = C.b64(built.epochSecret);
const tree = { privs: {} };
out.tree_processed = C.b64(await treekem.processCommit(tree, input.tree.py_commit, input.tree.member_pkcs8));

// Markdown: XSS и форматирование
out.md = input.markdown.map((s) => md.render(s));
out.md_plain = md.plain('**Hi** _there_ `code` [link](https://x.io)\n> quote');

process.stdout.write(JSON.stringify(out));
