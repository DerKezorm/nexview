/* Laedt jede Seite und schreibt mit, was die Konsole sagt.
 *
 *   node konsole-pruefen.mjs --basis http://localhost:8002 --passwort admin
 *
 * Gedacht als Gegenstueck zu mobil-pruefen.mjs: Das prueft, wie es aussieht,
 * dieses hier, was dabei schiefgeht. Gebraucht fuer die
 * Content-Security-Policy - eine zu enge Regel zeigt keine Fehlermeldung,
 * sondern eine halb geladene Seite, und der einzige Ort, an dem sie sich
 * meldet, ist die Konsole.
 *
 * ⚠️ **Zweimal laufen lassen.** Einmal vor der CSP, einmal danach. Ohne den
 * ersten Lauf laesst sich nicht sagen, ob eine Meldung neu ist oder schon
 * immer da war.
 */

import puppeteer from 'puppeteer-core';
import { existsSync } from 'node:fs';

const arg = (name, standard) => {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : standard;
};

const BASIS = arg('basis', 'http://localhost:8002').replace(/\/+$/, '');
const PASSWORT = arg('passwort', 'admin');
const BENUTZER = arg('benutzer', 'admin');

const BROWSER = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].find(existsSync);

if (!BROWSER) {
  console.error('Kein Chrome und kein Edge gefunden.');
  process.exit(2);
}

/* Dieselbe Liste wie in mobil-pruefen.mjs - dort sind es die Seiten, die eine
   Oberflaeche kaputtmachen koennen, hier dieselben. */
const SEITEN = [
  ['Startseite', '/'],
  ['Filme entdecken', '/filme'],
  ['Serien entdecken', '/serien'],
  ['Stoebern', '/stoebern'],
  ['Suche', '/suche'],
  ['Personen', '/personen'],
  ['Kalender', '/kalender'],
  ['Detailseite Film', '/titel/movie/603'],
  ['Detailseite Serie', '/titel/tv/4386'],
  ['Meine Anfragen', '/requests'],
  ['Mag ich', '/mag-ich'],
  ['Tickets', '/tickets'],
  ['Profil', '/profil'],
  ['Ueber', '/ueber'],
  ['Alle Anfragen', '/admin/requests'],
  ['Statistik', '/admin/stats'],
  ['Einstellungen', '/admin/settings'],
];

/* Reiter, die auf der Einstellungsseite einzeln angeklickt werden - sie laden
   je eigene Inhalte nach. */
const REITER = ['Adresse', 'E-Mail', 'Benutzer', 'Kontingente', 'Sperrliste', 'Protokoll'];

const warte = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: BROWSER,
  headless: 'new',
  args: ['--no-sandbox'],
});

const seite = await browser.newPage();
await seite.setViewport({ width: 1440, height: 900 });

/* Alles einsammeln, was der Browser zu sagen hat - Konsole, geplatzte
   Anfragen, und die CSP-Verstoesse, die als eigenes Ereignis kommen. */
const meldungen = [];
let aktuelleSeite = '(Start)';

seite.on('console', (m) => {
  if (m.type() !== 'error' && m.type() !== 'warning') return;
  // "Failed to load resource" sagt nicht, welche - der response-Horcher unten
  // meldet denselben Fall mit Adresse. Sonst stuende alles doppelt da.
  if (m.text().startsWith('Failed to load resource')) return;
  meldungen.push({ seite: aktuelleSeite, art: m.type(), text: m.text() });
});
seite.on('pageerror', (e) => {
  meldungen.push({ seite: aktuelleSeite, art: 'pageerror', text: String(e.message ?? e) });
});
seite.on('requestfailed', (r) => {
  meldungen.push({
    seite: aktuelleSeite,
    art: 'requestfailed',
    text: `${r.url().slice(0, 110)} — ${r.failure()?.errorText ?? '?'}`,
  });
});
/* Die Konsole meldet "Failed to load resource" ohne zu sagen, welche - und
   damit ist die Meldung wertlos. Die Antwort selbst weiss es. */
seite.on('response', (r) => {
  if (r.status() >= 400) {
    meldungen.push({
      seite: aktuelleSeite,
      art: 'http',
      text: `HTTP ${r.status()} ${r.url().replace(BASIS, '').slice(0, 120)}`,
    });
  }
});

// Anmelden
await seite.goto(BASIS + '/', { waitUntil: 'networkidle2' });
await warte(1200);
const feld = await seite.$('input[type="text"], input[autocomplete="username"]');
if (!feld) {
  console.error('Kein Anmeldefeld unter ' + BASIS + ' - laeuft dort Nexview?');
  await browser.close();
  process.exit(2);
}
await feld.type(BENUTZER);
const passwort = await seite.$('input[type="password"]');
await passwort.type(PASSWORT);
await Promise.all([
  seite.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}),
  passwort.press('Enter'),
]);
await warte(2000);

for (const [name, pfad] of SEITEN) {
  aktuelleSeite = name;
  await seite.goto(BASIS + pfad, { waitUntil: 'networkidle2' }).catch(() => {});
  await warte(1600);

  if (pfad === '/admin/settings') {
    for (const beschriftung of REITER) {
      aktuelleSeite = `Einstellungen · ${beschriftung}`;
      const getroffen = await seite.evaluate((b) => {
        const k = [...document.querySelectorAll('button, [role="tab"]')].find(
          (e) => e.textContent.trim() === b,
        );
        if (k) k.click();
        return Boolean(k);
      }, beschriftung);
      if (getroffen) await warte(1400);
    }
  }
}

await browser.close();

// Bericht
const csp = meldungen.filter((m) => /Content Security Policy|violates the following/i.test(m.text));
const rest = meldungen.filter((m) => !csp.includes(m));

console.log(`\n=== ${meldungen.length} Meldungen auf ${SEITEN.length} Seiten ===\n`);

if (csp.length) {
  console.log(`--- CSP-Verstoesse (${csp.length}) ---`);
  const gesehen = new Set();
  for (const m of csp) {
    const kurz = m.text.slice(0, 260);
    if (gesehen.has(kurz)) continue;
    gesehen.add(kurz);
    console.log(`[${m.seite}] ${kurz}\n`);
  }
} else {
  console.log('--- Keine CSP-Verstoesse ---\n');
}

if (rest.length) {
  console.log(`--- Sonstiges (${rest.length}) ---`);
  const gezaehlt = new Map();
  for (const m of rest) {
    const kurz = m.text.slice(0, 200);
    gezaehlt.set(kurz, (gezaehlt.get(kurz) ?? 0) + 1);
  }
  for (const [text, anzahl] of [...gezaehlt].sort((a, b) => b[1] - a[1])) {
    console.log(`${String(anzahl).padStart(3)}x  ${text}`);
  }
}
process.exit(csp.length ? 1 : 0);
