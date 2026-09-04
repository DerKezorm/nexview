/* Der Service Worker: Er nimmt Meldungen an, wenn Nexview gar nicht offen ist.
 *
 * ⚠️ **Er speichert nichts zwischen.** Kein Cache, keine Offline-Fassung. Eine
 * Oberfläche von gestern aus dem Speicher zeigte Anfragen von gestern und
 * sagte nicht dazu, dass sie das tut. Dieser Worker hat genau eine Aufgabe,
 * und alles andere geht wie bisher übers Netz.
 *
 * ⚠️ **Er wird nicht gebündelt.** Er liegt in `public/` und geht unverändert
 * mit; Vite fasst ihn nicht an. Deshalb steht hier kein Import und keine
 * Syntax, für die ein Bauschritt nötig wäre.
 *
 * ⚠️ **Er hat keine Übersetzung.** Ein Service Worker lebt ohne Dokument, hat
 * also weder i18next noch die eingestellte Sprache. Was hier erscheint, kommt
 * deshalb **fertig formuliert vom Server**, in der Sprache, die das Gerät bei
 * der Anmeldung genannt hat. Nur die eine Zeile für den Notfall unten ist
 * fest, und die sieht man nur, wenn etwas kaputt ist.
 */

/* Sofort übernehmen, statt auf das Schließen aller Reiter zu warten.
   ⚠️ Ohne das bliebe nach einem Update wochenlang der alte Worker aktiv, und
   eine Änderung hier käme bei niemandem an, der Nexview dauernd offen hat. */
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()))

self.addEventListener('push', (ereignis) => {
  /* ⚠️ **Eine Meldung ist Pflicht, sobald ein Push ankommt.** Chrome und
     Firefox entziehen die Erlaubnis, wenn ein Worker einen Push annimmt, ohne
     etwas zu zeigen. Deshalb steht am Ende jedes Zweigs eine Meldung, auch
     wenn der Rumpf unlesbar war. */
  let daten = {}
  try {
    daten = ereignis.data ? ereignis.data.json() : {}
  } catch (e) {
    daten = {}
  }

  const titel = daten.title || 'Nexview'
  const optionen = {
    body: daten.body || '',
    icon: 'icon-192.png',
    badge: 'badge-96.png',
    /* Gleiche Marke ersetzt statt zu stapeln: Dieselbe Aussage zum selben
       Titel steht sonst nach einer Nacht dreimal untereinander. */
    tag: daten.tag || 'nexview',
    renotify: true,
    data: { url: daten.url || './' },
  }
  /* Das Poster, wo der Browser es zeigt. Safari kennt die Eigenschaft nicht
     und übergeht sie still. */
  if (daten.image) optionen.image = daten.image

  ereignis.waitUntil(self.registration.showNotification(titel, optionen))
})

self.addEventListener('notificationclick', (ereignis) => {
  ereignis.notification.close()

  /* ⚠️ **Erst einen offenen Reiter suchen, dann einen neuen aufmachen.** Wer
     bei jedem Klick ein weiteres Fenster bekommt, hat nach einem Tag zehn
     Nexviews offen, und in jedem eine eigene Sitzung. */
  const ziel = new URL(
    (ereignis.notification.data && ereignis.notification.data.url) || './',
    self.registration.scope,
  ).href

  ereignis.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((fenster) => {
      for (const f of fenster) {
        /* Im Geltungsbereich heißt: dieselbe Installation. Ein zweites Nexview
           unter einem anderen Unterpfad ist ein anderes Fenster. */
        if (f.url.startsWith(self.registration.scope) && 'focus' in f) {
          if ('navigate' in f && f.url !== ziel) return f.navigate(ziel).then((g) => g && g.focus())
          return f.focus()
        }
      }
      return self.clients.openWindow(ziel)
    }),
  )
})

/* ⚠️ **Der Push-Dienst darf ein Abonnement von sich aus erneuern.** Passiert
   das, ist die Adresse im Server veraltet, und jede weitere Meldung liefe ins
   Leere, ohne Fehler, den irgendjemand sähe. Der Worker meldet die neue
   Adresse deshalb sofort nach.

   ⚠️ **Ohne Anmeldung geht das nicht**, und das ist hinnehmbar: Nexview meldet
   sich mit einem Token an, das nur die Oberfläche kennt. Der Aufruf scheitert
   dann mit 401, und beim nächsten Öffnen der Oberfläche meldet sie das Gerät
   ohnehin neu an. Wo die Sitzung über ein Cookie läuft, kommt er durch. */
self.addEventListener('pushsubscriptionchange', (ereignis) => {
  const neu = ereignis.newSubscription
  if (!neu) return
  const roh = neu.toJSON()
  ereignis.waitUntil(
    fetch(new URL('api/push/devices', self.registration.scope).href, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        endpoint: roh.endpoint,
        p256dh: roh.keys.p256dh,
        auth: roh.keys.auth,
        language: 'en',
      }),
    }).catch(() => undefined),
  )
})
