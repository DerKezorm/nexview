# Nexview → Claude Design — Notizen zum Abgleich

Ziel: Claude-Design-Projekt **Nexview Design System**.

Die Kennung des Projekts steht bewusst **nicht** hier, sondern in
`.design-sync/projekt-kennung.txt` — diese Datei bleibt lokal und wird nicht
mitgeliefert. Der Repo ist öffentlich, und die Kennung zeigt auf ein privates
Projekt; sie gibt zwar niemandem Zugriff, hat in einem öffentlichen
Verzeichnis aber nichts verloren. Vor dem nächsten Abgleich die Kennung aus
dieser Datei wieder als `"projectId"` oben in `config.json` eintragen — sonst
legt der Abgleich ein zweites, leeres Projekt an.

Stand: **26.08.2026, Nexview 0.21.0, nexview-ui 0.5.0** — 33 Bausteine,
8 Token-Dateien, 15 Specimen-Karten.

## ⚠️ Der Bau hat drei Schritte, nicht einen

```
node .ds-sync/package-build.mjs --config .design-sync/config.json \
  --node-modules frontend/node_modules \
  --entry frontend/packages/nexview-ui/dist/index.es.js --out ds-bundle
node .design-sync/fundament-einfuegen.mjs ds-bundle
node .ds-sync/package-validate.mjs ds-bundle
node .design-sync/karten-pruefen.mjs ds-bundle
```

`package-build.mjs` **räumt `ds-bundle/` bei jedem Lauf vollständig leer** — es
kennt nur Bausteine. Tokens, Specimen-Karten und die Marke liegen deshalb
dauerhaft unter `.design-sync/foundation/` und werden von
`fundament-einfuegen.mjs` hineinkopiert; dasselbe Skript schreibt `styles.css`
neu (Reihenfolge: erst `_ds_bundle.css`, dann die Rollen-Ebene). Wer den
zweiten Schritt vergisst, lädt ein Bündel ohne Fundament hoch.

`karten-pruefen.mjs` ist nötig, weil `package-validate.mjs` **nur Bausteine**
prüft. Eine zerlaufene Specimen-Karte fällt sonst erst in der Oberfläche auf.
Es prüft auch, ob der Inhalt in das im `@dsCard`-Kopf angegebene Maß passt —
zu klein angegeben heißt: die Karte wird abgeschnitten.

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
- ⚠️ `src/index.ts` muss **`import './styles.css'`** enthalten. Ohne diese eine
  Zeile entsteht `dist/nexview-ui.css` gar nicht erst, und das Bündel geht
  ohne Farben hoch. Der Bau meldet dabei keinen Fehler.

## Vorschauen

`.design-sync/previews/*.tsx` — 33 Stück, von Hand geschrieben, weil das
Projekt kein Storybook hat. Jede Vorschau umschließt ihren Baustein mit
`bg-ink-950 p-6`: die Kartenvorlage setzt hart `background:#fff`.

⚠️ **Vorschauen werden von Tailwind nicht durchsucht.** `@source` in
`packages/nexview-ui/src/styles.css` zeigt auf `src/components` — eine Klasse,
die dort nirgends steht, entsteht im Stylesheet nicht. Wer in einer Vorschau
`grid-cols-5`, `h-6` oder `max-w-xl` schreibt, bekommt **keinen Fehler**,
sondern ein Element ohne Maß: Das Symbolraster lief so auf 10.930 px Höhe
auseinander. Maße in Vorschauen deshalb entweder als `style={{…}}` oder nur
mit Klassen, die es wirklich gibt (`h-4`, `h-5`, `h-7`, `h-8`, `h-9`, `h-11`,
`h-12`, `h-full`).

`NexviewProvider` steht als Provider in der Konfiguration und ist deshalb
**bewusst nicht** als eigene Karte dabei — er kann sich nicht in sich selbst
zeichnen.

## Was nicht in der Bibliothek ist, und warum

Aufgenommen wird nur, was **ohne Daten** steht. Draußen bleiben deshalb:
`TitelVerweis` (braucht den Router), `ThemeSwitcher` (`api` + `useAuth`),
`MediaCard`, `DetailModal`, `UserMenu`, `NotificationBell`, `AufraeumTabelle`,
`VerschwindetBald` und alles unter `pages/`. Sie laden ihre Inhalte selbst und
wären hier leer.

`FilterBar` gab es beim Stand vom 18.08. noch — die Datei ist inzwischen weg
(die Optionen leben in `media/optionen.ts`). Ihre Rolle haben `Reiterreihe`
und `Umschalter` übernommen.

## Re-sync-Risiken

- **`ds-bundle/` ist kein Ablageort.** Alles, was dort von Hand hineingelegt
  wird, ist beim nächsten Bau weg. Dauerhaftes gehört nach
  `.design-sync/foundation/`.
- **Umlaute im README-Index.** Der Wandler schreibt die einzeilige
  Kurzbeschreibung jedes Bausteins ohne Umlaute („Rckfrage", „Fuleiste").
  In `.d.ts` und `.prompt.md` stimmen sie. Kosmetisch, in `lib/docs.mjs`
  verursacht — nicht von Hand im Bündel nachbessern, das überlebt keinen Bau.
- **`Betont`** trägt im Index die Beschreibung der *zweiten* Ausfuhr aus der
  Datei, nicht die der Datei selbst.
- **Zwei Marken, zwei Projekte.** „nexapps Design System" ist die Marke des
  internen Dashboards (`nexapps-intern/design/`), nicht Nexview. Dass Nexviews
  Bausteine bis 26.08.2026 in einem nexapps-Projekt lagen, war ein Fehlgriff —
  die beiden Altprojekte sind seitdem gelöscht. Für Nexview gibt es genau ein
  Projekt, und seine Kennung steht in `projekt-kennung.txt`.
