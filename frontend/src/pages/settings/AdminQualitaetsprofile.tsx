/**
 * Die Ablage der Qualitätsprofile.
 *
 * ⚠️ **Warum das eine Ablage ist und kein Assistent.** Ein Profil entsteht
 * zwar über Fragen, aber es *lebt* hier: Es liegt in Nexview, und auf welche
 * Instanzen es geschoben wird, ist eine spätere und jederzeit wiederholbare
 * Entscheidung. Deshalb ist diese Liste die Hauptseite und der Assistent nur
 * ein Weg, einen Eintrag anzulegen.
 *
 * ⚠️ **Eine Zeile je Profil, nicht je Installation.** Wo ein Profil liegt,
 * steht als Marke *in* seiner Zeile. Vorher stand dasselbe Profil einmal pro
 * Instanz untereinander; das las sich wie mehrere Profile mit gleichem Namen,
 * und ein frisch angelegtes belegte sofort mehrere Zeilen, obwohl es noch
 * nirgends lag.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  Qualitaetsprofil,
  QualitaetsprofilAbgleich,
  TrashQuelle,
  VerbindungStand,
  VerteilenErgebnis,
} from '../../api/types'
import { Button, Card, ErrorBanner, Section, Spinner } from '../../components/ui'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Symbol } from '../../components/Symbol'
import { AdminQualitaetsWizard } from './AdminQualitaetsWizard'
import { AdminQualitaetsVerteilen } from './AdminQualitaetsVerteilen'
import { AdminQualitaetsUnterschiede } from './AdminQualitaetsUnterschiede'
import type { Antworten, Installation, Profil, Stand, Typ } from './qualitaetsprofile-typen'
import { fingerabdruck, kurzfassung } from './qualitaetsprofile-typen'

/** Farbe und Wortlaut je Zustand - an einer Stelle, damit Liste und Zählung nicht auseinanderlaufen. */
const STAND_STIL: Record<Stand, string> = {
  aktuell: 'border-ok-500/50 bg-ok-500/10 text-ok-500',
  update: 'border-warn-500/50 bg-warn-500/10 text-warn-500',
  angepasst: 'border-ink-600 bg-ink-800 text-mist-400',
  konflikt: 'border-bad-500/50 bg-bad-500/10 text-bad-500',
  fehlt: 'border-bad-500/50 bg-bad-500/10 text-bad-500',
  unerreichbar: 'border-ink-700 bg-ink-900 text-mist-600',
  pruefung: 'border-ink-700 bg-ink-900 text-mist-600',
  'nicht-installiert': 'border-ink-700 bg-ink-900 text-mist-600',
}

/** Bei welchen Zuständen es überhaupt etwas anzusehen gibt. */
const HAT_UNTERSCHIEDE = new Set<Stand>(['update', 'angepasst', 'konflikt'])

export function AdminQualitaetsprofile() {
  const { t } = useTranslation()

  /**
   * Die Instanzen kommen aus derselben Quelle wie überall sonst.
   *
   * ⚠️ **Nicht aus den Einstellungsfeldern zusammenbauen.** Der Anzeigename ist
   * frei wählbar, und wo keiner vergeben wurde, gilt ein Ersatzname - den
   * kennt aber nur der Server (``arr_instanzen``). Baut die Oberfläche ihn
   * selbst nach, heißt dieselbe Instanz hier anders als auf ihrer Kachel, und
   * niemand findet sie wieder. Derselbe Abfrage-Schlüssel wie im
   * Eltern-Bauteil: es entsteht kein zweiter Aufruf.
   */
  const verbindung = useQuery({
    queryKey: ['instanz-verbindung'],
    queryFn: () => api.get<VerbindungStand>('/api/settings/instanzen/verbindung'),
  })

  // Die Kennungen sind stabil ("radarr-standard", "sonarr-uhd", ...) - daran
  // hängt, welche Instanzen für ein Profil überhaupt infrage kommen.
  const instanzen = (verbindung.data?.instanzen ?? []).map((i) => ({
    kennung: i.kennung,
    name: i.name,
    typ: (i.kennung.startsWith('sonarr') ? 'sonarr' : 'radarr') as Typ,
  }))

  const queryClient = useQueryClient()
  const [wizardOffen, setWizardOffen] = useState(false)
  /** Welches Profil gerade verteilt wird - null heißt: keins. */
  const [verteileId, setVerteileId] = useState<number | null>(null)
  /**
   * Was beim letzten Verteilen auffiel.
   *
   * ⚠️ Diese Hinweise wurden bisher verworfen. Darunter ist der wichtigste
   * Befund, den das Schreiben liefern kann: dass ein wiederverwendetes
   * Erkennungsmuster **andere Regeln** trägt als der Bauplan will. Muster
   * werden allein am Namen wiedererkannt — stammt eines von jemand anderem,
   * verspricht das Profil etwas und zeigt auf eine ungeprüfte Regel.
   */
  const [hinweise, setHinweise] = useState<string[]>([])
  /** Welche Installation gerade im Vergleich steht. */
  const [diff, setDiff] = useState<{ profilId: number; kennung: string } | null>(null)
  /**
   * Welches Profil gelöscht werden soll — `null` heißt: nichts offen.
   *
   * ⚠️ **Rückfrage als eigenes Fenster, nicht als `window.confirm`.** Der
   * Browser-Dialog sieht aus wie eine Warnung des Browsers, nicht wie eine
   * Frage von Nexview — Chrome schreibt „Auf localhost:5180 wird Folgendes
   * angezeigt" darüber. Er ignoriert jede Gestaltung und kann vor allem nicht
   * zeigen, **wo** das Profil gerade liegt: genau die Angabe, an der die
   * Entscheidung hängt.
   *
   * Die Orte werden mitgenommen statt später nachgeschlagen — sonst zeigte der
   * Dialog eine Liste, die sich hinter ihm schon geändert haben kann.
   */
  const [zuLoeschen, setZuLoeschen] = useState<{
    profil: Profil
    liegtAuf: Installation[]
  } | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  /** Kurze Erfolgsmeldung, etwa nach dem Holen eines neuen Stands. */
  const [meldung, setMeldung] = useState<string | null>(null)

  const quelle = useQuery({
    queryKey: ['trash-quelle'],
    queryFn: () => api.get<TrashQuelle>('/api/settings/qualitaetsprofile/quelle'),
  })

  const ablage = useQuery({
    queryKey: ['qualitaetsprofile'],
    queryFn: () => api.get<Qualitaetsprofil[]>('/api/settings/qualitaetsprofile'),
  })

  /**
   * Der Abgleich mit den Instanzen - getrennt von der Liste geholt.
   *
   * ⚠️ Dafür muss jede Instanz gefragt werden. Zusammen mit der Liste geladen
   * würde eine stumme Instanz die ganze Seite aufhalten; so steht die Liste
   * sofort da und die Abzeichen kommen nach.
   */
  const abgleich = useQuery({
    queryKey: ['qualitaetsprofile-abgleich'],
    queryFn: () =>
      api.get<QualitaetsprofilAbgleich[]>('/api/settings/qualitaetsprofile/abgleich'),
    staleTime: 30_000,
  })

  const standVon = (profilId: number, kennung: string) =>
    abgleich.data?.find((a) => a.profil_id === profilId && a.kennung === kennung)

  const profile: Profil[] = (ablage.data ?? []).map((p) => ({
    id: String(p.id),
    name: p.name,
    typ: p.dienst,
    zweck: kurzfassung(p.rezept as unknown as Antworten, t),
    rezept: p.rezept as unknown as Antworten,
    installationen: p.installationen.map((i) => ({
      instanz: i.kennung,
      // Solange der Abgleich läuft, steht dort noch nichts - dann lieber
      // "wird geprüft" als ein geratenes Urteil.
      stand: (standVon(p.id, i.kennung)?.stand ??
        (abgleich.isLoading ? 'pruefung' : 'unerreichbar')) as Stand,
    })),
  }))

  const melden = (ausnahme: unknown) =>
    setFehler(ausnahme instanceof ApiError ? ausnahme.message : String(ausnahme))
  const frisch = () => {
    setFehler(null)
    void queryClient.invalidateQueries({ queryKey: ['qualitaetsprofile'] })
    // Der Abgleich gehört dazu: Nach dem Schreiben stimmt der alte nicht mehr.
    void queryClient.invalidateQueries({ queryKey: ['qualitaetsprofile-abgleich'] })
  }

  /**
   * Erste Ebene der Doppelprüfung: Habe ich dieses Rezept schon in Nexview?
   *
   * Sie vergleicht die Antworten, nicht die Struktur des fertigen Profils -
   * das ist billig und fängt den häufigsten Fall ab, dass jemand den
   * Assistenten mehrfach mit denselben Antworten durchläuft. Die zweite Ebene
   * - gibt es auf DIESER Instanz schon ein strukturgleiches, womöglich fremdes
   * Profil? - kann erst ein Abgleich mit der Instanz beantworten.
   */
  const schonVorhanden = (a: Antworten) => {
    const neu = fingerabdruck(a)
    return profile.find((p) => p.rezept && fingerabdruck(p.rezept) === neu)?.name ?? null
  }

  /**
   * Den aktuellen Stand der Guides holen.
   *
   * Danach müssen Ablage **und** Abgleich neu geholt werden: Der Abgleich
   * entscheidet ja gerade daran, ob eine Kopie noch dem Stand entspricht.
   */
  const holenMut = useMutation({
    mutationFn: () =>
      api.post<{ stand: string }>(
        '/api/settings/qualitaetsprofile/quelle/aktualisieren',
      ),
    onSuccess: () => {
      setMeldung(t('qualityProfiles.fetchedOk'))
      void queryClient.invalidateQueries({ queryKey: ['trash-quelle'] })
      frisch()
    },
    onError: (ausnahme) => {
      setMeldung(null)
      melden(ausnahme)
    },
  })

  const anlegenMut = useMutation({
    mutationFn: (a: Antworten) =>
      api.post<Qualitaetsprofil>('/api/settings/qualitaetsprofile', {
        name: a.name.trim(),
        dienst: a.typ,
        rezept: a,
      }),
    onSuccess: () => {
      setWizardOffen(false)
      frisch()
    },
    onError: melden,
  })

  const loeschenMut = useMutation({
    mutationFn: (id: string) => api.delete(`/api/settings/qualitaetsprofile/${id}`),
    onSuccess: frisch,
    onError: melden,
  })

  /**
   * Das Verteilen: Die Liste der Kennungen ist die Wahrheit, kein Änderungssatz.
   *
   * Der Server schreibt daraufhin auf jede genannte Instanz und hört auf den
   * übrigen auf, das Profil zu verwalten - die Kopie dort bleibt aber stehen.
   */
  const verteilenMut = useMutation({
    mutationFn: ({ id, kennungen }: { id: string; kennungen: string[] }) =>
      api.put<VerteilenErgebnis>(
        `/api/settings/qualitaetsprofile/${id}/instanzen`,
        { kennungen },
      ),
    onSuccess: (daten) => {
      setVerteileId(null)
      // ⚠️ **Die Hinweise gehoeren gezeigt, nicht verworfen.**
      //
      // Hier steht unter anderem, dass ein wiederverwendetes Erkennungsmuster
      // **andere Regeln** traegt als der Bauplan will. Muster werden allein am
      // Namen wiedererkannt; stammt eines von jemand anderem, verspricht das
      // Profil "Deutsch Pflicht" und zeigt auf eine ungeprüfte Regel. Genau so
      // ein stiller Fehler soll hier laut werden.
      setHinweise(daten.hinweise ?? [])
      frisch()
    },
    onError: melden,
  })

  /** Was beim letzten Verteilen auffiel - bis zum naechsten Vorgang sichtbar. */
  const verteilen = (id: string, installationen: Installation[]) =>
    verteilenMut.mutate({
      id,
      kennungen: installationen
        .filter((i) => i.stand !== 'nicht-installiert')
        .map((i) => i.instanz),
    })

  const nameVon = (kennung: string) =>
    instanzen.find((i) => i.kennung === kennung)?.name ?? kennung

  /**
   * ⚠️ **Eine Zeile je Profil - nicht je Installation.**
   *
   * Vorher stand dasselbe Profil einmal pro Instanz untereinander. Das las sich
   * wie mehrere Profile mit gleichem Namen, und ein frisch angelegtes belegte
   * sofort mehrere Zeilen, obwohl es noch nirgends lag. Ein Profil ist **ein**
   * Ding in Nexview; wo es liegt, ist eine Eigenschaft davon und gehört in die
   * Zeile hinein, nicht daneben.
   *
   * Installationen auf Instanzen, die es nicht mehr gibt, fallen heraus.
   */
  const zeilen = profile.map((p) => ({
    profil: p,
    liegtAuf: p.installationen.filter((i) => instanzen.some((x) => x.kennung === i.instanz)),
  }))

  if (verbindung.isLoading || ablage.isLoading) {
    return (
      <div className="mt-6 flex justify-center py-10">
        <Spinner className="h-6 w-6" />
      </div>
    )
  }

  return (
    <div className="mt-6 flex flex-col gap-5">
      {/* Der Assistent liegt über der Ablage: Man sieht dahinter, wohin das
          Ergebnis wandert, und landet nach dem Schließen wieder genau dort.
          ⚠️ Er wird **abgehängt**, sobald er zu ist, statt nur versteckt: Sonst
          behält er seine Antworten, und der nächste Aufruf beginnt mitten in
          einem alten Durchlauf statt bei Frage eins. */}
      {wizardOffen && (
        <AdminQualitaetsWizard
          offen
          onAbbrechen={() => setWizardOffen(false)}
          onAnlegen={(a) => anlegenMut.mutate(a)}
          schonVorhanden={schonVorhanden}
        />
      )}

      {diff &&
        (() => {
          const stand = standVon(diff.profilId, diff.kennung)
          const profil = profile.find((p) => p.id === String(diff.profilId))
          if (!stand || !profil) return null
          return (
            <AdminQualitaetsUnterschiede
              profilname={profil.name}
              instanzname={nameVon(diff.kennung)}
              abgleich={stand}
              onSchliessen={() => setDiff(null)}
              onUebernehmen={() => {
                // Neu schreiben heißt: alles, wo es schon liegt, noch einmal.
                setDiff(null)
                setVerteileId(diff.profilId)
                verteilenMut.mutate({
                  id: String(diff.profilId),
                  kennungen: profil.installationen.map((i) => i.instanz),
                })
              }}
            />
          )
        })()}

      {verteileId !== null && profile.some((p) => p.id === String(verteileId)) && (
        <AdminQualitaetsVerteilen
          profil={profile.find((p) => p.id === String(verteileId))!}
          instanzen={instanzen}
          laeuft={verteilenMut.isPending}
          onSchliessen={() => setVerteileId(null)}
          onSpeichern={(inst) => verteilen(String(verteileId), inst)}
        />
      )}
      {/* ⚠️ Die Folge steht **vor** dem Klick, und sie ist keine Warnung,
          sondern eine Entwarnung: In Radarr passiert nichts. Wo das Profil
          liegt, steht namentlich dabei — ohne das müsste der Betreiber die
          Zeile dahinter im Kopf behalten. */}
      <ConfirmDialog
        open={zuLoeschen !== null}
        title={t('qualityProfiles.deleteTitle', { name: zuLoeschen?.profil.name ?? '' })}
        description={t('qualityProfiles.deleteBody')}
        warning={
          zuLoeschen?.liegtAuf.length
            ? t('qualityProfiles.deleteStays', {
                instanzen: zuLoeschen.liegtAuf.map((i) => nameVon(i.instanz)).join(' · '),
                count: zuLoeschen.liegtAuf.length,
              })
            : undefined
        }
        confirmLabel={t('qualityProfiles.delete')}
        loading={loeschenMut.isPending}
        onConfirm={() => {
          if (zuLoeschen) loeschenMut.mutate(zuLoeschen.profil.id)
          setZuLoeschen(null)
        }}
        onCancel={() => setZuLoeschen(null)}
      />

      <Section title={t('qualityProfiles.title')} breit>
        <p className="max-w-3xl text-sm text-mist-600">{t('qualityProfiles.intro')}</p>
        {fehler && <ErrorBanner message={fehler} />}
        {meldung && (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {meldung}
          </p>
        )}
        {/* Der Hinweis aus dem täglichen Nachsehen. Er sagt nur Bescheid -
            geholt wird nie von selbst, sonst verschöben sich die Profile in
            Radarr ohne Zutun. */}
        {quelle.data?.neuer_stand_da && !holenMut.isPending && (
          <p className="flex flex-wrap items-center gap-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
            {t('qualityProfiles.newStateAvailable', {
              datum: (quelle.data.neuer_stand_datum ?? '').slice(0, 10),
            })}
          </p>
        )}

        {/* Die Herkunft gehört sichtbar hierher, nicht ins Menü: Drinnen ist
            sie ein Vertrauensbeweis, draußen wäre sie nur Fachjargon. */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
          <span className="flex flex-wrap items-center gap-2 text-sm text-mist-500">
            {t('qualityProfiles.source', { datum: quelle.data?.stand ?? '…' })}
            {/* Die Quelle nachprüfbar machen: Wer wissen will, woher die
                Bewertungen kommen, soll nicht danach suchen müssen. */}
            <a
              href="https://github.com/TRaSH-Guides/Guides"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-900 px-2.5 py-1 text-xs font-medium text-mist-400 transition-colors hover:border-ink-600 hover:text-mist-100"
            >
              <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
                <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.53 2.34 1.09 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.56-1.11-4.56-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" />
              </svg>
              {t('qualityProfiles.sourceLink')}
            </a>
            <span className="text-xs text-mist-600">
              {quelle.data?.mitgeliefert
                ? t('qualityProfiles.sourceBundled')
                : t('qualityProfiles.sourceFetched', {
                    datum: (quelle.data?.geholt_am ?? '').slice(0, 10),
                  })}
            </span>
          </span>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => holenMut.mutate()}
              loading={holenMut.isPending}
            >
              {holenMut.isPending
                ? t('qualityProfiles.fetching')
                : t('qualityProfiles.fetchNow')}
            </Button>
            {instanzen.length > 0 && zeilen.length > 0 && (
              <Button type="button" onClick={() => setWizardOffen(true)}>
                {t('qualityProfiles.startWizard')}
              </Button>
            )}
          </div>
        </div>

        {zeilen.length === 0 ? (
          <Leerzustand
            hatInstanzen={instanzen.length > 0}
            onStart={() => setWizardOffen(true)}
          />
        ) : (
          <div className="flex flex-col gap-6">
            {/* ⚠️ Getrennt nach Dienst, nicht als eine lange Liste. Ein Profil
                gehört zu genau einem von beiden, und die Bausteine der zwei
                haben nichts gemeinsam - gemischt untereinander sähe es aus, als
                könnte man ein Filmprofil auf eine Serieninstanz schieben. */}
            {(['radarr', 'sonarr'] as const).map((dienst) => {
              const dieses = zeilen.filter((z) => z.profil.typ === dienst)
              const hatInstanz = instanzen.some((i) => i.typ === dienst)
              if (dieses.length === 0 && !hatInstanz) return null
              return (
                <section key={dienst} className="flex flex-col gap-3">
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-mist-300">
                    <Symbol name={dienst} />
                    {t(`qualityProfiles.section_${dienst}`)}
                    <span className="text-xs font-normal text-mist-600">
                      {t('qualityProfiles.sectionCount', { anzahl: dieses.length })}
                    </span>
                  </h3>
                  {dieses.length === 0 ? (
                    <p className="rounded-xl border border-dashed border-ink-700 px-4 py-3 text-xs text-mist-600">
                      {t(`qualityProfiles.sectionEmpty_${dienst}`)}
                    </p>
                  ) : (
                    dieses.map(({ profil, liegtAuf }) => (
              <div
                key={profil.id}
                className="flex flex-wrap items-start gap-4 rounded-xl border border-ink-700 bg-ink-900/50 px-4 py-3.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-mist-100">{profil.name}</div>
                  <div className="mt-0.5 text-xs text-mist-600">{profil.zweck}</div>

                  {/* Wo es liegt, gehört in die Zeile des Profils - nicht in
                      eine eigene Zeile je Ort. */}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {liegtAuf.length === 0 ? (
                      <span className="text-xs text-mist-600">
                        {t('qualityProfiles.nowhere')}
                      </span>
                    ) : (
                      liegtAuf.map((i) => {
                        const marke = (
                          <>
                            <span className="font-medium">{nameVon(i.instanz)}</span>
                            <span className="opacity-80">
                              {t(`qualityProfiles.state.${i.stand}`)}
                            </span>
                          </>
                        )
                        const klasse =
                          'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ' +
                          STAND_STIL[i.stand]
                        // Nur wo es etwas zu sehen gibt, ist die Marke auch ein
                        // Knopf - sonst klickt man ins Leere.
                        return HAT_UNTERSCHIEDE.has(i.stand) ? (
                          <button
                            key={i.instanz}
                            type="button"
                            onClick={() =>
                              setDiff({ profilId: Number(profil.id), kennung: i.instanz })
                            }
                            className={klasse + ' transition-colors hover:brightness-125'}
                          >
                            {marke}
                          </button>
                        ) : (
                          <span key={i.instanz} className={klasse}>
                            {marke}
                          </span>
                        )
                      })
                    )}
                  </div>
                </div>

                <div className="flex shrink-0 flex-wrap gap-2">
                  <Button
                    type="button"
                    variant={liegtAuf.length === 0 ? 'primary' : 'ghost'}
                    onClick={() => setVerteileId(Number(profil.id))}
                    loading={verteilenMut.isPending && verteileId === Number(profil.id)}
                  >
                    {liegtAuf.length === 0
                      ? t('qualityProfiles.install')
                      : t('qualityProfiles.distribute')}
                  </Button>
                  {/* Löschen heißt: aus Nexview. Was schon auf einer Instanz
                      liegt, bleibt dort - deshalb steht die Folge im Text und
                      nicht im Kleingedruckten. */}
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setZuLoeschen({ profil, liegtAuf })}
                    loading={loeschenMut.isPending && loeschenMut.variables === profil.id}
                  >
                    {t('qualityProfiles.delete')}
                  </Button>
                </div>
              </div>
                    ))
                  )}
                </section>
              )
            })}
          </div>
        )}

        {/* Was hier NICHT steht, ist so wichtig wie das, was dasteht - aber
            erst, wenn überhaupt eine Liste da ist, auf die sich der Satz
            beziehen kann. */}
        {hinweise.length > 0 && (
          <div className="rounded-xl border border-warn-500/50 bg-warn-500/10 px-4 py-3">
            <p className="text-sm font-medium text-warn-500">
              {t('qualityProfiles.noticeTitle')}
            </p>
            <ul className="mt-1.5 flex flex-col gap-1">
              {hinweise.map((h) => {
                const trenner = h.indexOf(':')
                const art = trenner > 0 ? h.slice(0, trenner) : ''
                const rest = trenner > 0 ? h.slice(trenner + 1).trim() : h
                return (
                  <li key={h} className="text-xs leading-relaxed text-mist-400">
                    {art === 'fremde_regeln'
                      ? t('qualityProfiles.foreignRules', { namen: rest })
                      : h}
                  </li>
                )
              })}
            </ul>
          </div>
        )}
        {zeilen.length > 0 && (
          <p className="max-w-3xl text-xs text-mist-600">{t('qualityProfiles.foreignHint')}</p>
        )}
      </Section>


    </div>
  )
}

/**
 * Der Leerzustand.
 *
 * Er unterscheidet zwei Fälle, weil sie verschiedene Antworten brauchen: Ohne
 * eingerichtete Instanz kann man gar nichts anlegen - da wäre ein Knopf eine
 * Sackgasse. Mit Instanz ist der Knopf der ganze Punkt der Seite.
 */
function Leerzustand({
  hatInstanzen,
  onStart,
}: {
  hatInstanzen: boolean
  onStart: () => void
}) {
  const { t } = useTranslation()
  return (
    <Card className="flex flex-col items-center gap-3 border-dashed py-10 text-center">
      <span className="text-mist-600">
        <Symbol name="qualitaet" className="h-8 w-8" />
      </span>
      <h3 className="text-base font-semibold text-mist-100">
        {t('qualityProfiles.emptyTitle')}
      </h3>
      <p className="max-w-md text-sm text-mist-600">
        {hatInstanzen ? t('qualityProfiles.emptyBody') : t('qualityProfiles.emptyNoInstance')}
      </p>
      {hatInstanzen && (
        <Button type="button" onClick={onStart} className="mt-1">
          {t('qualityProfiles.startWizard')}
        </Button>
      )}
    </Card>
  )
}
