import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { AnalyseStand } from '../../api/types'
import { BereichsBefunde } from '../../components/BereichsBefunde'
import { Card, Kennzahl } from '../../components/ui'
import { formatDate } from '../../lib/format'

/**
 * Reiter „Betrieb" — wie es Nexview selbst geht.
 *
 * ⚠️ **Der einzige Reiter, der nicht von Radarr oder dem Medienserver
 * handelt.** Sicherungen, Postausgang, Protokoll: Das sind die Dinge, die
 * lautlos ausfallen. Ein stiller SMTP-Server fällt niemandem auf — die Glocke
 * funktioniert ja weiter, nur die Post kommt nie an.
 */
export function AnalyseBetrieb({ stand }: { stand: AnalyseStand }) {
  const { t, i18n } = useTranslation()
  const { betrieb } = stand

  return (
    <div className="flex flex-col gap-6">
      <BereichsBefunde bereiche={['betrieb']} />

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kennzahl
          label={t('analyse.backups')}
          wert={String(betrieb.sicherungen)}
          hinweis={
            betrieb.sicherung_letzte
              ? t('analyse.backupLast', {
                  datum: formatDate(
                    betrieb.sicherung_letzte.slice(0, 10),
                    i18n.language,
                  ),
                })
              : t('analyse.backupNone')
          }
          ton={betrieb.sicherung_letzte ? 'normal' : 'warn'}
        />
        <Kennzahl
          label={t('analyse.mailQueue')}
          wert={String(betrieb.mail_offen)}
          hinweis={t('analyse.mailQueueHint')}
        />
        <Kennzahl
          label={t('analyse.mailFailed')}
          wert={String(betrieb.mail_aufgegeben)}
          hinweis={t('analyse.mailFailedHint')}
          ton={betrieb.mail_aufgegeben > 0 ? 'warn' : 'normal'}
        />
        <Kennzahl
          label={t('analyse.logErrors')}
          wert={String(betrieb.protokoll_fehler_24h)}
          hinweis={t('analyse.logErrorsHint')}
          ton={betrieb.protokoll_fehler_24h > 0 ? 'warn' : 'normal'}
        />
      </section>

      <Card className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t('analyse.installation')}</h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <dt className="text-xs tracking-wide text-mist-600 uppercase">
              {t('analyse.version')}
            </dt>
            <dd className="tabular-nums text-mist-200">
              {betrieb.version}
              {betrieb.neueste_version && (
                <span className="ml-2 text-xs text-accent-400">
                  {t('analyse.newerAvailable', { version: betrieb.neueste_version })}
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs tracking-wide text-mist-600 uppercase">
              {t('analyse.backupSchedule')}
            </dt>
            <dd className="text-mist-200">
              {/* Die Takt-Namen stehen schon bei den Sicherungen — dort werden
                  sie eingestellt. Ein zweiter Satz derselben vier Wörter liefe
                  beim nächsten Umbenennen auseinander. */}
              {t(`backups.schedule_${betrieb.sicherung_takt}`, {
                defaultValue: betrieb.sicherung_takt,
              })}
            </dd>
          </div>
          <div>
            <dt className="text-xs tracking-wide text-mist-600 uppercase">
              {t('analyse.logLevel')}
            </dt>
            <dd className="text-mist-200">{betrieb.protokoll_stufe}</dd>
          </div>
        </dl>
        <div className="flex flex-wrap gap-2 pt-1">
          <Ziel to="/admin/settings?reiter=sicherungen" text={t('settings.tabBackups')} />
          <Ziel to="/admin/settings?reiter=protokoll" text={t('settings.tabLogs')} />
          <Ziel to="/admin/settings?reiter=mail" text={t('settings.tabMail')} />
        </div>
      </Card>
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
