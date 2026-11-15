# Discover for TV apps

Three lists of films and series that are **not in the library yet**, so a TV app can show a Discover screen where
people find something and ask for it. Together with the search and the requests that already exist under
`/api/app/1`, that is the whole screen: search over all titles at the top, rows of what is missing below, filtered by
films or series and by genre.

Both calls are part of the app surface `/api/app/1`: fields may be added, none will be renamed or removed. They need
an adult's key (`Authorization: Bearer nxv_…`); a child's key gets `403`. The titles come from TMDB through nexview,
so the app needs no TMDB key of its own.

## The lists

```
GET /api/app/1/discover/{type}?list=streaming&genre=18&page=1
```

| Parameter | |
|---|---|
| `type` | `movie` or `tv` |
| `list` | `streaming`, `new` or `popular` (anything else: `422`) |
| `genre` | optional, a TMDB genre id from the call below |
| `page` | optional, from 1 |

| `list` | What it holds | Order |
|---|---|---|
| `streaming` | New on a subscription service in the region: films whose digital release in the region was in the last six weeks, series whose first episode aired in the last six weeks, both only if TMDB lists them under a flat-rate service there. | newest first |
| `new` | Recently out: films released in the region in the last 90 days, in cinemas or at home (digital, disc); series that started in the last 90 days. | most popular first |
| `popular` | Popular of all time, old titles included: sorted by number of votes, rated 7.0 or better. This is where the classics are. | most votes first |

The region is the one nexview uses for this account: the server's default region, unless the account picked its own
discover region in its profile. Series lists leave out talk shows, news and video clips.

**Everything already in the library is left out**: titles with `status` `downloaded` (on disk, through Radarr, Sonarr
or nexcrate) or `in_library` (only on the media server). Series with missing seasons (`partial`) stay in. So do
titles someone has asked for already; they carry their state (`pending_approval`, `approved`, `searching` …), so the
app can say "Requested" instead of offering a request. Titles above the account's age limit never appear, as
everywhere else in nexview.

### Answer

The same shape as the other lists (`/search`, `/discover` without `list`):

```json
{
  "page": 1,
  "total_pages": 12,
  "total_results": 713,
  "items": [
    {
      "media_type": "movie",
      "tmdb_id": 1184918,
      "title": "The Wild Robot",
      "overview": "…",
      "poster_url": "https://image.tmdb.org/t/p/…",
      "backdrop_url": "https://image.tmdb.org/t/p/…",
      "release_date": "2024-09-12",
      "vote_average": 8.3,
      "genres": ["Animation", "Science Fiction", "Family"],
      "status": "not_requested"
    }
  ],
  "demo": false,
  "arr_warning": null
}
```

Items carry every field of a title in the other lists; the ones above are what a row needs. Keys are snake_case.

**Paging.** One page of the answer covers a fixed block of three TMDB pages (60 titles before filtering), and
everything from that block that is not in the library goes out. A page therefore has between 0 and 60 items:
close to 60 with an empty library, fewer the more you already have. An empty `items` does **not** mean the end;
keep asking until `page == total_pages`. `total_results` is TMDB's count before filtering, an upper bound.

`arr_warning` is set when nexview could not reach the library (Radarr, Sonarr, nexcrate). The states are then
incomplete and titles you have may appear; showing the list anyway is fine.

### Without `list`

`GET /api/app/1/discover/{type}` without `list` stays what it was: the web interface's discover list with its filters
(`genre_id`, `sort`, `date_from` …), not filtered by the library.

## Genres

```
GET /api/app/1/discover/genres/{type}
```

```json
[{ "id": 28, "name": "Action" }, { "id": 18, "name": "Drama" }]
```

TMDB's genres for films or series, named in the account's language. Pass the `id` as `genre`. Films and series have
different genre lists (series have "Action & Adventure" instead of "Action").

## "Already there" in the search

`GET /api/app/1/search/{type}?q=…` is not filtered: it returns every match, each with its `status`. Show "Already
there" for `downloaded` and `in_library`, "Requested" for a running request, and offer a request otherwise.

## Errors

| Status | When |
|---|---|
| 401 | Missing, wrong or revoked key. |
| 403 | A child's key. |
| 422 | Unknown `type` or `list`, `page` below 1. |
| 429 | TMDB is rate-limiting nexview; try again later. |
| 502 | TMDB is unreachable or refuses nexview's TMDB key; the operator has to look. |

Errors look like the rest of nexview's API, `{"detail": {"code": "…", "message": "…"}}` where there is a code. Match
on `code`, not on `message`.

Without a TMDB key nexview runs on sample data; the lists then hold a few made-up titles or none.

Available from the nexview version after 1.7.0. An older nexview ignores `list` and answers with its unfiltered
discover list, and has no `/discover/genres`: it answers `404` there. Check `version` in `/api/app/1/me` or
`/api/app/1/hello` and hide the screen on older versions.
