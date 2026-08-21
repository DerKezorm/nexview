/* Prueft die Oberflaeche auf schmalen Bildschirmen.
 *
 *   npm run mobil
 *   npm run mobil -- --basis http://localhost:5173 --breiten 360,390,430
 *
 * Gesucht wird dreierlei, auf jeder Seite und bei jeder Breite:
 *
 *   1. waagerechtes Scrollen der Seite
 *   2. Text, den Auslassungspunkte auf wenige Zeichen zusammenschnurren lassen
 *   3. Inhalt, der aus einem Kasten mit overflow:hidden herausragt und dort
 *      still abgeschnitten wird - ohne Auslassungszeichen, ohne Hinweis
 *
 * Der dritte Fall ist der heimtueckische: so wurde auf dem Poster aus
 * "Bereits geladen" ein "geladen" und aus dem Ersatztitel "Nordlicht" ein
 * "rdlich". Beides faellt keinem Test auf, der nur nach Scrollbalken sucht.
 *
 * Voraussetzung ist eine laufende Nexview-Instanz mit Inhalt. Gemeint ist eine
 * Wegwerf-Installation mit eigenem NEXVIEW_DATA_DIR, nicht die produktive:
 * das Skript meldet sich an und klickt sich durch alle Seiten.
 *
 * Genutzt wird das ohnehin installierte Chrome bzw. Edge - puppeteer-core
 * bringt keinen eigenen Browser mit und laedt auch keinen herunter.
 */
import puppeteer from 'puppeteer-core';
import { existsSync } from 'node:fs';

const arg = (name, standard) => {
  const i = process.argv.indexOf('--' + name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : standard;
};

const BASIS = arg('basis', 'http://localhost:5173').replace(/\/+$/, '');
const BREITEN = arg('breiten', '360,390,430').split(',').map(Number);
const PASSWORT = arg('passwort', 'demo1234');

const BROWSER = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(existsSync);

if (!BROWSER && !process.argv.includes('--browser')) {
  console.error('Kein Chrome und kein Edge gefunden. Pfad mit --browser <datei> angeben.');
  process.exit(2);
}

/* [Anzeigename, Pfad, Konto, optional: Reiter, der vorher angeklickt wird]
   Die Kennungen stammen aus den Beispieldaten - bei anderem Inhalt anpassen. */
const SEITEN = [
  ['Startseite', '/', 'mira'],
  ['Filme entdecken', '/filme', 'mira'],
  ['Serien entdecken', '/serien', 'mira'],
  ['Suche', '/suche', 'mira'],
  ['Personen', '/personen', 'mira'],
  ['Kalender', '/kalender', 'mira'],
  ['Detailseite Film', '/titel/movie/950765', 'mira'],
  ['Detailseite Serie', '/titel/tv/1399', 'mira'],
  ['Meine Anfragen', '/requests', 'mira'],
  ['Mag ich', '/mag-ich', 'mira'],
  ['Tickets', '/tickets', 'mira'],
  ['Ticket-Verlauf', '/tickets/1', 'mira'],
  ['Profil', '/profil', 'mira'],
  ['Profil - Speicher', '/profil', 'mira', 'Speicher'],
  ['Ueber', '/ueber', 'mira'],
  ['Alle Anfragen', '/admin/requests', 'admin'],
  ['Statistik', '/admin/stats', 'admin'],
  ['Einstellungen - Dienste', '/admin/settings', 'admin'],
  ['Einstellungen - Adresse', '/admin/settings', 'admin', 'Adresse'],
  ['Einstellungen - E-Mail', '/admin/settings', 'admin', 'E-Mail'],
  ['Einstellungen - Benutzer', '/admin/settings', 'admin', 'Benutzer'],
  ['Einstellungen - Sperrliste', '/admin/settings', 'admin', 'Sperrliste'],
  ['Einstellungen - Protokoll', '/admin/settings', 'admin', 'Protokoll'],
];

/** Laeuft im Browser und liefert alle drei Fehlerarten einer Seite. */
const MESSUNG = () => {
  const breite = document.documentElement.clientWidth;
  const raus = [];
  const gekuerzt = [];
  const beschnitten = [];

  const pfad = (el) => {
    const teile = [];
    for (let e = el; e && e.tagName && teile.length < 3; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      if (e.className && typeof e.className === 'string') {
        s += '.' + e.className.trim().split(/\s+/).join('.');
      }
      teile.unshift(s);
    }
    return teile.join(' > ');
  };

  /* Was in einem waagerechten Schieber steckt, DARF ueberstehen - dafuer ist
     der Schieber da. Gesucht sind nur die uebrigen. */
  const imSchieber = (el) => {
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const o = getComputedStyle(e).overflowX;
      if (o === 'auto' || o === 'scroll' || o === 'hidden') return true;
    }
    return false;
  };

  /* Der naechste Vorfahr, der ueberstehenden Inhalt einfach abschneidet.
     Ein Schieber (auto/scroll) tut das nicht - dort kann man hinscrollen. */
  const klemmt = (el) => {
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const o = getComputedStyle(e);
      if (o.overflowX === 'auto' || o.overflowX === 'scroll') return null;
      if (o.overflow === 'hidden' || o.overflowX === 'hidden' || o.overflowX === 'clip') return e;
    }
    return null;
  };

  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const stil = getComputedStyle(el);
    if (stil.position === 'fixed') continue;

    if (r.right > breite + 1 && !imSchieber(el)) {
      raus.push({
        um: Math.round(r.right - breite),
        wo: pfad(el),
        text: (el.textContent || '').trim().slice(0, 40),
      });
    }

    if (stil.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1) {
      const voll = (el.textContent || '').trim();
      const anteil = el.clientWidth / el.scrollWidth;
      const zeichen = Math.round(voll.length * anteil);
      if (voll.length > 3 && (zeichen < 12 || anteil < 0.55)) {
        gekuerzt.push({ zeichen, von: voll.length, text: voll.slice(0, 44), wo: pfad(el) });
      }
    }

    /* Nicht nur die direkten Kinder ansehen: das Zustands-Etikett auf dem
       Poster steckt in einer Zwischenzeile, die brav in den Rahmen passt -
       herausgeragt ist das Etikett darin. */
    const eigenerText = [...el.childNodes].some(
      (n) => n.nodeType === 3 && n.textContent.trim().length > 2,
    );
    if (eigenerText) {
      const kaefig = klemmt(el);
      if (kaefig) {
        const kr = kaefig.getBoundingClientRect();
        const links = kr.left - r.left;
        const rechts = r.right - kr.right;
        if (Math.max(links, rechts) > 2) {
          beschnitten.push({
            links: Math.round(links),
            rechts: Math.round(rechts),
            text: (el.textContent || '').trim().slice(0, 40),
            wo: pfad(el),
          });
        } else if (
          /* Der Kasten steht brav im Rahmen, aber sein Text passt nicht
             hinein und laeuft heraus - ohne Auslassungszeichen. Genau so
             wurde aus "Bereits geladen" ein "geladen". */
          stil.textOverflow !== 'ellipsis' &&
          stil.overflowX === 'visible' &&
          el.scrollWidth > el.clientWidth + 2
        ) {
          beschnitten.push({
            links: 0,
            rechts: el.scrollWidth - el.clientWidth,
            text: (el.textContent || '').trim().slice(0, 40),
            wo: pfad(el),
          });
        }
      }
    }
  }

  /* Ragt ein Kasten heraus, ragt alles darin mit heraus - gemeldet wird nur
     der aeusserste, sonst steht dieselbe Ursache achtmal im Bericht. */
  const aussen = raus.filter(
    (a, i) => !raus.some((b, j) => j !== i && a.wo !== b.wo && a.wo.startsWith(b.wo)),
  );
  const einmalig = (liste) => {
    const gesehen = new Set();
    return liste.filter((e) => (gesehen.has(e.wo) ? false : (gesehen.add(e.wo), true)));
  };

  return {
    scrollBreite: document.documentElement.scrollWidth,
    clientBreite: breite,
    raus: aussen.slice(0, 6),
    gekuerzt: einmalig(gekuerzt).slice(0, 6),
    beschnitten: einmalig(beschnitten).slice(0, 6),
  };
};

const warte = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: arg('browser', BROWSER),
  headless: 'new',
});

/* Erst pruefen, ob der Pruefer ueberhaupt etwas findet. Ein Durchlauf ohne
   Befund ist sonst nicht von einem kaputten Pruefer zu unterscheiden. */
async function selbsttest() {
  const s = await (await browser.createBrowserContext()).newPage();
  await s.setViewport({ width: 390, height: 823 });
  await s.setContent(
    '<body style="margin:0">' +
      '<div style="overflow:hidden;width:120px">' +
      '<span style="white-space:nowrap">Bereits geladen und mehr</span></div>' +
      '<p style="width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
      'Spider-Man: No Way Home</p>' +
      '<div style="width:600px">zu breit</div>' +
      '</body>',
  );
  const m = await s.evaluate(MESSUNG);
  await s.close();
  if (m.raus.length !== 1 || m.gekuerzt.length !== 1 || m.beschnitten.length !== 1) {
    console.error('Selbsttest fehlgeschlagen - der Pruefer erkennt seine eigenen Fehler nicht:');
    console.error(JSON.stringify(m, null, 1));
    await browser.close();
    process.exit(2);
  }
  console.log('Selbsttest bestanden: alle drei Fehlerarten werden erkannt.');
}

await selbsttest();

let seite = await (await browser.createBrowserContext()).newPage();
let angemeldetAls = null;

async function anmelden(benutzer) {
  /* Frischer Kontext statt Abmelden: nur so zaehlt wirklich das gewuenschte
     Konto und nicht der Rest der vorigen Sitzung. */
  const alt = seite;
  seite = await (await browser.createBrowserContext()).newPage();
  await seite.setViewport(alt.viewport());
  await alt.close();

  await seite.goto(BASIS + '/', { waitUntil: 'networkidle2' });
  await warte(1200);
  const feld = await seite.$('input[type="text"], input[autocomplete="username"]');
  if (!feld) {
    throw new Error('Kein Anmeldefeld unter ' + BASIS + ' - laeuft dort wirklich Nexview?');
  }
  await feld.type(benutzer);
  const passwort = await seite.$('input[type="password"]');
  await passwort.type(PASSWORT);
  await Promise.all([
    seite.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {}),
    passwort.press('Enter'),
  ]);
  await warte(1500);
  angemeldetAls = benutzer;
}

let befunde = 0;

for (const [name, pfad, als, reiter] of SEITEN) {
  for (const breite of BREITEN) {
    await seite.setViewport({ width: breite, height: 823, isMobile: true, hasTouch: true });
    if (angemeldetAls !== als) await anmelden(als);

    await seite.goto(BASIS + pfad, { waitUntil: 'networkidle2' });
    await warte(1400);

    if (reiter) {
      const getroffen = await seite.evaluate((b) => {
        const k = [...document.querySelectorAll('button, [role="tab"]')].find(
          (e) => e.textContent.trim() === b,
        );
        if (k) {
          k.click();
          return true;
        }
        return false;
      }, reiter);
      if (!getroffen) console.warn('   Hinweis: Reiter "' + reiter + '" nicht gefunden');
      await warte(1600);
    }

    const m = await seite.evaluate(MESSUNG);
    const scrollt = m.scrollBreite > m.clientBreite + 1;
    if (!scrollt && !m.raus.length && !m.gekuerzt.length && !m.beschnitten.length) continue;

    befunde++;
    console.log('\n### ' + name + '  (' + pfad + ')  bei ' + breite + ' px');
    if (scrollt) {
      console.log('   waagerechtes Scrollen: ' + m.scrollBreite + ' statt ' + m.clientBreite);
    }
    for (const r of m.raus) {
      console.log('   steht ' + r.um + ' px ueber: ' + r.wo + '   [' + r.text + ']');
    }
    for (const g of m.gekuerzt) {
      console.log(
        '   nur ' + g.zeichen + ' von ' + g.von + ' Zeichen: "' + g.text + '"  in ' + g.wo,
      );
    }
    for (const c of m.beschnitten) {
      console.log(
        '   abgeschnitten (links ' +
          c.links +
          ', rechts ' +
          c.rechts +
          '): "' +
          c.text +
          '"  in ' +
          c.wo,
      );
    }
  }
}

await browser.close();

console.log('\n---');
if (befunde === 0) {
  console.log(
    SEITEN.length + ' Ansichten bei ' + BREITEN.join('/') + ' px: nichts zu beanstanden.',
  );
  process.exit(0);
}
console.log(befunde + ' Ansicht(en) mit Befund.');
process.exit(1);
