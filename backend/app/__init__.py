"""Nexview backend package."""

# Die Nummer der Fassung, an der gerade gearbeitet wird - nicht die der zuletzt
# veroeffentlichten. So zeigt die Ueber-Seite auch zwischen zwei
# Veroeffentlichungen, welcher Stand laeuft.
#
# Beim Veroeffentlichen wird genau diese Nummer als Tag "v<nummer>" gesetzt;
# die Aktion prueft, dass beides zusammenpasst. Danach wird hier auf die
# naechste Nummer erhoeht. Was in welcher Fassung steckt, steht in CHANGELOG.md.
__version__ = "0.4.3"
