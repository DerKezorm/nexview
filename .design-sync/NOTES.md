# Nexview → Claude Design — Notizen zum Abgleich

Ziel: Claude-Design-Projekt **nexapps**.

Die Kennung des Projekts steht bewusst **nicht** hier, sondern in
`.design-sync/projekt-kennung.txt` — diese Datei bleibt lokal und wird nicht
mitgeliefert. Der Repo ist öffentlich, und die Kennung zeigt auf ein privates
Projekt; sie gibt zwar niemandem Zugriff, hat in einem öffentlichen
Verzeichnis aber nichts verloren. Vor dem nächsten Abgleich die Kennung aus
dieser Datei wieder als `"projectId"` oben in `config.json` eintragen — sonst
legt der Abgleich ein zweites, leeres Projekt an.

Stand: 18.08.2026, Nexview 0.4.3 — 20 Bausteine, 108 Dateien.

## Wie das hier aufgebaut ist

Nexview ist eine Anwendung, keine Komponentenbibliothek. Damit der Umbau
trotzdem greift, liegt unter `frontend/packages/nexview-ui/` eine dünne
Verpackung: sie **exportiert die vorhandenen Komponenten unverändert**
(`src/index.ts` verweist mit `../../../src/components/...` in die App) und
baut daraus ein eigenes `dist/`. Nichts wird nachgebaut, nichts dupliziert.

- `vite.config.ts` setzt **`root: __dirname`**. Ohne das überschreibt der
  Bibliotheksbau `frontend/dist/` — also den Stand, den Docker ausliefert.
- `tsconfig.build.json` steht **für sich** (erbt nicht). Es gibt kein
  `tsconfig.app.json` in diesem Projekt.
- Die Reihenfolge im `buildCmd` ist Absicht: erst `vite build`
  (`emptyOutDir` räumt `dist/` leer), **danach** `tsc` für `dist/types/`.
  Andersherum verschwinden die Typdateien und der Wandler findet 0 Bausteine.

## Vorschauen

`.design-sync/previews/*.tsx` — 21 Stück, von Hand geschrieben, weil das
Projekt kein Storybook hat. Jede Vorschau umschließt ihren Baustein mit
`bg-ink-950 p-6`: die Kartenvorlage setzt hart `background:#fff`, und Nexview
ist ein reines Dunkelsystem.

`NexviewProvider` steht als Provider in der Konfiguration und ist deshalb
**bewusst nicht** als eigene Karte dabei — er kann sich nicht in sich selbst
zeichnen.

## Was beim nächsten Abgleich stolpern kann

- **Neue Komponente mit eigenem Umfeld.** `LoadingBar` braucht einen
  QueryClient (`useIsFetching`), ohne dass das an den Eigenschaften sichtbar
  wäre. Wer eine Vorschau ergänzt und einen leeren Bildschirm sieht: zuerst
  prüfen, was der Baustein aus dem Zusammenhang zieht, nicht die Eigenschaften.
- **Eigenschaftsnamen raten geht schief.** `FilterBar` heißt `filters`
  (nicht `value`) und braucht zusätzlich `studios`. Immer in die erzeugte
  `.d.ts` sehen.
- **`[FONT_MISSING] Inter`** bei der Prüfung ist richtig so und blockiert
  nichts: Nexview liefert keine Schriftdatei aus, es verlässt sich auf die
  Systemkette.
- **`frontend/dist/`** nach einem Bibliotheksbau kontrollieren. Wenn dort
  plötzlich `nexview-ui.css` liegt, fehlt `root` in der Vite-Konfiguration —
  dann `cd frontend && npm run build` zum Wiederherstellen.
- Der Wandler legt `ds-bundle/` und `.ds-sync/` im Projektstamm an. Beides ist
  Zwischenstand und gehört nicht ins Git.

## Was in Git gehört

`.design-sync/` (Konfiguration, Vorgaben, Vorschauen, diese Notizen) und
`frontend/packages/nexview-ui/`. Alles andere ist wiederherstellbar.
