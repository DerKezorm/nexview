/*
 * Startet Swagger UI.
 *
 * ⚠️ Eine eigene Datei, kein <script> in der Seite. FastAPI baut die
 * Startanweisung normalerweise direkt in das HTML - und genau daran waere sie
 * bei uns gescheitert: Die Content-Security-Policy erlaubt unter `script-src`
 * nur `'self'` und einen einzigen Hash (den des Startskripts der Weboberflaeche).
 * Ein zweites eingebettetes Skript haette einen zweiten Hash gebraucht, der sich
 * bei jeder Aenderung an dieser Zeile mitgeaendert haette.
 *
 * Als Datei ist sie schlicht `'self'` und die Regel bleibt, wie sie ist.
 */

window.ui = SwaggerUIBundle({
  /*
   * Relativ mit Absicht: Die Adresse loest gegen die /docs-Seite auf und
   * findet die Beschreibung damit an der Wurzel UND unter einem Unterpfad
   * (NEXVIEW_URL_BASE) - eine absolute Adresse ginge dort am Proxy vorbei.
   */
  url: 'openapi.json',
  dom_id: '#swagger-ui',
  layout: 'BaseLayout',
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  /*
   * Nur `presets.apis`, nicht zusaetzlich `SwaggerUIStandalonePreset`.
   * Der Standalone-Preset liegt in einer eigenen Datei und bringt vor allem
   * die Kopfleiste mit dem Eingabefeld fuer eine fremde Adresse mit. Die
   * brauchen wir nicht - hier gibt es genau ein Dokument - und sie waere ein
   * weiteres Megabyte im Abbild.
   */
  presets: [SwaggerUIBundle.presets.apis],
})
