import type { NexFassungZeile } from "../api/types";
import { FassungsZeile } from "./FassungsZeile";

/**
 * Der Rechte-Kasten je Fassung: Titel, Erklärtext, eine Zeile je Fassung mit
 * einem Haken "darf jeder anfragen".
 *
 * Ursprünglich Teil von `NexcrateStep` (dort ein einmaliger Vorschlag beim
 * Einrichten, erst mit dem Weiter-Knopf gespeichert). Seit C8 auch auf der
 * Dienste-Seite im NEX-Betrieb, wo ein Haken sofort speichert. Die Box selbst
 * kennt den Unterschied nicht - sie zeigt nur, was `offen` und `onToggle` ihr
 * vorgeben; wann und wie gespeichert wird, entscheidet der Aufrufer.
 */
export function FassungsRechte({
  fassungen,
  offen,
  onToggle,
  titel,
  text,
  disabled = false,
}: {
  fassungen: NexFassungZeile[];
  /** Kennung -> darf jeder anfragen. Fehlt ein Eintrag, gilt er als nicht angehakt. */
  offen: Record<string, boolean>;
  onToggle: (kennung: string, wert: boolean) => void;
  titel: string;
  text: string;
  disabled?: boolean;
}) {
  return (
    <div className="rounded-xl border border-ink-700 p-4">
      <p className="font-medium text-mist-100">{titel}</p>
      <p className="mt-1 text-sm leading-relaxed text-mist-500">{text}</p>
      <ul className="mt-3 flex flex-col gap-2">
        {fassungen.map((fassung) => (
          <FassungsZeile
            key={fassung.kennung}
            fassung={fassung}
            labelFor={`offen-${fassung.kennung}`}
            haken={
              <input
                id={`offen-${fassung.kennung}`}
                type="checkbox"
                className="h-4 w-4 shrink-0 accent-accent-500"
                checked={offen[fassung.kennung] ?? false}
                disabled={disabled}
                onChange={(event) => onToggle(fassung.kennung, event.target.checked)}
              />
            }
          />
        ))}
      </ul>
    </div>
  );
}
