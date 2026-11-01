#!/usr/bin/env node
/* Сборка клиента для образа backend: все <script src="/static/js/..."> страницы склеиваются
 * в один минифицированный файл static/dist/<страница>.<хеш>.js, без source map и комментариев.
 *
 *   npm install --no-save esbuild@0.24.2      # один раз; package.json не создаётся
 *   node scripts/build_frontend.mjs --out <каталог>
 *
 * В <каталог> пишутся templates/*.html (с одним тегом вместо списка скриптов),
 * static/dist/*.js. Исходники в репозитории
 * не меняются: локальная разработка (scripts/dev_server.py) работает с ними как раньше.
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { transform } from 'esbuild';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BACKEND = join(ROOT, 'backend');
const PAGES = ['login.html', 'cryptis.html'];
const outArg = process.argv.indexOf('--out');
if (outArg < 0 || !process.argv[outArg + 1]) {
  console.error('usage: node scripts/build_frontend.mjs --out <dir>');
  process.exit(64);
}
const OUT = resolve(process.argv[outArg + 1]);

// target es2020: тот же синтаксис, что и в исходниках; минификация меняет только имена и пробелы
const minify = async (code) => (await transform(code, {
  minify: true, target: 'es2020', legalComments: 'none', charset: 'utf8',
})).code;

const SCRIPT_TAG = /^[ \t]*<script src="\/static\/(js\/[^"]+\.js)"><\/script>\r?\n?/gm;

for (const page of PAGES) {
  const html = readFileSync(join(BACKEND, 'templates', page), 'utf8');
  const files = [...html.matchAll(SCRIPT_TAG)].map((m) => m[1]);
  if (!files.length) throw new Error(`${page}: no /static/js scripts found`);

  // файлы-скрипты делят общую область видимости (state, H, set …) — склеиваем их в одну функцию:
  // внутренние имена минификатор сокращает, наружу видно только то, что код сам кладёт в window
  const body = files.map((f) => `// ${f}\n${readFileSync(join(BACKEND, 'static', f), 'utf8')}`).join('\n;\n');
  const code = await minify(`(() => {\n'use strict';\n${body}\n})();\n`);

  const name = `${page.replace(/\.html$/, '')}.${createHash('sha256').update(code).digest('hex').slice(0, 12)}.js`;
  mkdirSync(join(OUT, 'static', 'dist'), { recursive: true });
  writeFileSync(join(OUT, 'static', 'dist', name), code);

  let first = true;
  const built = html.replace(SCRIPT_TAG, () => {
    if (!first) return '';
    first = false;
    return `<script src="/static/dist/${name}"></script>\n`;
  });
  mkdirSync(join(OUT, 'templates'), { recursive: true });
  writeFileSync(join(OUT, 'templates', page), built);
  console.log(`${page}: ${files.length} scripts -> static/dist/${name} (${(code.length / 1024).toFixed(1)} KB)`);
}
