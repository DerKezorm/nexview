import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { useConfig } from '../hooks/useConfig'
import { useRegionen } from '../hooks/useRegionen'

/**
 * Hinweis für alle, die nie eine eigene Region gewählt haben.
 *
 * Der Einrichtungsassistent fragt nicht danach, und das Feld beginnt leer -
 * also erbt die große Mehrheit stillschweigend die Vorgabe des Betreibers,
 * meist „DE". Für Kinostarts im Kalender ist das eine Ungenauigkeit. Für den
 * Satz „läuft in deinem Netflix" ist es eine **falsche Behauptung über einen
 * Menschen**: Der Katalog von Netflix Schweiz ist nicht der deutsche.
 *
 * Nur für die Betroffenen, wie beim abgelaufenen Medienserver-Zugang: Wer
 * seine Region gesetzt hat, sieht hier nie etwas. Und er verschwindet von
 * selbst, sobald sie gesetzt ist - eine Erinnerung, die man wegklicken muss,
 * obwohl die Sache erledigt ist, erzieht dazu, Balken ungelesen wegzuklicken.
 *
 * „Später" versteckt ihn nur für diese Sitzung und bewusst **nicht** dauerhaft:
 * Ein dauerhaftes Wegklicken hieße, mit einer falschen Region weiterzulaufen
 * und nie wieder daran erinnert zu werden. Gespeichert wird das im Browser,
 * nicht am Konto - dafür lohnt keine Spalte in der Datenbank.
 *
 * ⚠️ Der Schlüssel trägt die Kontonummer. Ohne sie galt ein „Später" für den
 * ganzen Browser: Einmal als Administrator weggeklickt, und das nächste Konto,
 * das sich in derselben Sitzung anmeldete, bekam den Hinweis nie zu sehen -
 * obwohl er für dieses Konto noch offen war. Ein Balken, der eine Aussage über
 * *ein* Konto macht, darf nicht am Browser hängen.
 */

const SITZUNGS_SCHLUESSEL = 'nexview.regionHinweis.spaeter'

function schluessel(kontoId: number) {
  return `${SITZUNGS_SCHLUESSEL}.${kontoId}`
}

export function RegionBanner() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: config } = useConfig()
  const regionen = useRegionen()
  const navigate = useNavigate()

  // Zaehler statt Zustand: Der Wert steht im Sitzungsspeicher und wird bei
  // jedem Zeichnen frisch gelesen. Ein einmal gesetzter Zustand ueberlebte
  // sonst den Kontowechsel, weil die Komponente dabei nicht neu entsteht.
  const [, neuZeichnen] = useState(0)

  // Kinderkonten haben kein Profil, in dem sie etwas einstellen könnten - und
  // keine eigenen Abos. Ein Hinweis, dem man nicht folgen kann, ist Lärm.
  if (!user || user.role === 'child') return null
  if (user.discover_region) return null
  if (sessionStorage.getItem(schluessel(user.id)) === '1') return null

  const vorgabe = config?.default_region ?? 'DE'
  const name =
    (regionen.data ?? []).find((eintrag) => eintrag.code === vorgabe)?.name ?? vorgabe

  return (
    <div className="relative z-10 border-b border-warn-500/40 bg-warn-500/10">
      <div className="mx-auto w-full max-w-7xl px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center gap-3 text-sm text-warn-500">
          <span>{t('regionHint.text', { region: name })}</span>
          <button
            type="button"
            onClick={() => navigate('/profil?reiter=sprache')}
            className="ml-auto rounded-full border border-warn-500/50 px-3 py-1 text-xs font-semibold transition-colors hover:bg-warn-500/15"
          >
            {t('regionHint.action')}
          </button>
          <button
            type="button"
            onClick={() => {
              sessionStorage.setItem(schluessel(user.id), '1')
              neuZeichnen((n) => n + 1)
            }}
            className="rounded-full px-3 py-1 text-xs font-semibold text-warn-500/80 transition-colors hover:text-warn-500"
          >
            {t('regionHint.later')}
          </button>
        </div>
      </div>
    </div>
  )
}
