# People for TV apps

A person's films and series, each with what nexview knows about it, so a TV app can show on an actor's page which of
their titles are missing from the library and offer them for a request. Plus a search for people by name, for media
servers that do not know a person's TMDB id.

Both calls are part of the app surface `/api/app/1`: fields may be added, none will be renamed or removed. They need
an adult's key (`Authorization: Bearer nxv_…`); a child's key gets `403`, as person pages do in the web interface. The
data comes from TMDB through nexview, so the app needs no TMDB key of its own.

## One person

```
GET /api/app/1/person/{person_id}?language=de
```

| Parameter | |
|---|---|
| `person_id` | the person's TMDB id (Jellyfin and Emby: `ProviderIds.Tmdb` of the person) |
| `language` | optional, a TMDB language like `de`, `fr` or `en-US`; anything else: `422`. Without it, the account's language applies. |

```json
{
  "person_id": 287,
  "name": "Brad Pitt",
  "biography": "…",
  "photo_url": "https://image.tmdb.org/t/p/…",
  "birthday": "1963-12-18",
  "deathday": null,
  "place_of_birth": "Shawnee, Oklahoma, USA",
  "known_for_department": "Acting",
  "credits": [
    {
      "media_type": "movie",
      "tmdb_id": 550,
      "kind": "movie",
      "title": "Fight Club",
      "character": "Tyler Durden",
      "poster_url": "https://image.tmdb.org/t/p/…",
      "release_date": "1999-10-15",
      "vote_average": 8.4,
      "status": "downloaded"
    }
  ]
}
```

The answer is the person page of the web interface (`GET /api/person/{person_id}`), with two differences: `language`
picks the language, and where TMDB has no biography in that language, `biography` holds the English one. It is empty
only when TMDB has none at all.

**Credits.** The best-known films and series, most popular first, at most 40 roles plus up to 16 appearances.
`media_type` is `movie` or `tv`. `kind` is `movie`, `series` or `appearance` (talk shows, interviews, award shows,
"Self"); a TV app's film row wants `kind == "movie"`. For directors and writers the directed or written titles come
first and `character` names the job. The year is the first four characters of `release_date` (the first air date for
series); it can be `null`.

**Status.** Each credit carries the same `status` as everywhere else in nexview:

| `status` | Meaning | Show |
|---|---|---|
| `downloaded` | on disk (Radarr, Sonarr or nexcrate) | in the library |
| `in_library` | only on the media server | in the library |
| `partial` | a series with missing seasons | missing, can be requested |
| `not_requested` | nobody asked for it | missing, can be requested |
| `pending_approval`, `requested`, `searching` | a request is running | requested |
| `blocked` | the operator does not want it | leave it out |

Treat a value you do not know as "requested", not as missing: new states may be added.

Titles above the account's age limit are left out of `credits`, as everywhere else in nexview.

A request goes through the existing `POST /api/app/1/requests` with `media_type` and `tmdb_id`, after
`GET /api/app/1/media/{type}/{tmdb_id}` for the details, the same way as from Discover.

## Find a person by name

```
GET /api/app/1/people?q=Brad%20Pitt
```

The web interface's people search: `q`, optional `department` (`acting` by default, `directing`, `writing`) and
`page`. Only people whose main department is the chosen one are returned.

```json
{
  "items": [
    {
      "person_id": 287,
      "name": "Brad Pitt",
      "photo_url": "https://image.tmdb.org/t/p/…",
      "department": "Acting",
      "known_for": "Fight Club, Troy"
    }
  ],
  "has_more": false
}
```

Use it only when the media server has no TMDB id for the person, and only accept a clear match: a single result whose
`name` equals the name you have. Two actors can share a name.

## Errors

| Status | When |
|---|---|
| 401 | Missing, wrong or revoked key. |
| 403 | A child's key. |
| 404 | TMDB does not know the person, or nexview runs on sample data (no TMDB key). |
| 422 | `person_id` below 1, an invalid `language`. |
| 429 | TMDB is rate-limiting nexview; try again later. |
| 502 | TMDB is unreachable or refuses nexview's TMDB key; the operator has to look. |

Errors look like the rest of nexview's API, `{"detail": {"code": "…", "message": "…"}}` where there is a code. Match
on `code`, not on `message`.

Available from nexview 1.9.0. An older nexview answers `404` on both calls; hide what depends on them.
