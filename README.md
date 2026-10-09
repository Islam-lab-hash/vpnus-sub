# vpnus-sub

Public VLESS subscription mirror with automatic refresh, deduplication, stable display names, and last-known-good fallback.

## Public subscription

Primary GitHub Raw URL:

`https://raw.githubusercontent.com/Islam-lab-hash/vpnus-sub/main/sub.txt`

The file contains a Base64-encoded list of curated VLESS links. The normal published set is 19-22 entries (currently targeted at 20 distinct location/family profiles).

## Pipeline

Every 6 hours, and on relevant source-code changes, GitHub Actions:

1. Fetches the authorized remote subscription.
2. Keeps the committed `sub.txt` unchanged if the remote source cannot be refreshed.
3. Removes exact technical duplicates.
4. Keeps one preferred profile per distinct location/family.
5. Restores stable human-readable names.
6. Validates Base64, VLESS structure, ports, unique technical endpoints, unique names, and status-file consistency.
7. Publishes only validated output back to `main`.

Generated health files:

- `sync-status.json` — last remote-sync result and final published count.
- `selection-status.json` — selected locations/endpoints and curation details.

## Reliability notes

- `sub.txt` is the last-known-good payload. A failed source refresh does not erase it.
- Only one build is allowed at a time; a newer run cancels an older in-progress run.
- Git publishing retries after fetching/rebasing the current `main` branch.
- The optional Yandex Object Storage mirror is independent from GitHub publishing and cannot break the primary GitHub build.

## Russia / network availability

A successful GitHub Actions run proves that the repository and generated subscription are valid. It does **not** guarantee that `raw.githubusercontent.com` is reachable from every ISP or network. If GitHub Raw is filtered, throttled, or unavailable on a user's network, the repository can be healthy while the public Raw URL still fails for that user. A separate non-GitHub mirror is therefore recommended for Russian users.

## Files

- `sources.txt` — emergency local fallback set.
- `scripts/build.py` — remote synchronization and last-known-good handling.
- `scripts/filter_top.py` — deduplication, selection, naming.
- `scripts/validate.py` — final integrity checks.
- `scripts/publish_yandex.py` — optional Yandex Object Storage publishing.
- `.github/workflows/build.yml` — CI/CD pipeline.
