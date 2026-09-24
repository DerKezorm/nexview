import type { TFunction } from 'i18next'

import type { AppConfig } from '../api/types'

/**
 * Ein Gesundheitsbefund aus nexcrate, übersetzt über seine Kennung.
 *
 * ⚠️ Nie nexcrates Satz: Der ist englisch und stand wörtlich in der deutschen
 * Oberfläche (Rundgang-Befund 5). `automatic_off` kommt je Art; die Art wählt
 * über den Kontext den Text (`automatic_off_movie`), sonst gilt der Grundtext.
 */
export function gesundheitsText(
  t: TFunction,
  code: string,
  params: Record<string, unknown> = {},
): string {
  return t(`nexcrate.health.${code}`, {
    ...params,
    context: typeof params.kind === 'string' ? params.kind : undefined,
    defaultValue: code,
  })
}

/**
 * Texte, die den Beschaffungsweg beim Namen nennen.
 *
 * ⚠️ **Bis zum 23.09.2026 nannten 41 Texte Radarr und Sonarr in beiden
 * Betriebsarten.** Wer über nexcrate beschafft, las dann „wird aus Radarr bzw.
 * Sonarr entfernt" oder „Es ist noch keine Radarr- oder Sonarr-Instanz
 * eingerichtet" – das eine falsch, das andere eine Anweisung, die in seiner
 * Installation nicht zu befolgen ist.
 *
 * Der Arr-Text bleibt, wie er ist; daneben steht `<schlüssel>_nex`, und
 * i18next wählt ihn über den Kontext. Fehlt einem Schlüssel die NEX-Fassung,
 * fällt i18next auf den Grundtext zurück. Deshalb darf der Kontext auch an
 * zusammengesetzten Schlüsseln wie `befund.${kennung}.titel` hängen.
 *
 * Nicht über einen Platzhalter `{{weg}}`: Manche Sätze sagen im NEX-Betrieb
 * inhaltlich etwas anderes (nexcrate nennt kein Datum, hat einen Papierkorb
 * mit Rückweg), und Meldungen aus dem Backend dürfen keine Platzhalter tragen.
 * Dort schickt der NEX-Weg gleich den Schlüssel `…_nex`.
 */
export function wegKontext(
  config: Pick<AppConfig, 'beschaffung'> | undefined | null,
): { context?: 'nex' } {
  return config?.beschaffung === 'nex' ? { context: 'nex' } : {}
}

/**
 * Die Schlüssel mit NEX-Fassung – gelesen vom Wächter in
 * `src/test/weg-texte.test.ts`. Mehrzahl ohne `_one`/`_other`.
 */
export const WEG_TEXTE = [
  'request.arrMissing',
  'request.moreSeasonsHint',
  'feedback.outdatedHint',
  'requests.cancelText',
  'requests.cancelTextAdmin',
  'requests.cancelTextNothing',
  'requests.cancelTextNothingAdmin',
  'logs.modeDesc.detailed',
  'adminUsers.dissolveItemsHint',
  'adminUsers.dissolveDeleteWarning',
  'adminUsers.dissolveOpenIntro',
  'notifications.instanceHealth',
  'mediaserver.libraryIntro',
  'about.tagline',
  'about.ratingsNotice',
  'channels.onRequestCancelledHint',
  'channels.onInstanceHealthHint',
  'storage.unmanagedHint',
  'storageAdmin.mustStayTitle',
  'storageAdmin.mustStayText',
  'storageAdmin.pointMeasure',
  'storageReleases.deleteReasonUnmanaged',
  'cleanup.noDateYet',
  'cleanup.deleteRecycleHint',
  'backups.downloadWhat',
  'restore.envKeyWarning',
  'restore.noKeyAtAll',
  'restore.outsideWarning',
  'befund.bibliothek.geisterposten.titel',
  'befund.bibliothek.geisterposten.folge',
  'befund.abgleich.arr_ohne_server.folge',
  'analyse.noInstances',
  'analyse.reconciliationHint',
  'analyse.arrOnly',
  'analyse.matrix.view.nur_arr',
  'analyse.matrix.match.arr',
  'analyse.matrix.help.ohne_kennung',
  'analyse.matrix.help.nur_arr',
] as const
