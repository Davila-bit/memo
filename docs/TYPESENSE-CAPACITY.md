# Typesense model capacity, 2026-11-15

Both features are independent and OFF by default. Only the exact value `1` enables
`TYPESENSE_HYBRID` or `TYPESENSE_VOICE`. The server reports ready voice support in
`Me.voice` (login and `/api/me`); web mock mode reports true. An off flag never
adds that feature's schema or attempts its migration, even if an old collection
still has the model. Turning a flag off does not remove an existing model.

Measured directly against the Cloud cluster at approximately 01:52 ET, using
`GET /metrics.json`. Reported `system_memory_total_bytes`: **439,349,248**.
The collection `memo_cap_test` held 80 synthetic, small `event_to_doc` documents.
The models were tested separately; they were never loaded together in this run.

| Stage | `system_memory_used_bytes` | Used MiB | Reported headroom MiB | Write/query result |
|---|---:|---:|---:|---|
| (a) idle | 275,124,224 | 262.38 | 156.62 | Baseline import accepted 80/80; immediately afterward memory was 275,865,600 bytes |
| (b) MiniLM only, indexed + one hybrid query | 475,004,928 | 453.00 | -34.00 | 80/80 accepted; hybrid found 80; subsequent single write accepted |
| (c) embedding collection deleted, 10 s later | 279,580,672 | 266.63 | 152.37 | Write to separate tiny health collection accepted |
| (d) Whisper only, indexed + one silent-WAV voice query | 446,443,520 | 425.76 | -6.77 | 80/80 accepted; normal multi_search response; subsequent single write accepted |
| (e) voice collection deleted, 10 s later | 281,624,576 | 268.58 | 150.42 | Write to separate tiny health collection accepted |

Headroom is the subtraction of the two reported metrics, not an independent
measurement of allocator capacity. The server also reported 2,147,479,552 bytes
of swap, with about 104–107 MB used throughout. No OUT_OF_MEMORY occurred in this
single-feature experiment. Success here is insufficient evidence of safe live
operation: neither model had positive headroom by the requested RAM metrics.
**Keep both flags off on this tier.** Voice used less memory than embeddings,
but neither is recommended for the live demo without more memory and a new test.
Actual spoken transcription was not tested; the WAV contained generated silence.

Cleanup was verified: both `memo_cap_test` and `memo_cap_health_test` returned
404. A final create/write/delete in the second collection succeeded. The live
`hearth_events` collection was never modified, dropped or recreated by this run;
its schema was identical before/after and its document count remained 73. A final
read-only search using the application's normal query parameters returned
`found=73`, one hit (`per_page=1`). That check deliberately bypassed `ensure()`.

Local evidence is in `.cache/typesense-capacity/{results.json,probe.log,live-before.json,live-after.json}`.
No keys or audio were written there. Audio was generated and transmitted in memory.

## Future activation and rollback (not performed in this run)

Do this only after adding capacity and repeating the isolated test. Use the merged
checkout that owns the live API. Pause scheduled and manual check-ins/event writes
and demo resets for the whole migration. Verify `/api/health` reports Tiger `ok`;
the command below deliberately uses the authoritative Tiger store, never a freshly
seeded app or a second in-memory store. Stop if the API is using in-memory storage.

Save this helper as `.cache/reindex-search.py` in that checkout. It reads Tiger
without applying schema migrations. It refuses an empty or incomplete snapshot and
checks that every currently indexed ID is present before any search migration.
`--rebuild` explicitly deletes/recreates the search collection, so use it only for
the rollback described below. Tiger is retained throughout.

```python
import asyncio
import sys

from hearth.core.pgstore import PgStore
from hearth.integrations._common import env
from hearth.integrations.search import TypesenseSearch


async def main():
    store = PgStore(env("TIGER_DATABASE_URL"))
    await store.pool.open(wait=True, timeout=20)  # read only; no apply_schema()
    try:
        n = await store.count_events()
        events = await store.list_events(limit=n)
        assert n > 0 and len(events) == n
        assert len({e.id for e in events}) == n
        search = TypesenseSearch()
        current = await search.client.collections[search.collection].retrieve()
        indexed = await search.client.collections[search.collection].documents.export()
        import json
        ids = {json.loads(line)["id"] for line in indexed.splitlines() if line.strip()}
        assert len(ids) == current["num_documents"]
        assert ids <= {e.id for e in events}, "Tiger snapshot is missing indexed events"
        if "--rebuild" in sys.argv:
            await search.client.collections[search.collection].delete()
        accepted = await search.reindex(events)
        assert accepted == n, f"accepted {accepted}/{n}; keep writes paused and recover"
        final = await search.client.collections[search.collection].retrieve()
        assert final["num_documents"] == n
        print(f"Verified {n} events; hybrid={search._hybrid_ready}; voice={search._voice_ready}")
    finally:
        await store.close()


asyncio.run(main())
```

For **embeddings only**, run this before restarting the API:

```sh
fnox exec --only TYPESENSE_HOST,TYPESENSE_API_KEY,TIGER_DATABASE_URL -- \
  env TYPESENSE_HYBRID=1 TYPESENSE_VOICE=0 \
  WS_ENV=TYPESENSE_HOST,TYPESENSE_API_KEY,TIGER_DATABASE_URL,TYPESENSE_HYBRID,TYPESENSE_VOICE \
  scripts/ws 'cd backend && uv run python ../.cache/reindex-search.py'
TYPESENSE_HYBRID=1 TYPESENSE_VOICE=0 scripts/dev-up api
```

`ensure()` adds the embedding field with `ts/all-MiniLM-L12-v2`, then
`TypesenseSearch.reindex()` imports every authoritative event, generating its
embedding. If additive migration fails, `reindex()` recreates the collection
from those events. Restarting the API alone does not reliably perform a deferred
recreation: `CheckinService.reindex()` only calls `index()` per event.

For **voice only**, substitute `TYPESENSE_HYBRID=0 TYPESENSE_VOICE=1` in BOTH
commands. `ensure()` attempts the voice schema update; this cluster rejected that
update in the previous run, so the integration's `reindex()` recreates an outdated
collection with `voice_query_model: {model_name: "ts/whisper/base.en"}` and imports
all events. Expect a search outage during recreation. After the restart, sign in
or reload: `/api/me` must report `voice: true` before the microphone appears.
Repeat memory, query and write checks before resuming event writes.

Keep the chosen flags on every future `dev-up` invocation (or in the existing
operator-managed runtime environment). Both flags are forwarded by `dev-up`.
Do not change that runtime file in this worktree or restart the live stack here.

To **turn both features off**, first stop app usage:

```sh
TYPESENSE_HYBRID=0 TYPESENSE_VOICE=0 scripts/dev-up api
```

Reload the web: `/api/me` reports `voice: false`, the microphone is hidden, and
voice requests return 400: `Voice questions need search to be set up.` Typed
questions use the original search path. To also **unload models and reclaim
memory**, keep writes paused, rerun the helper with both flags `0` and explicit
`--rebuild`, then restart once more so the API's cached readiness is refreshed:

```sh
fnox exec --only TYPESENSE_HOST,TYPESENSE_API_KEY,TIGER_DATABASE_URL -- \
  env TYPESENSE_HYBRID=0 TYPESENSE_VOICE=0 \
  WS_ENV=TYPESENSE_HOST,TYPESENSE_API_KEY,TIGER_DATABASE_URL,TYPESENSE_HYBRID,TYPESENSE_VOICE \
  scripts/ws 'cd backend && uv run python ../.cache/reindex-search.py --rebuild'
TYPESENSE_HYBRID=0 TYPESENSE_VOICE=0 scripts/dev-up api
```

This rebuild removes both model definitions and regenerates the index from Tiger.
Check `/metrics.json`, the collection schema/count, a typed question and a separate
throwaway write before resuming writes. If activation gets OUT_OF_MEMORY, stop
that activation and use this off/rebuild path; do not repeatedly retry model writes.
