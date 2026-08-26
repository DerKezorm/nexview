#!/usr/bin/env node
/**
 * Zeichnet jede Specimen-Karte einmal und legt einen Kontaktbogen an.
 *
 * Der Wandler prueft nur Bausteine - Karten unter `guidelines/` und `brand/`
 * sind fuer ihn Beiwerk. Ohne diesen Lauf faellt erst in der Oberflaeche auf,
 * dass eine Karte leer oder zerlaufen ist.
 *
 * Aufruf: node .design-sync/karten-pruefen.mjs [ds-bundle]
 */

import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from '../.ds-sync/node_modules/playwright/index.mjs';

const ZIEL = resolve(process.argv[2] ?? 'ds-bundle');
const AUS = join(ZIEL, '_screenshots', 'karten');
mkdirSync(AUS, { recursive: true });

const karten = [];
for (const ordner of ['guidelines', 'brand']) {
  const p = join(ZIEL, ordner);
  if (!existsSync(p)) continue;
  for (const f of readdirSync(p).filter((f) => f.endsWith('.card.html'))) {
    karten.push({ ordner, datei: f, pfad: join(p, f) });
  }
}

const browser = await chromium.launch();
let fehler = 0;

for (const k of karten) {
  // Das Mass steht in der ersten Zeile: <!-- @dsCard ... viewport="760x210" -->
  const kopf = readFileSync(k.pfad, 'utf8').split('\n', 1)[0];
  const m = /viewport="(\d+)x(\d+)"/.exec(kopf);
  const width = m ? Number(m[1]) : 760;
  const height = m ? Number(m[2]) : 240;

  const seite = await browser.newPage({ viewport: { width, height } });
  const meldungen = [];
  seite.on('pageerror', (e) => meldungen.push(String(e)));
  seite.on('console', (e) => { if (e.type() === 'error') meldungen.push(e.text()); });

  await seite.goto(pathToFileURL(k.pfad).href, { waitUntil: 'load' });

  // Laeuft der Inhalt aus dem angegebenen Mass heraus, schneidet die Karte ab.
  const zu = await seite.evaluate(() => ({
    h: document.body.scrollHeight,
    b: document.body.scrollWidth,
  }));

  const name = `${k.ordner}__${k.datei.replace('.card.html', '')}.png`;
  await seite.screenshot({ path: join(AUS, name) });
  await seite.close();

  const eng = zu.h > height + 2 || zu.b > width + 2;
  if (meldungen.length || eng) {
    fehler++;
    console.error(`✗ ${k.datei}`);
    if (eng) console.error(`   passt nicht: Inhalt ${zu.b}x${zu.h}, Karte ${width}x${height}`);
    for (const x of meldungen) console.error(`   ${x}`);
  } else {
    console.error(`  ${k.datei} — ${width}x${height}`);
  }
}

await browser.close();
console.error(fehler ? `\n✗ ${fehler} von ${karten.length} Karten stimmen nicht` : `\n✓ ${karten.length} Karten sitzen`);
process.exit(fehler ? 1 : 0);
