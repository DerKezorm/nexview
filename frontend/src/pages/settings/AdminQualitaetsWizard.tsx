/**
 * Der Assistent, der aus Alltagsfragen ein Qualitätsprofil macht.
 *
 * ⚠️ **Warum überhaupt Fragen und nicht die Einstellungen von Radarr.** Ein
 * Profil besteht aus Dutzenden Erkennungsmustern mit Punktwerten - wer das
 * bedienen kann, braucht Nexview dafür nicht. Gefragt wird deshalb nach dem,
 * was jemand über sein Zuhause weiß, nicht über Radarr.
 *
 * ⚠️ **Keine Gerätefragen.** "Welchen Fernseher hast du" wäre falsch gestellt:
 * Wer Radarr betreibt, lädt für mehrere Leute mit verschiedenen Geräten. Gefragt
 * wird nach dem *Zweck des Profils* - und wer mehrere Zwecke hat, läuft den
 * Assistenten mehrmals.
 *
 * ⚠️ **Stand: Oberfläche ohne Hinterbau.** Die Antworten werden noch nicht in
 * ein echtes Profil übersetzt; am Ende entsteht ein Eintrag in der Ablage.
 * Die Übersetzung Antwort → TRaSH-Baustein ist eine feste Tabelle, kein
 * Ermessen - sie kommt mit dem Hinterbau.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Fenster } from '../../components/Fenster'
import { Button } from '../../components/ui'
import type { Antworten, Typ } from './qualitaetsprofile-typen'
import { LEERE_ANTWORTEN, SPRACHEN } from './qualitaetsprofile-typen'

/**
 * Die Schritte in ihrer Reihenfolge - die Fortschrittszeile liest daraus.
 *
 * ⚠️ **Die Reihenfolge hängt von der ersten Antwort ab.** Wer „einfach" wählt,
 * bekommt genau den bisherigen Weg; „ausführlich" schiebt drei Schritte davor
 * das Ende. Beide enden im selben Rezept — die zusätzlichen Antworten legen
 * nur weitere TRaSH-Gruppen obendrauf.
 */
const GRUNDSCHRITTE = ['modus', 'zweck', 'ziel', 'warten', 'quelle', 'sprachen', 'feinheiten'] as const
const ZUSATZ = ['ton', 'bild', 'gruppen'] as const
type Schritt = (typeof GRUNDSCHRITTE)[number] | (typeof ZUSATZ)[number] | 'fertig'

function schritteFuer(modus: string): Schritt[] {
  return modus === 'ausfuehrlich'
    ? [...GRUNDSCHRITTE, ...ZUSATZ, 'fertig']
    : [...GRUNDSCHRITTE, 'fertig']
}

export function AdminQualitaetsWizard({
  offen,
  onAbbrechen,
  onAnlegen,
  schonVorhanden,
}: {
  offen: boolean
  onAbbrechen: () => void
  /** Bekommt die fertigen Antworten - was daraus wird, entscheidet die Ablage. */
  onAnlegen: (antworten: Antworten) => void
  /**
   * Die Doppelprüfung der Ablage: Gibt es dieses Rezept schon? Liefert den
   * Namen des vorhandenen Profils, sonst null.
   *
   * Sie steckt bewusst nicht im Assistenten: Ob zwei Rezepte gleich sind, weiß
   * die Ablage - der Assistent kennt nur die Antworten vor sich.
   */
  schonVorhanden: (antworten: Antworten) => string | null
}) {
  const { t } = useTranslation()
  const [schritt, setSchritt] = useState<Schritt>('modus')
  const [a, setA] = useState<Antworten>(LEERE_ANTWORTEN)

  const setze = (teil: Partial<Antworten>) => setA((alt) => ({ ...alt, ...teil }))
  const SCHRITTE = schritteFuer(a.modus)
  // ⚠️ Nach einem Wechsel auf „einfach" kann der aktuelle Schritt fortgefallen
  // sein. Dann steht der Assistent auf etwas, das es nicht mehr gibt.
  const index = Math.max(0, SCHRITTE.indexOf(schritt))
  const doppelt = schritt === 'fertig' ? schonVorhanden(a) : null
  const pflichtAnzahl = a.sprachen.filter((c) => a.sprachRollen[c] === 'pflicht').length

  /** Ohne Namen kein Profil - sonst stünde in der Ablage eine namenlose Zeile. */
  const weiterErlaubt =
    (schritt !== 'zweck' || a.name.trim().length > 0) &&
    (schritt !== 'sprachen' || a.sprachen.length > 0)

  return (
    <Fenster
      offen={offen}
      titel={t('qualityWizard.windowTitle')}
      onSchliessen={onAbbrechen}
      /* ⚠️ Die Fußzeile liefert den einen Ausgang - deshalb blendet der Rahmen
         seinen eigenen Schließen-Knopf aus. Escape und ein Klick daneben
         schließen weiterhin. */
      fuss={
        <div className="flex w-full items-center justify-between">
          <Button
            type="button"
            variant="ghost"
            onClick={() => (index === 0 ? onAbbrechen() : setSchritt(SCHRITTE[index - 1]))}
          >
            {index === 0 ? t('qualityWizard.cancel') : t('qualityWizard.back')}
          </Button>
          {schritt === 'fertig' ? (
            <Button type="button" onClick={() => onAnlegen(a)} disabled={Boolean(doppelt)}>
              {t('qualityWizard.create')}
            </Button>
          ) : (
            <Button
              type="button"
              disabled={!weiterErlaubt}
              onClick={() => setSchritt(SCHRITTE[index + 1])}
            >
              {t('qualityWizard.next')}
            </Button>
          )}
        </div>
      }
    >
      <div className="flex flex-col gap-5">
      {/* Fortschritt: zeigt, wie viel noch kommt. Ohne das fühlt sich jede
          Frage an, als könnten noch zwanzig folgen. */}
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist-600">
        {SCHRITTE.map((s, i) => (
          <li key={s} className="flex items-center gap-2">
            <span
              className={
                i === index
                  ? 'font-semibold text-mist-100'
                  : i < index
                    ? 'text-ok-500'
                    : ''
              }
            >
              {i + 1} {t(`qualityWizard.step.${s}`)}
            </span>
            {i < SCHRITTE.length - 1 && <span className="text-ink-600">›</span>}
          </li>
        ))}
      </ol>

      {schritt === 'modus' && (
        <Frage titel={t('qualityWizard.modeTitle')} unter={t('qualityWizard.modeSub')}>
          <Wahl
            name="modus"
            wert={a.modus}
            onWahl={(v) => setze({ modus: v as Antworten['modus'] })}
            optionen={[
              {
                wert: 'einfach',
                titel: t('qualityWizard.modeSimple'),
                hinweis: t('qualityWizard.modeSimpleHint'),
              },
              {
                wert: 'ausfuehrlich',
                titel: t('qualityWizard.modeDetailed'),
                hinweis: t('qualityWizard.modeDetailedHint'),
              },
            ]}
          />
          <Erklaerung text={t('qualityWizard.modeExplain')} />
        </Frage>
      )}

      {schritt === 'zweck' && (
        <Frage titel={t('qualityWizard.purposeTitle')} unter={t('qualityWizard.purposeSub')}>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-mist-300">{t('qualityWizard.nameLabel')}</span>
            <input
              className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              value={a.name}
              onChange={(e) => setze({ name: e.target.value })}
              placeholder={t('qualityWizard.namePlaceholder')}
            />
          </label>
          <Wahl
            name="typ"
            wert={a.typ}
            onWahl={(v) => setze({ typ: v as Typ })}
            optionen={[
              { wert: 'radarr', titel: t('qualityWizard.typeMovies'), hinweis: t('qualityWizard.typeMoviesHint') },
              { wert: 'sonarr', titel: t('qualityWizard.typeShows'), hinweis: t('qualityWizard.typeShowsHint') },
            ]}
          />
          <Erklaerung text={t('qualityWizard.purposeExplain')} />
        </Frage>
      )}

      {schritt === 'ziel' && (
        <Frage titel={t('qualityWizard.targetTitle')} unter={t('qualityWizard.targetSub')}>
          <Wahl
            name="ziel"
            wert={a.aufloesung}
            onWahl={(v) => setze({ aufloesung: v as Antworten['aufloesung'] })}
            optionen={[
              { wert: '2160p', titel: t('qualityWizard.res4k'), hinweis: t('qualityWizard.res4kHint') },
              { wert: '1080p', titel: t('qualityWizard.res1080'), hinweis: t('qualityWizard.res1080Hint') },
            ]}
          />
          <Erklaerung text={t('qualityWizard.targetExplain')} />
        </Frage>
      )}

      {schritt === 'warten' && (
        <Frage titel={t('qualityWizard.waitTitle')} unter={t('qualityWizard.waitSub')}>
          <Wahl
            name="warten"
            wert={a.sofortNehmen ? 'sofort' : 'warten'}
            onWahl={(v) => setze({ sofortNehmen: v === 'sofort' })}
            optionen={[
              { wert: 'sofort', titel: t('qualityWizard.takeNow'), hinweis: t('qualityWizard.takeNowHint') },
              { wert: 'warten', titel: t('qualityWizard.waitFor'), hinweis: t('qualityWizard.waitForHint') },
            ]}
          />
          {a.sofortNehmen && <Warnung text={t('qualityWizard.takeNowWarning')} />}
          <Erklaerung text={t('qualityWizard.waitExplain')} />
        </Frage>
      )}

      {schritt === 'quelle' && (
        <Frage titel={t('qualityWizard.sourceTitle')} unter={t('qualityWizard.sourceSub')}>
          <Wahl
            name="quelle"
            wert={a.quelle}
            onWahl={(v) => setze({ quelle: v as Antworten['quelle'] })}
            optionen={[
              {
                wert: 'encodes',
                titel: t('qualityWizard.srcEncodes'),
                hinweis: t('qualityWizard.srcEncodesHint'),
                abzeichen: { text: t('qualityWizard.badgeBalanced'), ton: 'neutral' },
              },
              {
                wert: 'remux',
                titel: t('qualityWizard.srcRemux'),
                hinweis: t('qualityWizard.srcRemuxHint'),
                abzeichen: { text: t('qualityWizard.badgeBest'), ton: 'gut' },
              },
              {
                wert: 'web',
                titel: t('qualityWizard.srcWeb'),
                hinweis: t('qualityWizard.srcWebHint'),
                abzeichen: { text: t('qualityWizard.badgeThrifty'), ton: 'sparsam' },
              },
            ]}
          />
          <Erklaerung text={t('qualityWizard.sourceExplain')} />
        </Frage>
      )}

      {schritt === 'sprachen' && (
        <Frage titel={t('qualityWizard.langTitle')} unter={t('qualityWizard.langSub')}>
          <div className="flex flex-col gap-2">
            {SPRACHEN.map((s) => {
              const an = a.sprachen.includes(s.code)
              const rolle = a.sprachRollen[s.code] ?? 'bevorzugt'
              return (
                <div
                  key={s.code}
                  className={
                    'flex flex-wrap items-center gap-3 rounded-xl border px-3 py-2 ' +
                    (an ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900')
                  }
                >
                  <label className="flex flex-1 cursor-pointer items-center gap-3">
                    <input
                      type="checkbox"
                      checked={an}
                      onChange={() => {
                        const rollen = { ...a.sprachRollen }
                        if (an) {
                          delete rollen[s.code]
                        } else {
                          // Die erste gewählte Sprache ist die, ohne die es nicht
                          // geht - alles Weitere kommt als Zugabe dazu. Wer es
                          // anders will, stellt es daneben um.
                          rollen[s.code] = a.sprachen.length === 0 ? 'pflicht' : 'bevorzugt'
                        }
                        setze({
                          sprachen: an
                            ? a.sprachen.filter((x) => x !== s.code)
                            : [...a.sprachen, s.code],
                          sprachRollen: rollen,
                        })
                      }}
                      className="h-4 w-4 shrink-0 accent-accent-500"
                    />
                    <span className="text-sm text-mist-200">{t(s.labelKey)}</span>
                    {!s.ausgearbeitet && (
                      <span className="text-xs text-mist-600">{t('qualityWizard.langSimple')}</span>
                    )}
                  </label>
                  {an && (
                    <div className="flex shrink-0 gap-1">
                      {(['pflicht', 'bevorzugt'] as const).map((r) => (
                        <button
                          key={r}
                          type="button"
                          onClick={() =>
                            setze({ sprachRollen: { ...a.sprachRollen, [s.code]: r } })
                          }
                          className={
                            'rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ' +
                            (rolle === r
                              ? 'border-accent-500 bg-accent-500 text-white'
                              : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-200')
                          }
                        >
                          {t(`qualityWizard.role_${r}`)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Nur bei mehreren Pflichtsprachen gibt es überhaupt etwas zu
              entscheiden - sonst wäre die Frage eine ohne Antwortmöglichkeit. */}
          {pflichtAnzahl > 1 && (
            <Wahl
              name="mehrere-pflicht"
              legende={t('qualityWizard.multiRequiredQuestion', { anzahl: pflichtAnzahl })}
              wert={a.mehrerePflicht}
              onWahl={(v) => setze({ mehrerePflicht: v as Antworten['mehrerePflicht'] })}
              optionen={[
                { wert: 'alle', titel: t('qualityWizard.langAll'), hinweis: t('qualityWizard.langAllHint') },
                { wert: 'eine', titel: t('qualityWizard.langOne'), hinweis: t('qualityWizard.langOneHint') },
              ]}
            />
          )}
          {a.sprachen.length > 0 && pflichtAnzahl === 0 && (
            <Warnung text={t('qualityWizard.noRequiredWarning')} />
          )}
          <Erklaerung text={t('qualityWizard.langExplain')} />
        </Frage>
      )}

      {schritt === 'feinheiten' && (
        <Frage titel={t('qualityWizard.fineTitle')} unter={t('qualityWizard.fineSub')}>
          <Wahl
            name="hdr"
            legende={t('qualityWizard.hdrQuestion')}
            wert={a.hdr}
            onWahl={(v) => setze({ hdr: v as Antworten['hdr'] })}
            optionen={[
              { wert: 'netz', titel: t('qualityWizard.hdrSafe'), hinweis: t('qualityWizard.hdrSafeHint') },
              { wert: 'frei', titel: t('qualityWizard.hdrFree'), hinweis: t('qualityWizard.hdrFreeHint') },
              { wert: 'egal', titel: t('qualityWizard.hdrNone'), hinweis: t('qualityWizard.hdrNoneHint') },
            ]}
          />
          <Wahl
            name="schluss"
            legende={t('qualityWizard.stopQuestion')}
            wert={a.schlusspunkt}
            onWahl={(v) => setze({ schlusspunkt: v as Antworten['schlusspunkt'] })}
            optionen={[
              { wert: 'trash', titel: t('qualityWizard.stopTrash'), hinweis: t('qualityWizard.stopTrashHint') },
              { wert: 'frueh', titel: t('qualityWizard.stopEarly'), hinweis: t('qualityWizard.stopEarlyHint') },
            ]}
          />
        </Frage>
      )}

      {schritt === 'ton' && (
        <Frage titel={t('qualityWizard.audioTitle')} unter={t('qualityWizard.audioSub')}>
          <Wahl
            name="ton"
            wert={a.ton}
            onWahl={(v) => setze({ ton: v as Antworten['ton'] })}
            optionen={[
              { wert: 'bevorzugen', titel: t('qualityWizard.audioPrefer'), hinweis: t('qualityWizard.audioPreferHint') },
              { wert: 'egal', titel: t('qualityWizard.audioAny'), hinweis: t('qualityWizard.audioAnyHint') },
            ]}
          />
          <Wahl
            name="barrierefrei"
            legende={t('qualityWizard.a11yLegend')}
            wert={a.barrierefrei}
            onWahl={(v) => setze({ barrierefrei: v as Antworten['barrierefrei'] })}
            optionen={[
              { wert: 'egal', titel: t('qualityWizard.a11yKeep'), hinweis: t('qualityWizard.a11yKeepHint') },
              { wert: 'meiden', titel: t('qualityWizard.a11yAvoid'), hinweis: t('qualityWizard.a11yAvoidHint') },
            ]}
          />
          <Erklaerung text={t('qualityWizard.audioExplain')} />
        </Frage>
      )}

      {schritt === 'bild' && (
        <Frage titel={t('qualityWizard.videoTitle')} unter={t('qualityWizard.videoSub')}>
          <Wahl
            name="x265"
            legende={t('qualityWizard.x265Legend')}
            wert={a.x265}
            onWahl={(v) => setze({ x265: v as Antworten['x265'] })}
            optionen={[
              { wert: 'meiden', titel: t('qualityWizard.x265Avoid'), hinweis: t('qualityWizard.x265AvoidHint') },
              { wert: 'egal', titel: t('qualityWizard.x265Any'), hinweis: t('qualityWizard.x265AnyHint') },
            ]}
          />
          {/* SDR meiden ergibt nur Sinn, wo HDR überhaupt vorkommt. */}
          {a.aufloesung === '2160p' && (
            <Wahl
              name="sdr"
              legende={t('qualityWizard.sdrLegend')}
              wert={a.sdr}
              onWahl={(v) => setze({ sdr: v as Antworten['sdr'] })}
              optionen={[
                { wert: 'egal', titel: t('qualityWizard.sdrAny'), hinweis: t('qualityWizard.sdrAnyHint') },
                { wert: 'meiden', titel: t('qualityWizard.sdrAvoid'), hinweis: t('qualityWizard.sdrAvoidHint') },
              ]}
            />
          )}
          <Erklaerung text={t('qualityWizard.videoExplain')} />
        </Frage>
      )}

      {schritt === 'gruppen' && (
        <Frage titel={t('qualityWizard.groupsTitle')} unter={t('qualityWizard.groupsSub')}>
          <Wahl
            name="regionale_gruppen"
            legende={t('qualityWizard.regionalLegend')}
            wert={a.regionale_gruppen}
            onWahl={(v) => setze({ regionale_gruppen: v as Antworten['regionale_gruppen'] })}
            optionen={[
              { wert: 'bevorzugen', titel: t('qualityWizard.regionalPrefer'), hinweis: t('qualityWizard.regionalPreferHint') },
              { wert: 'egal', titel: t('qualityWizard.regionalAny'), hinweis: t('qualityWizard.regionalAnyHint') },
            ]}
          />
          {/* Schnittfassungen gibt es nur bei Filmen. */}
          {a.typ === 'radarr' && (
            <Wahl
              name="fassungen"
              legende={t('qualityWizard.versionsLegend')}
              wert={a.fassungen}
              onWahl={(v) => setze({ fassungen: v as Antworten['fassungen'] })}
              optionen={[
                { wert: 'egal', titel: t('qualityWizard.versionsAny'), hinweis: t('qualityWizard.versionsAnyHint') },
                { wert: 'bevorzugen', titel: t('qualityWizard.versionsPrefer'), hinweis: t('qualityWizard.versionsPreferHint') },
              ]}
            />
          )}
          <Wahl
            name="asiatische_dienste"
            legende={t('qualityWizard.asianLegend')}
            wert={a.asiatische_dienste}
            onWahl={(v) => setze({ asiatische_dienste: v as Antworten['asiatische_dienste'] })}
            optionen={[
              { wert: 'egal', titel: t('qualityWizard.asianNo'), hinweis: t('qualityWizard.asianNoHint') },
              { wert: 'dazu', titel: t('qualityWizard.asianYes'), hinweis: t('qualityWizard.asianYesHint') },
            ]}
          />
          <Erklaerung text={t('qualityWizard.groupsExplain')} />
        </Frage>
      )}

      {schritt === 'fertig' && (
        <Frage titel={t('qualityWizard.doneTitle', { name: a.name })} unter={t('qualityWizard.doneSub')}>
          {doppelt ? (
            <Warnung text={t('qualityWizard.duplicate', { name: doppelt })} />
          ) : (
            <div className="flex flex-col divide-y divide-ink-800 text-sm">
              <Zeile menge="1" text={t('qualityWizard.sumProfile', { name: a.name })} />
              <Zeile menge="~60" text={t('qualityWizard.sumFormats')} />
              <Zeile menge="0" text={t('qualityWizard.sumUntouched')} />
              {/* Die Sprachregel noch einmal im Klartext - sie ist die
                  Einstellung, die am ehesten dazu führt, dass gar nichts mehr
                  geladen wird. Wer sie hier liest, merkt einen Irrtum jetzt. */}
              <p className="pt-3 text-xs leading-relaxed text-mist-500">
                {pflichtAnzahl === 0
                  ? t('qualityWizard.sumLangNone')
                  : pflichtAnzahl === 1
                    ? t('qualityWizard.sumLangOne', {
                        sprache: t(
                          SPRACHEN.find(
                            (s) => a.sprachRollen[s.code] === 'pflicht' && a.sprachen.includes(s.code),
                          )?.labelKey ?? '',
                        ),
                      })
                    : t(
                        a.mehrerePflicht === 'alle'
                          ? 'qualityWizard.sumLangAll'
                          : 'qualityWizard.sumLangAny',
                        {
                          sprachen: SPRACHEN.filter(
                            (s) => a.sprachRollen[s.code] === 'pflicht' && a.sprachen.includes(s.code),
                          )
                            .map((s) => t(s.labelKey))
                            .join(', '),
                        },
                      )}
              </p>
            </div>
          )}
          <Erklaerung text={t('qualityWizard.doneExplain')} />
        </Frage>
      )}

      </div>
    </Fenster>
  )
}

/** Ein Schritt: Frage, Unterzeile, Inhalt - überall gleich aufgebaut. */
function Frage({
  titel,
  unter,
  children,
}: {
  titel: string
  unter: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-lg font-semibold text-mist-100">{titel}</h3>
        <p className="mt-1 text-sm text-mist-600">{unter}</p>
      </div>
      {children}
    </div>
  )
}

/** Eine Gruppe Auswahlknöpfe - Muster wie in den übrigen Einstellungen. */
function Wahl({
  name,
  legende,
  wert,
  onWahl,
  optionen,
}: {
  name: string
  legende?: string
  wert: string
  onWahl: (wert: string) => void
  optionen: {
    wert: string
    titel: string
    hinweis: string
    /** Kurzurteil, das beim Vergleichen hilft - "guter Kompromiss" statt drei Zeilen lesen. */
    abzeichen?: { text: string; ton: 'gut' | 'neutral' | 'sparsam' }
  }[]
}) {
  const TON = {
    gut: 'border-ok-500/50 bg-ok-500/10 text-ok-500',
    neutral: 'border-accent-500/50 bg-accent-500/10 text-accent-400',
    sparsam: 'border-ink-600 bg-ink-800 text-mist-400',
  }
  return (
    <fieldset className="flex flex-col gap-2">
      {legende && <legend className="mb-1 text-sm font-medium text-mist-300">{legende}</legend>}
      {optionen.map((o) => (
        <label
          key={o.wert}
          className={
            'flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 ' +
            (wert === o.wert ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900')
          }
        >
          <input
            type="radio"
            name={name}
            checked={wert === o.wert}
            onChange={() => onWahl(o.wert)}
            className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
          />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-mist-200">{o.titel}</span>
              {o.abzeichen && (
                <span
                  className={
                    'rounded-full border px-2 py-0.5 text-[0.65rem] font-semibold tracking-wide uppercase ' +
                    TON[o.abzeichen.ton]
                  }
                >
                  {o.abzeichen.text}
                </span>
              )}
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">{o.hinweis}</span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/**
 * Was die Antwort bewirkt - in Alltagssprache.
 *
 * Steht **unter** der Auswahl, nicht darüber: Erst entscheidet man, dann will
 * man wissen, was man da entschieden hat.
 */
function Erklaerung({ text }: { text: string }) {
  return (
    <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
      {text}
    </p>
  )
}

/** Für Folgen, die man kennen muss, bevor man weiterklickt. */
function Warnung({ text }: { text: string }) {
  return (
    <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs leading-relaxed text-warn-500">
      {text}
    </p>
  )
}

function Zeile({ menge, text }: { menge: string; text: string }) {
  return (
    <div className="flex items-baseline gap-3 py-2">
      <span className="w-10 shrink-0 text-right font-mono text-ok-500">{menge}</span>
      <span className="text-mist-300">{text}</span>
    </div>
  )
}
