# Nexview on your home dashboard

Nexview answers one call with everything a dashboard tile needs:

```
GET /api/v1/dashboard
Authorization: Bearer nxv_…
```

```json
{
  "version": "0.26.0",
  "befunde": {
    "fehler": 1,
    "warnung": 3,
    "hinweis": 2,
    "dringendste": ["dienst.nicht_erreichbar", "nachschub.haengt", "platz.knapp"]
  },
  "anfragen": { "wartend": 3, "laufend": 5, "fehlgeschlagen_7d": 0 },
  "bibliothek": {
    "filme": 3509,
    "serien": 172,
    "belegt_bytes": 43025678000000,
    "frei_bytes": 5520000000000
  },
  "instanzen": [
    { "name": "Radarr", "erreichbar": true, "probleme": 0 },
    { "name": "Sonarr", "erreichbar": false, "probleme": 1 }
  ],
  "tickets_offen": 0
}
```

It sits under `/api/v1`, which means the shape is a promise: as long as `v1` is
in the address, nothing that already works will break. Fields may be added —
read the ones you know and ignore the rest.

## Before you start: the token

Create it in Nexview under **Profile → Account → Security → API tokens**, tick
**may only read**, and copy the value. It is shown exactly once.

> **⚠️ It has to belong to an administrator, and you should know what that
> means.**
>
> A token inherits the rights of the account it belongs to — that is the whole
> design, and it is deliberate: there is no second set of permissions to keep in
> sync. Instance state and disk figures are an operator's business, so an
> ordinary account gets `403` here.
>
> *May only read* limits the token to `GET`, so it can never change anything.
> But an administrator's read-only token can still read the user list, the log
> and the settings. That is fine on your own machine. Think twice before you pin
> it to a screen other people can see, or paste it into a dashboard you host
> somewhere else.

## Homepage

Add this to `services.yaml`. The `mappings` pull three numbers out of the one
response — Homepage makes only a single request either way.

```yaml
- Media:
    - Nexview:
        icon: nexview.png
        href: https://nexview.example.com
        description: Requests and library
        widget:
          type: customapi
          url: https://nexview.example.com/api/v1/dashboard
          method: GET
          headers:
            Authorization: Bearer nxv_your_token_here
          mappings:
            - field:
                anfragen: wartend
              label: Waiting
            - field:
                befunde: fehler
              label: Errors
            - field:
                anfragen: laufend
              label: In flight
```

If you would rather see whether Nexview is up at all, point a `ping` widget at
`/api/v1/health`. That one needs **no** token — a monitor that has to sign in
before it may ask "are you alive" is not a monitor.

## Homarr

Homarr's *API* integration reads the same address. Under **Add tile → API**:

| Field | Value |
| --- | --- |
| URL | `https://nexview.example.com/api/v1/dashboard` |
| Method | `GET` |
| Header | `Authorization: Bearer nxv_your_token_here` |
| Refresh | 5 minutes is plenty — see below |

Then pick the values you want, for example `befunde.fehler`,
`anfragen.wartend` and `instanzen.0.erreichbar`.

## How often to ask

**Every five minutes is plenty, and every ten is fine.** Nothing behind this
address is measured when you call it: Nexview collects instance state, disk
figures and the reconciliation on its own schedule — every two minutes for
"is it answering", hourly for the rest. Asking every ten seconds returns the
same numbers and only burns power.

Nexview also records when a token was last used, but writes that at most every
15 minutes for exactly this reason.

## What the findings mean

`dringendste` holds up to three **identifiers**, not sentences — for example
`dienst.nicht_erreichbar` or `platz.knapp`. A ready-made sentence would be in
the server's language, and it would change the moment somebody improves a
wording. Under a promise that could never happen again.

The prefix tells you the area: `dienst` (Radarr/Sonarr), `platz` (disk),
`nachschub` (requests in flight), `bibliothek` (the collection), `abgleich`
(where the sources disagree), `betrieb` (Nexview itself).

Open Nexview's own **Admin dashboard** for the full sentence, what follows from
it, and a button that takes you to the right page.
