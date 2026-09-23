import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { NexFassungZeile } from "../api/types";

/**
 * Eine Fassung, wie nexcrate sie führt – Name, Medienart, Klasse, Zustand.
 *
 * ⚠️ **Die Medienart gehört dazu, nicht nur die Klasse.** nexcrate darf
 * Fassungen für Filme und Serien gleich benennen, und das tut es auch: Beim
 * ersten Umstieg an einer echten Anlage standen zwei Zeilen „Full-HD"
 * untereinander, ohne jeden Unterschied. Der Betreiber gibt hier Rechte frei
 * – er muss sehen, wofür.
 *
 * Zwei Listen zeigen dieselben Zeilen (Einrichtung und Dienste-Seite);
 * deshalb steht das Aussehen hier und nicht zweimal dort. `haken` bekommt die
 * Einrichtung mit, `labelFor` macht aus dem Namen dessen Beschriftung.
 */
export function FassungsZeile({
  fassung,
  haken,
  labelFor,
}: {
  fassung: NexFassungZeile;
  haken?: ReactNode;
  labelFor?: string;
}) {
  const { t } = useTranslation();
  const art = t(
    fassung.media_type === "movie" ? "common.movies" : "common.seriesPlural",
  );
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-700 px-3 py-2 text-sm">
      {haken}
      {labelFor ? (
        <label htmlFor={labelFor} className="font-medium text-mist-100">
          {fassung.name}
        </label>
      ) : (
        <span className="font-medium text-mist-100">{fassung.name}</span>
      )}
      <span className="rounded bg-ink-800 px-2 py-0.5 text-xs text-mist-300">
        {art}
      </span>
      {fassung.klasse && (
        <span className="rounded bg-ink-800 px-2 py-0.5 text-xs uppercase text-mist-300">
          {fassung.klasse}
        </span>
      )}
      <span className={fassung.bereit ? "text-green-400" : "text-amber-300"}>
        {fassung.bereit ? t("nexcrate.ready") : t("nexcrate.notReady")}
      </span>
      {fassung.gruende.map((grund) => (
        <span key={grund} className="text-xs text-mist-500">
          {t(`nexcrate.reason.${grund}`, { defaultValue: grund })}
        </span>
      ))}
    </li>
  );
}
