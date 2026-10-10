/**
 * Ein nachgebauter Anmelde-Anbieter (OpenID Connect) für den Ende-zu-Ende-Test
 * `nexoidc.spec.ts` – nie ein echter, nie das Netz.
 *
 * Er beantwortet Discovery, Schlüssel, Token und userinfo mit einem RSA-Schlüssel,
 * den er beim Start selbst erzeugt, und schickt den Browser beim Anmelden ohne
 * Rückfrage mit einem Code zurück. Die Person heißt immer gleich (`e2e-person`).
 *
 *   node e2e/fake-oidc.mjs [port]
 */

import { createHash, generateKeyPairSync, randomBytes, sign } from 'node:crypto'
import { createServer } from 'node:http'

const PORT = Number(process.argv[2] ?? process.env.FAKE_OIDC_PORT ?? 8794)
const ISSUER = `http://127.0.0.1:${PORT}`
const PERSON = { sub: 'e2e-person', preferred_username: 'e2e-person', email: 'person@example.com' }

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const KID = randomBytes(6).toString('hex')
const JWK = { ...publicKey.export({ format: 'jwk' }), kid: KID, use: 'sig', alg: 'RS256' }

/** Laufende Anmeldungen: Code → was der Start mitbrachte. */
const codes = new Map()

const base64url = (wert) => Buffer.from(wert).toString('base64url')

function idToken(clientId, nonce) {
  const jetzt = Math.floor(Date.now() / 1000)
  const kopf = base64url(JSON.stringify({ alg: 'RS256', typ: 'JWT', kid: KID }))
  const inhalt = base64url(
    JSON.stringify({ iss: ISSUER, aud: clientId, iat: jetzt, exp: jetzt + 300, nonce, ...PERSON }),
  )
  const unterschrift = sign('RSA-SHA256', Buffer.from(`${kopf}.${inhalt}`), privateKey).toString('base64url')
  return `${kopf}.${inhalt}.${unterschrift}`
}

function json(antwort, status, daten) {
  antwort.writeHead(status, { 'Content-Type': 'application/json' })
  antwort.end(JSON.stringify(daten))
}

function clientAus(anfrage, formular) {
  const kopf = anfrage.headers.authorization ?? ''
  if (kopf.startsWith('Basic ')) {
    return decodeURIComponent(Buffer.from(kopf.slice(6), 'base64').toString().split(':')[0])
  }
  return formular.get('client_id') ?? ''
}

createServer((anfrage, antwort) => {
  const adresse = new URL(anfrage.url ?? '/', ISSUER)
  if (adresse.pathname === '/.well-known/openid-configuration') {
    return json(antwort, 200, {
      issuer: ISSUER,
      authorization_endpoint: `${ISSUER}/authorize`,
      token_endpoint: `${ISSUER}/token`,
      jwks_uri: `${ISSUER}/jwks`,
      userinfo_endpoint: `${ISSUER}/userinfo`,
      response_types_supported: ['code'],
      subject_types_supported: ['public'],
      id_token_signing_alg_values_supported: ['RS256'],
      token_endpoint_auth_methods_supported: ['client_secret_basic', 'client_secret_post'],
      code_challenge_methods_supported: ['S256'],
    })
  }
  if (adresse.pathname === '/jwks') return json(antwort, 200, { keys: [JWK] })
  if (adresse.pathname === '/authorize') {
    const ziel = adresse.searchParams.get('redirect_uri') ?? ''
    const code = randomBytes(12).toString('hex')
    codes.set(code, {
      nonce: adresse.searchParams.get('nonce') ?? '',
      clientId: adresse.searchParams.get('client_id') ?? '',
      challenge: adresse.searchParams.get('code_challenge') ?? '',
    })
    const zurueck = new URL(ziel)
    zurueck.searchParams.set('code', code)
    zurueck.searchParams.set('state', adresse.searchParams.get('state') ?? '')
    antwort.writeHead(302, { Location: zurueck.toString() })
    return antwort.end()
  }
  if (adresse.pathname === '/token' && anfrage.method === 'POST') {
    let rumpf = ''
    anfrage.on('data', (stueck) => (rumpf += stueck))
    anfrage.on('end', () => {
      const formular = new URLSearchParams(rumpf)
      const lauf = codes.get(formular.get('code') ?? '')
      codes.delete(formular.get('code') ?? '')
      const pruefung = createHash('sha256').update(formular.get('code_verifier') ?? '').digest('base64url')
      if (!lauf || lauf.challenge !== pruefung || clientAus(anfrage, formular) !== lauf.clientId) {
        return json(antwort, 400, { error: 'invalid_grant' })
      }
      json(antwort, 200, {
        access_token: randomBytes(16).toString('hex'),
        token_type: 'Bearer',
        expires_in: 300,
        id_token: idToken(lauf.clientId, lauf.nonce),
      })
    })
    return
  }
  if (adresse.pathname === '/userinfo') return json(antwort, 200, PERSON)
  antwort.writeHead(404)
  antwort.end()
}).listen(PORT, '127.0.0.1', () => {
  console.log(`fake OIDC provider at ${ISSUER}`)
})
