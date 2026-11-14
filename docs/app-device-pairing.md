# Pairing a TV with a code

A TV can't scan a QR code, so it shows one instead: the TV asks nexview for a short code, someone signed in confirms
it on their phone or computer, and the TV picks up a key of its own. The flow follows the OAuth 2.0 Device
Authorization Grant (RFC 8628), with nexview's own paths and error format.

The calls below need no key and are part of the app surface `/api/app/1`: fields may be added, none will be
renamed or removed.

## 0. Find nexview on the network

```
GET /api/app/1/hello
```

```json
{ "app": "nexview", "version": "1.7.0", "name": null, "url": "https://nexview.example.com", "device_code": true }
```

| Field | Meaning |
|---|---|
| `app` | Always exactly `nexview`. Treat any other answer, or none, as "not nexview". |
| `version` | nexview's version, as in `/api/health`. |
| `name` | The instance's name. nexview has no such setting yet, so it is `null` for now. |
| `url` | The public address set in nexview's settings, else `null`. Prefer it over the address you found, so the device keeps working outside the home network. |
| `device_code` | `true` when the calls below exist. An older nexview has no `/hello` at all (404 or 405). |

No secrets, no account data, no counts. Limited to 60 calls a minute per sender address when nexview knows the
address (`NEXVIEW_CLIENT_IP`), otherwise not limited; above that 429 `device_poll_too_fast` with `Retry-After`.

## 1. Ask for a code

```
POST /api/app/1/device/code
Content-Type: application/json

{ "device": "Living room", "app": "nexcinema", "address": "http://192.168.1.20:8000" }
```

All three fields are optional.

| Field | Max | Meaning |
|---|---|---|
| `device` | 80 | The device's name. Shown when confirming and in the list of keys. Empty: `TV`. |
| `app` | 40 | Which app asks. Shown when confirming. |
| `address` | 200 | The address the device uses to reach nexview. Used for the links below only when nexview has no public address set under Settings; otherwise the public address wins, so a phone outside the home network can open the link. Without either, nexview takes the address the request came in on. |

Answer `200`, sent with `Cache-Control: no-store`:

```json
{
  "device_code": "Jx3…43 characters…",
  "user_code": "BCDF-GHJK",
  "verification_uri": "https://nexview.example.com/link",
  "verification_uri_complete": "https://nexview.example.com/link?code=BCDF-GHJK",
  "expires_in": 600,
  "expires_at": "2026-11-14T18:10:00Z",
  "interval": 5
}
```

| Field | Meaning |
|---|---|
| `device_code` | Secret. Keep it on the device, never show it. nexview stores only its hash. |
| `user_code` | Show it large on screen. Eight letters from `BCDFGHJKLMNPQRSTVWXZ` with a dash in the middle. |
| `verification_uri` | Show it next to the code: "Go to … and enter the code". |
| `verification_uri_complete` | Put it in a QR code. A phone that scans it lands on the page with the code filled in. |
| `expires_in`, `expires_at` | The code is good for 10 minutes. After that, ask for a new one. |
| `interval` | Seconds to wait between two polls. |

Errors:

| Status | `detail.code` | What to do |
|---|---|---|
| 429 | `device_code_busy` | Too many codes are open in this installation (or from this sender, when nexview knows the sender's address). Wait `Retry-After` seconds, then ask again. |

## 2. Poll until someone confirms

Wait `interval` seconds, then:

```
POST /api/app/1/device/token
Content-Type: application/json

{ "device_code": "Jx3…" }
```

The answer is always `200` with `status`, sent with `Cache-Control: no-store`:

| `status` | Other fields | What to do |
|---|---|---|
| `pending` | `interval` | Nobody has confirmed yet. Poll again after `interval` seconds. |
| `approved` | `key`, `account` | Done. Store `key` in the keychain and send it as `Authorization: Bearer` from now on. The code is used up; `key` comes **once**. |
| `denied` | | Someone declined. Stop polling; offer a new code. |
| `expired` | | The code expired, was already used, or is unknown. Stop polling; offer a new code. |

```json
{
  "status": "approved",
  "interval": null,
  "key": "nxv_…",
  "account": { "name": "Mia", "role": "child" }
}
```

`account.role` is `admin`, `user` or `child`. A key for a child can do only what the child can: the adult paths
under `/api/app/1` answer `403`, the paths under `/api/app/1/kids/…` work, and a child's wishes go to their parent.

Errors:

| Status | `detail.code` | What to do |
|---|---|---|
| 429 | `device_poll_too_fast` | Polled sooner than `interval`. Wait `Retry-After` seconds; the code stays valid. |
| 409 | `apikey_too_many` | The account already has 20 keys, so no key was made. The code is used up. Say so and offer a new code; the person has to revoke an old key first. |
| 422 | | `device_code` missing or longer than 100 characters. |

## 3. What the person sees

`/link` in nexview's web interface (signed in, adults only). They type the code, or the QR code opens the page with
the code already checked, see the device's name and the app, can rename the device and, as a parent with the
permission to manage child accounts, pick one of their children instead of themselves. Then **Connect** or
**Decline**. The page warns to confirm only a code that is on their own screen right now: whoever confirms gives the
device their access.

The new key appears in the person's profile under API tokens (or, for a child, under that child's devices) with the
device's name, and can be revoked there at any time. A revoked key answers `401`; the app should then go back to
pairing.

## Error format

Errors look like the rest of nexview's API:

```json
{ "detail": { "code": "device_poll_too_fast", "message": "…", "retry_after": 5 } }
```

Match on `code`, not on `message`; the message is a fallback text in German.

## Limits at a glance

| | Limit |
|---|---|
| Code lifetime | 10 minutes |
| Polling | at most every 5 seconds per code |
| Open codes | 30 per installation; 10 new codes per 10 minutes per sender address when nexview knows it (`NEXVIEW_CLIENT_IP`) |
| Typing codes in the browser | after 3 wrong codes the wait doubles from 1 second up to a minute, after 10 a 15-minute pause per account (and per address, when known); a correct code does not reset the count |
