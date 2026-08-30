/**
 * Die letzte Meldung, wenn die Oberfläche gar nicht erst hochkommt.
 *
 * ⚠️ **Der Fehler, den dieser Test verhindert, ist eine weiße Seite.** Seit die
 * Sprachdatei übers Netz kommt, kann sie ausbleiben — und ohne diesen Zweig
 * stünde der Besucher vor einem leeren Bildschirm ohne Erklärung und ohne
 * etwas zum Anklicken. Er hielte die ganze Anwendung für kaputt.
 */

import { beforeEach, expect, it, vi } from 'vitest'

import { startFehlgeschlagen } from './startFehlgeschlagen'

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>'
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

it('sagt, was los ist - statt einer leeren Seite', () => {
  startFehlgeschlagen(new Error('de.json kam nicht an'))

  const wurzel = document.getElementById('root')!
  expect(wurzel.textContent).toContain('could not finish loading')
  // Nicht nur eine Meldung: auch ein Weg heraus.
  expect(wurzel.querySelector('button')?.textContent).toBe('Reload')
})

it('behält den eigentlichen Fehler für die Konsole', () => {
  // Der freundliche Satz hilft dem Besucher; wer die Konsole aufmacht, braucht
  // den echten Grund.
  const fehler = new Error('de.json kam nicht an')
  startFehlgeschlagen(fehler)

  expect(console.error).toHaveBeenCalledWith(expect.stringContaining('Nexview'), fehler)
})

it('stürzt nicht ab, wenn es die Wurzel gar nicht gibt', () => {
  // Kommt vor, wenn schon die HTML-Seite selbst unvollständig ist. Ein
  // Absturz hier ersetzte eine erklärte weiße Seite durch eine stumme.
  document.body.innerHTML = ''
  expect(() => startFehlgeschlagen(new Error('x'))).not.toThrow()
})

it('räumt weg, was vorher im Wurzelelement stand', () => {
  // Sonst stünde die Fehlermeldung unter einem halb gezeichneten Startbild.
  document.getElementById('root')!.innerHTML = '<p>halb gezeichnet</p>'
  startFehlgeschlagen(new Error('x'))

  expect(document.getElementById('root')!.textContent).not.toContain('halb gezeichnet')
})
