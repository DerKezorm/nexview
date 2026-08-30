import { useTranslation } from 'react-i18next'

import type { WiedergabeStand } from '../../api/types'
import { Avatar } from '../../components/Avatar'
import { LaufendeWiedergaben } from '../../components/LaufendeWiedergaben'
import { Card, Kennzahl } from '../../components/ui'
import { formatDate, formatSize } from '../../lib/format'

/**
 * Reiter „Wiedergabe" — wer schaut, und wie die Bibliothek gewachsen ist.
 *
 * ⚠️ **Mit Namen, und das ist eine bewusste Kehrtwende.** Bis 0.24 sah auch
 * der Administrator nur seine eigenen Marker — aus Datensparsamkeit, und das
 * war ausdrücklich so entschieden. Der Betreiber hat es am 30.08.2026
 * aufgehoben: Ein Werkzeug, das beim Verwalten helfen soll, muss sagen dürfen,
 * wer zieht und wer nicht. Wer den alten Kommentar in
 * `services/mediaserver_watched.py` liest, soll hier nachlesen können, warum
 * er nicht mehr gilt.
 *
 * ⚠️ **Ein Marker ist kein Abspielzähler.** Nexview weiß „gesehen / nicht
 * gesehen" je Person, nicht wie oft. Alles auf dieser Seite muss so formuliert
 * sein, dass es das nicht behauptet.
 */
export function AnalyseWiedergabe({ stand }: { stand: WiedergabeStand }) {
  const { t, i18n } = useTranslation()

  const gesamtWiedergaben = stand.monate.reduce((summe, m) => summe + m.anzahl, 0)

  /**
   * Der Anteil - oder eingestanden, dass es keinen gibt.
   *
   * ⚠️ **Hier stand „0 %".** Der Teiler war gegen null abgesichert, die
   * Aussage nicht: Neben „0 %" stand „2 von 0 Titeln", und wer 2 gesehen hat,
   * kann nicht 0 im Bestand haben. Ein Widerspruch in der ersten Kennzahl, die
   * ein Betreiber auf dieser Seite ansieht.
   *
   * ⚠️ **Und es ist kein Fall aus der Vorführung.** ``bestand_gesamt`` ist
   * auch dann null, wenn der Bibliotheks-Abgleich noch nicht gelaufen ist oder
   * Nexview die Bibliotheken des Medienservers nicht lesen kann - also genau
   * bei einem frisch verbundenen Server. Der erste Blick eines neuen
   * Betreibers trifft damit auf eine Zahl, die sich selbst widerspricht.
   *
   * Ohne Bestand ist der Anteil nicht null, sondern **unbekannt**. Ein Strich
   * sagt das; eine Null behauptet etwas.
   */
  const bestandBekannt = stand.bestand_gesamt > 0
  const anteil = bestandBekannt
    ? `${Math.round((stand.angesehen / stand.bestand_gesamt) * 100)} %`
    : '—'

  return (
    <div className="flex flex-col gap-6">
      {/* Ganz oben: Was jetzt passiert, schlaegt alles, was war. */}
      <LaufendeWiedergaben />

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kennzahl
          label={t('wiedergabe.total')}
          wert={String(gesamtWiedergaben)}
          hinweis={t('wiedergabe.totalHint', { monate: stand.monate.length })}
        />
        <Kennzahl
          label={t('wiedergabe.watchers')}
          wert={String(stand.konten_mit_daten)}
          hinweis={t('wiedergabe.watchersHint')}
        />
        <Kennzahl
          label={t('wiedergabe.touched')}
          wert={anteil}
          hinweis={
            bestandBekannt
              ? t('wiedergabe.touchedHint', {
                  gesehen: stand.angesehen,
                  gesamt: stand.bestand_gesamt,
                })
              : t('wiedergabe.touchedNoLibrary', { gesehen: stand.angesehen })
          }
        />
        <Kennzahl
          label={t('wiedergabe.libraryNow')}
          wert={String(stand.bestand.at(-1)?.posten ?? 0)}
          hinweis={formatSize(stand.bestand.at(-1)?.bytes ?? 0, i18n.language)}
        />
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card className="flex flex-col gap-3">
          <div>
            <h2 className="text-lg font-semibold">{t('wiedergabe.perMonth')}</h2>
            <p className="text-xs text-mist-600">{t('wiedergabe.perMonthHint')}</p>
          </div>
          <Saeulen
            punkte={stand.monate.map((m) => ({ name: m.monat, wert: m.anzahl }))}
          />
        </Card>

        <Card className="flex flex-col gap-3">
          <div>
            <h2 className="text-lg font-semibold">{t('wiedergabe.growth')}</h2>
            <p className="text-xs text-mist-600">{t('wiedergabe.growthHint')}</p>
          </div>
          <Saeulen
            punkte={stand.bestand.map((b) => ({ name: b.monat, wert: b.posten }))}
            // Der Bestand fängt nicht bei null an — er war schon da, bevor das
            // Fenster beginnt. Die Säulen zeigen deshalb den Zuwachs innerhalb
            // des Fensters, sonst wären alle achtzehn fast gleich hoch.
            basis={stand.bestand[0]?.posten ?? 0}
          />
        </Card>
      </div>

      {/* ⚠️ Erscheint erst, wenn wirklich gemessen wurde. Der Abtaster läuft
          seit diesem Update; auf einer frisch aktualisierten Anlage stünde
          sonst eine Überschrift über einer leeren Fläche. */}
      {stand.spitzen.length > 0 && (
        <Card className="flex flex-col gap-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="text-lg font-semibold">{t('wiedergabe.peak')}</h2>
              <p className="text-xs text-mist-600">{t('wiedergabe.peakHint')}</p>
            </div>
            <span className="text-sm text-mist-500">
              {t('wiedergabe.peakEver', { count: stand.spitze_gesamt })}
            </span>
          </div>
          <Saeulen
            punkte={stand.spitzen.map((s) => ({
              name: s.tag,
              wert: s.gleichzeitig,
            }))}
          />
        </Card>
      )}

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t('wiedergabe.people')}</h2>
        {stand.personen.length === 0 ? (
          <p className="text-sm text-mist-500">{t('wiedergabe.nobody')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {stand.personen.map((person) => (
              <li
                key={person.user_id ?? person.name}
                className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 px-3 py-2"
              >
                <Avatar url={person.avatar_url} name={person.name} />
                <span className="min-w-0 flex-1 truncate text-sm">{person.name}</span>
                {person.zuletzt && (
                  <span className="text-xs text-mist-600">
                    {t('wiedergabe.lastSeen', {
                      datum: formatDate(person.zuletzt.slice(0, 10), i18n.language),
                    })}
                  </span>
                )}
                <span className="rounded-full border border-ink-700 px-2 py-0.5 text-xs tabular-nums text-mist-400">
                  {t('wiedergabe.titles', { count: person.anzahl })}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs leading-relaxed text-mist-600">
          {t('wiedergabe.markerHint')}
        </p>
      </Card>

      {/* Erscheint nur, wenn ein Titel von mehr als einer Person gesehen wurde —
          sonst wäre die Liste eine Reihe von Einsen und täuschte eine Aussage
          vor, wo keine ist. */}
      {stand.beliebteste.length > 0 && (
        <Card className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">{t('wiedergabe.mostWatched')}</h2>
          {stand.beliebteste.map((titel) => (
            <div
              key={`${titel.media_type}-${titel.tmdb_id}`}
              className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 px-3 py-2"
            >
              <span className="min-w-0 flex-1 truncate text-sm">{titel.titel}</span>
              <span className="rounded-full border border-ink-700 px-2 py-0.5 text-xs tabular-nums text-mist-400">
                {t('wiedergabe.byPeople', { count: titel.anzahl })}
              </span>
            </div>
          ))}
        </Card>
      )}
    </div>
  )
}

/**
 * Eine Reihe Säulen. Bewusst schlicht: `div`s mit Höhe, kein SVG.
 *
 * `basis` zieht einen Sockel ab — bei einer Bestandskurve, die nicht bei null
 * beginnt, wären sonst alle Säulen gleich hoch und das Bild sagte nichts.
 */
function Saeulen({
  punkte,
  basis = 0,
}: {
  punkte: { name: string; wert: number }[]
  basis?: number
}) {
  const werte = punkte.map((p) => Math.max(0, p.wert - basis))
  const groesste = Math.max(1, ...werte)

  return (
    <div className="flex flex-col gap-2">
      {/* ⚠️ **`items-stretch`, nicht `items-end`.** Mit `items-end` schrumpfen
          die Spalten auf ihren Inhalt, und eine Säule mit `height: 40%` ist
          dann 40 % von null — sie war unsichtbar, während Achsen und
          Überschrift ganz normal dastanden. Die Spalte muss die volle Höhe
          haben, damit der Prozentwert etwas hat, worauf er sich bezieht;
          nach unten gedrückt wird die Säule vom `justify-end` der Spalte. */}
      <div className="flex h-32 items-stretch gap-1">
        {punkte.map((punkt, index) => (
          <div
            key={punkt.name}
            className="flex h-full flex-1 flex-col justify-end"
            title={`${punkt.name}: ${punkt.wert}`}
          >
            <div
              className="rounded-t bg-[var(--color-viz-1)]"
              style={{
                // Mindestens ein Pixel: Ein Monat ohne Wiedergabe ist eine
                // Aussage, eine unsichtbare Säule sieht aus wie fehlende Daten.
                height: `${Math.max(1, (werte[index] / groesste) * 100)}%`,
                opacity: werte[index] === 0 ? 0.25 : 1,
              }}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-xs text-mist-600">
        <span>{punkte[0]?.name}</span>
        <span>{punkte.at(-1)?.name}</span>
      </div>
    </div>
  )
}
