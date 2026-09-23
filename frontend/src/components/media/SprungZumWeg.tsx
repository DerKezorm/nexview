import { useTranslation } from "react-i18next";

import type { MediaType } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { useConfig } from "../../hooks/useConfig";

/**
 * „In der Beschaffung öffnen" – der Sprung in die Oberfläche des Wegs.
 *
 * ⚠️ **Nur für Entscheider.** Was dort steht, ist Betreibersache: Profile,
 * Ordner, Downloads. Ein Verweis für alle führte jeden Anfragenden an eine
 * Anmeldemaske, die er nicht bedienen kann.
 *
 * ⚠️ **Nur mit eingetragener Adresse nach außen.** Der Weg liefert die
 * Vorlage nur, wenn dort eine Adresse steht (gemessen: sonst `null`). Nexviews
 * eigene Sicht einzusetzen führte einen Besucher von draußen ins Leere –
 * deshalb entscheidet das der Server, nicht diese Datei.
 */
export function SprungZumWeg({
  mediaType,
  tmdbId,
}: {
  mediaType: MediaType;
  tmdbId: number;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { data: config } = useConfig();

  const vorlage = config?.beschaffung_sprung?.titel;
  if (!vorlage || !user?.can_approve) return null;

  const adresse = vorlage
    .replace("{kind}", mediaType === "movie" ? "movie" : "series")
    .replace("{ref}", `tmdb:${tmdbId}`);

  return (
    <a
      className="text-sm text-accent-400 hover:underline"
      href={adresse}
      target="_blank"
      rel="noreferrer"
    >
      {t("beschaffung.openThere")}
    </a>
  );
}
