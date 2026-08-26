#!/usr/bin/env node
/**
 * Legt das Fundament in ein frisch gebautes `ds-bundle/`.
 *
 * ⚠️ **Muss nach JEDEM `package-build.mjs` laufen.** Der Wandler raeumt
 * `ds-bundle/` bei jedem Lauf vollstaendig leer - er kennt nur Bausteine.
 * Token-Dateien, Specimen-Karten und die Marke leben deshalb dauerhaft unter
 * `.design-sync/foundation/` und werden hier hineinkopiert.
 *
 * Ausserdem wird `styles.css` neu geschrieben: Der Wandler legt dort genau
 * einen Import auf `_ds_bundle.css`; die Rollen-Ebene muss danach kommen,
 * damit ihre var()-Verweise auf die Rampen zeigen koennen.
 *
 * Aufruf (aus dem Repo-Wurzelverzeichnis):
 *   node .design-sync/fundament-einfuegen.mjs [ds-bundle]
 */

import { cpSync, existsSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HIER = dirname(fileURLToPath(import.meta.url));
const QUELLE = join(HIER, 'foundation');
const ZIEL = resolve(process.argv[2] ?? 'ds-bundle');

if (!existsSync(QUELLE)) {
  console.error(`[FEHLT] ${QUELLE} - ohne Fundament gibt es nichts zu kopieren`);
  process.exit(1);
}
if (!existsSync(join(ZIEL, '_ds_bundle.js'))) {
  console.error(`[KEIN_BUENDEL] ${ZIEL} sieht nicht nach einem gebauten ds-bundle aus`);
  process.exit(1);
}

for (const ordner of ['tokens', 'guidelines', 'brand']) {
  const von = join(QUELLE, ordner);
  if (!existsSync(von)) continue;
  cpSync(von, join(ZIEL, ordner), { recursive: true });
  console.error(`  ${ordner}/: ${readdirSync(von).length} Datei(en)`);
}

/*
 * Reihenfolge ist Pflicht, nicht Geschmack: `colors.css` besteht nur aus
 * Verweisen wie `--surface-card: var(--color-ink-850)`. Stuende es vor dem
 * Buendel, gaebe es die Rampe zu dem Zeitpunkt noch nicht.
 *
 * `base.css` fehlt hier bewusst - die Anwendung bringt ihren eigenen Reset
 * mit (Tailwind Preflight), zwei uebereinander heben sich in Kleinigkeiten
 * gegenseitig auf. Die Datei liegt fuer rahmenwerkfreie Seiten trotzdem bei.
 */
const IMPORTE = [
  '_ds_bundle.css',
  'tokens/colors.css',
  'tokens/typography.css',
  'tokens/spacing.css',
  'tokens/radius.css',
  'tokens/elevation.css',
  'tokens/motion.css',
];

const kopf = [
  '/* Einstiegspunkt. Nur Importe - hier steht nie ein eigener Wert.',
  ' *',
  ' * Erzeugt von .design-sync/fundament-einfuegen.mjs. Von Hand geaenderte',
  ' * Zeilen sind nach dem naechsten Bau wieder weg.',
  ' *',
  ' * tokens/base.css fehlt absichtlich: Reset nur fuer Seiten ohne Rahmenwerk.',
  ' */',
].join('\n');

writeFileSync(
  join(ZIEL, 'styles.css'),
  `${kopf}\n${IMPORTE.map((p) => `@import "./${p}";`).join('\n')}\n`,
  'utf8',
);
console.error(`  styles.css: ${IMPORTE.length} Import(e)`);
console.error(`✓ Fundament liegt in ${ZIEL}`);
