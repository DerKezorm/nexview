import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { BefundBereich, DashboardStand } from '../api/types'
import { Befundliste } from '../components/Befundliste'
import { befundZusammenfassung } from '../components/befundText'
import { Reiterreihe, type Reiter } from '../components/Reiterreihe'
import type { SymbolName } from '../components/Symbol'
import { Datentraegerring } from '../components/Datentraegerring'
import { LaufendeWiedergaben } from '../components/LaufendeWiedergaben'
import { Verlaufsdiagramm } from '../components/Verlaufsdiagramm'
import { Card, ErrorBanner, Kennzahl, Spinner } from '../components/ui'

/**
 * Das Betreiber-Dashboard — der eine Ort, an dem man einmal am Tag nachsieht.
 *
 * ⚠️ **Hier steht, was zu tun ist. Nicht, wie es sich entwickelt.** Die
 * Statistik-Seite beantwortet „wie läuft es", diese Seite „was ist kaputt".
 * Ohne diese Trennung entstehen zwei Seiten, die dasselbe zeigen, und man
 * macht keine von beiden mehr auf.
 *
 * Zwei Sorten Zahl stehen hier, und der Unterschied ist der ganze Punkt:
 *
 * * **Handlungszahlen** stehen immer da. „Drei Anfragen warten auf Freigabe"
 *   ist kein Problem, sondern Alltag.
 * * **Befunde** sind Ausnahmen und verschwinden, sobald es behoben ist. Wären
 *   die Alltagszahlen auch Befunde, stünde dauerhaft ein Alarm da — und die
 *   echten Befunde daneben wären nichts mehr wert.
 */
/** Kategorie-Knopf: "alle" plus je ein Bereich. */
type Kategorie = 'alle' | BefundBereich

/**
 * Reihenfolge und Symbol je Kategorie.
 *
 * ⚠️ **Nicht alphabetisch, sondern nach Dringlichkeit.** Ein ausgefallener
 * Dienst legt alles lahm, ein Aufräum-Hinweis nichts — und die Reihenfolge der
 * Knöpfe ist das Erste, was jemand liest.
 */
const KATEGORIEN: { wert: BefundBereich; symbol: SymbolName }[] = [
  { wert: 'dienste', symbol: 'dienste' },
  { wert: 'platz', symbol: 'kontingent' },
  { wert: 'nachschub', symbol: 'herunterladen' },
  { wert: 'bibliothek', symbol: 'film' },
  { wert: 'abgleich', symbol: 'analyse' },
  { wert: 'betrieb', symbol: 'system' },
]

export function AdminDashboardPage() {
  const { t } = useTranslation()
  const [kategorie, setKategorie] = useState<Kategorie>('alle')
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['admin-dashboard'],
    queryFn: () => api.get<DashboardStand>('/api/admin/dashboard'),
    // Dieselbe Taktung wie die übrigen Zähler am Menü. Der Rundgang selbst
    // läuft alle 120 s; häufiger zu fragen brächte nichts Neues.
    refetchInterval: 60_000,
  })

  const stand = query.data

  // ⚠️ **Das Vermerken gehört hierher, nicht in die Abfrage.** Das Menü fragt
  // denselben Endpunkt im Minutentakt ab; zählte schon das Abfragen als
  // gesehen, wäre das Abzeichen nie zu sehen.
  //
  // Nach dem Vermerken wird der Stand neu geholt, damit das Abzeichen sofort
  // verschwindet statt erst beim nächsten Takt.
  useEffect(() => {
    if (!stand) return
    let abgebrochen = false
    void api
      .post('/api/admin/dashboard/gesehen')
      .then(() => {
        if (!abgebrochen) void queryClient.invalidateQueries({ queryKey: ['admin-dashboard'] })
      })
      // Ein misslungenes Vermerken ist kein Grund, die Seite zu stören: Die
      // Befunde stehen ja da. Es bleibt beim Abzeichen, das ist ertragbar.
      .catch(() => {})
    return () => {
      abgebrochen = true
    }
    // Nur beim ersten Stand, nicht bei jedem 60-Sekunden-Takt - sonst schriebe
    // die Seite im Minutentakt, solange sie offen liegt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stand !== undefined])

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('dashboard.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="text-sm text-mist-500">{t('dashboard.subtitle')}</p>
      </header>

      {query.isLoading && (
        <div className="flex justify-center py-12">
          <Spinner className="h-6 w-6" />
        </div>
      )}
      {query.error && <ErrorBanner message={String(query.error)} />}

      {stand && (
        <>
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Kennzahl
              label={t('dashboard.pendingApprovals')}
              wert={String(stand.zahlen.freigaben_offen)}
              hinweis={t('dashboard.pendingApprovalsHint')}
              // Eingefärbt, aber ausdrücklich kein Befund: Es ist Alltag,
              // nur eben Alltag, der auf jemanden wartet.
              ton={stand.zahlen.freigaben_offen > 0 ? 'warn' : 'normal'}
            />
            <Kennzahl
              label={t('dashboard.running')}
              wert={String(stand.zahlen.laeuft)}
              hinweis={t('dashboard.runningHint')}
            />
            <Kennzahl
              label={t('dashboard.openTickets')}
              wert={String(stand.zahlen.tickets_offen)}
              hinweis={t('dashboard.openTicketsHint')}
              ton={stand.zahlen.tickets_offen > 0 ? 'warn' : 'normal'}
            />
            <Kennzahl
              label={t('dashboard.openFeedback')}
              wert={String(stand.zahlen.rueckmeldungen_offen)}
              hinweis={t('dashboard.openFeedbackHint')}
              ton={stand.zahlen.rueckmeldungen_offen > 0 ? 'warn' : 'normal'}
            />
          </section>

          {/* ⚠️ **Nur Kategorien, in denen wirklich etwas steht.** Ein Knopf
              „Abgleich" ohne Abgleich-Befund dahinter ist ein Versprechen ohne
              Inhalt — man klickt ihn genau einmal. */}
          {/* Verschwindet, wenn nichts laeuft - auf dem Dashboard zaehlt jede
              Zeile, und "gerade schaut niemand" ist keine Nachricht. */}
          <LaufendeWiedergaben kompakt />

          <Card className="flex flex-col gap-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">{t('dashboard.findings')}</h2>
              {stand.befunde.length > 0 && (
                <p className="text-sm text-mist-500">
                  {befundZusammenfassung(stand.zaehler, t)}
                </p>
              )}
            </div>

            {stand.befunde.length > 0 && (
              <Reiterreihe
                eintraege={[
                  {
                    value: 'alle' as Kategorie,
                    label: t('dashboard.allCategories'),
                    symbol: 'befund',
                    abzeichen: String(stand.befunde.length),
                  },
                  ...KATEGORIEN.filter((k) =>
                    stand.befunde.some((b) => b.bereich === k.wert),
                  ).map<Reiter<Kategorie>>((k) => ({
                    value: k.wert,
                    label: t(`befund.bereich.${k.wert}`),
                    symbol: k.symbol,
                    abzeichen: String(
                      stand.befunde.filter((b) => b.bereich === k.wert).length,
                    ),
                  })),
                ]}
                aktiv={kategorie}
                onWechsel={setKategorie}
                label={t('dashboard.findings')}
              />
            )}

            <Befundliste
              befunde={
                kategorie === 'alle'
                  ? stand.befunde
                  : stand.befunde.filter((b) => b.bereich === kategorie)
              }
              // Überschriften nur in der Gesamtsicht: Auf einem Reiter
              // „Dienste" wäre eine Überschrift „Dienste" reines Papier.
              gruppiert={kategorie === 'alle'}
            />
          </Card>

          {/* Das einzige Diagramm auf dieser Seite - und es steht hier, weil
              „die Platte ist in sechs Wochen voll" eine Steigung ist. Alles
              Weitere gehört auf die Analyse-Seite; ein Dashboard, das man
              studieren muss, sieht man sich nicht täglich an. */}
          {(kategorie === 'alle' || kategorie === 'platz') &&
            (stand.traeger || stand.verlauf.length > 0) && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {stand.traeger && (
                <Card className="flex flex-col gap-3">
                  <h2 className="text-lg font-semibold">{t('dashboard.disk')}</h2>
                  <Datentraegerring traeger={stand.traeger} />
                </Card>
              )}
              {stand.verlauf.length > 0 && (
                <Card className="flex flex-col gap-3">
                  <h2 className="text-lg font-semibold">{t('dashboard.trend')}</h2>
                  <Verlaufsdiagramm punkte={stand.verlauf} />
                </Card>
              )}
            </div>
          )}

          {/* Die dreissig Einstellungsseiten bleiben, wo sie sind — dieses
              Dashboard ersetzt sie nicht, es zeigt nur hin. */}
          <Card className="flex flex-wrap gap-2">
            <Ziel to="/admin/requests" text={t('nav.allRequests')} />
            <Ziel to="/admin/stats" text={t('nav.stats')} />
            <Ziel to="/admin/settings" text={t('nav.settings')} />
          </Card>
        </>
      )}
    </div>
  )
}

function Ziel({ to, text }: { to: string; text: string }) {
  return (
    <Link
      to={to}
      className="rounded-full border border-ink-700 px-4 py-2 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100"
    >
      {text}
    </Link>
  )
}
