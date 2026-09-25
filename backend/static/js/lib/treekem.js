/* TreeKEM-подобное дерево ключей группы на клиенте (ТЗ 6.8).
 *
 * Сервер присылает план затронутой ветки: какие узлы получают новые ключи и под чьи
 * публичные ключи шифровать их приватную часть. Коммитер генерирует пары ECDH P-256
 * для узлов пути, запечатывает (ECIES) приватные ключи для «резолюции» детей каждого
 * узла и шифрует новый групповой ключ эпохи под ключ корня.
 *
 * Участник обрабатывает коммит снизу вверх: узнаёт ключ узла, если среди целей есть
 * узел, чей приватный ключ у него уже есть (свой лист = Identity-ключ, или узел ниже
 * по пути, полученный на предыдущем шаге). Приватные ключи узлов хранятся в vault как PKCS#8.
 */
(function (root) {
  'use strict';

  const C = () => root.CX.crypto;
  const level = (x) => { let k = 0; while ((x >> k) & 1) k++; return k; };
  const leafNode = (leaf) => 2 * leaf;
  const ECDH = { name: 'ECDH', namedCurve: 'P-256' };

  async function buildCommit(plan) {
    const fresh = {};
    for (const n of plan.path) {
      const kp = await root.crypto.subtle.generateKey(ECDH, true, ['deriveBits']);
      fresh[n] = {
        pub: C().b64(await root.crypto.subtle.exportKey('spki', kp.publicKey)),
        pkcs8: new Uint8Array(await root.crypto.subtle.exportKey('pkcs8', kp.privateKey)),
      };
    }
    const pubOf = (n) => (fresh[n] ? fresh[n].pub : plan.known_pubs[String(n)]);
    const nodes = [];
    for (const n of plan.path) {
      const ciphertexts = [];
      for (const t of plan.targets[String(n)] || []) {
        ciphertexts.push({ target: t, ct: await C().eciesSeal(pubOf(t), fresh[n].pkcs8) });
      }
      nodes.push({ index: n, public_key: fresh[n].pub, ciphertexts });
    }
    const epochSecret = C().random(32);
    const rootCt = await C().eciesSeal(pubOf(plan.root), epochSecret);
    return { body: { epoch: plan.next_epoch, nodes, root_ciphertext: rootCt }, epochSecret };
  }

  /* tree = { privs: {nodeIndex: pkcs8B64}, epochs: {epoch: secretB64}, processed: epoch }
   * identityPkcs8 — приватный Identity-ключ (он же ключ листа). */
  async function processCommit(tree, commit, identityPkcs8) {
    tree.privs = tree.privs || {};
    tree.epochs = tree.epochs || {};
    if (commit.my_leaf !== null && commit.my_leaf !== undefined) {
      const leaf = String(leafNode(commit.my_leaf));
      if (!tree.privs[leaf]) tree.privs[leaf] = identityPkcs8;
    }
    const nodes = commit.nodes.slice().sort((a, b) => level(a.index) - level(b.index) || a.index - b.index);
    for (const node of nodes) {
      let got = null;
      for (const c of node.ciphertexts) {
        const holder = tree.privs[String(c.target)];
        if (!holder) continue;
        try {
          got = await C().eciesOpen(await C().importEcdhPriv(holder), c.ct);
          break;
        } catch (e) { /* ключ устарел — пробуем следующую цель */ }
      }
      if (got) tree.privs[String(node.index)] = C().b64(got);
      else delete tree.privs[String(node.index)];
    }
    for (const b of commit.blanks || []) delete tree.privs[String(b)];
    const rootPriv = tree.privs[String(commit.root)];
    tree.processed = Math.max(tree.processed || 0, commit.epoch);
    if (!rootPriv) return null;
    const secret = await C().eciesOpen(await C().importEcdhPriv(rootPriv), commit.root_ciphertext);
    tree.epochs[String(commit.epoch)] = C().b64(secret);
    return secret;
  }

  root.CX = root.CX || {};
  root.CX.treekem = { buildCommit, processCommit, level, leafNode };
})(typeof window !== 'undefined' ? window : globalThis);
