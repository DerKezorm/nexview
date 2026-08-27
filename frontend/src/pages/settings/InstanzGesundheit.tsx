import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { GesundheitStand } from "../../api/types";

/**
 * Was die Instanz selbst als Problem meldet – der Warnkasten im Instanz-Block.
 *
 * Die Texte kommen im Wortlaut von Radarr/Sonarr (englisch) und werden
 * bewusst nicht übersetzt: Es ist ihre Aussage, nicht unsere. Gesund heißt:
 * kein Kasten – Abwesenheit ist hier die gute Nachricht.
 */
export function InstanzGesundheit({ kennung }: { kennung: string }) {
  const { t } = useTranslation();

  const query = useQuery({
    queryKey: ["instanz-gesundheit"],
    queryFn: () => api.get<GesundheitStand>("/api/settings/instanzen/gesundheit"),
  });

  const zeile = query.data?.instanzen.find(
    (eintrag) => eintrag.kennung === kennung,
  );
  if (!zeile || zeile.probleme.length === 0) {
    return null;
  }

  return (
    <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
      <p className="font-semibold">{t("settings.instanceReports")}</p>
      <ul className="mt-1 list-disc pl-4">
        {zeile.probleme.map((problem) => (
          <li key={problem.text}>{problem.text}</li>
        ))}
      </ul>
    </div>
  );
}
