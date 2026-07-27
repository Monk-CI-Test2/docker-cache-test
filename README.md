# docker-cache-test

Parallel Docker cache test for the MonkCI Ceph RBD cache, using the (large) PostHog build.

**All workflows are artifact-free.** Per-job results travel to the report jobs
as GitHub job outputs (matrix legs each set their own `rN` output key), so no
artifact storage is billed and a billing-blocked artifact upload can never fail
a run or blank a report. Builds use `--output type=cacheonly` (nothing exported).

## Fault Matrix (bug hunting)

Actions → **"Docker Cache - Fault Matrix"** → **Run workflow**, pick a `scenario`
(or `all`). Every scenario asserts a cache invariant and fails loudly when the
deployment violates it; the final `outcomes` job renders a table telling you
which failures indicate real cache bugs vs. infra noise.

| scenario | invariant under test |
|---|---|
| `cache-write` | a layer written by one job is served CACHED to the next fresh runner (write → snapshot publish → read-back) |
| `lastgood` | a crash/unclean release never publishes dirty layers; the previous last-good survives; a clean release advances it |
| `parallel` | 8 concurrent jobs all read the shared snapshot, each clone is writable + isolated, and at most ONE writer's layers persist |
| `lru` | under size pressure eviction is oldest-first; no round hangs or hard-fails on ENOSPC without GC |
| `org-lru` | probe whether this repo's canonical survived heavy bloat (org-wide ordering needs controller-side logs; run `lru` from several repos to stage it) |
| `io-failures` | ENOSPC / read-only writes fail fast (never hang) and recover in-place; damaged content blobs never hang reads; a fresh acquire always yields a working build path |
| `corrupt-recover` | deleted BuildKit DBs + unclean release → no hang, build passes, fresh canonical after reset, warm again |

Harness: `fault/Dockerfile` (salted layers) + `fault/probe.sh` (builds and
reports which steps were CACHED). Scenarios seed their own salts, so each is
valid standalone; under `all` they are chained to serialize on the one
per-repo canonical cache.

## PostHog parallel benchmarks

## Run it
Actions → **"PostHog Docker Cache - 20x Parallel"** → **Run workflow**.

- `warmup=true` runs one cold **writer** that builds PostHog and publishes the protected
  last-good snapshot, then **20 parallel** jobs build the same ref and are each served a
  disposable COW clone of that snapshot.
- Same `posthog_ref` across all jobs so the cache is shared.

## What you get in the run summary
A per-job table plus aggregates:

| column | meaning |
|---|---|
| `cache_setup_sec` | duration of "Set up Docker builder" = acquire + krbd map + mount + (readers) COW clone creation. **This is the per-process overhead of getting a snapshot/clone.** |
| `build_sec` | the docker build itself (cache-hit builds are near-instant) |
| `cached_steps` | build steps served from the clone |
| `cache_fs_used` | `df` on the mounted cache = data the job saw |

### Note on "snapshot size"
Only the **writer** publishes a snapshot (once per run). RBD snapshots are copy-on-write,
so creation is O(1) (~1s) regardless of the ~tens-of-GiB payload; readers get COW **clones**
(near-zero at creation). A GitHub job has no Ceph credentials, so it cannot read true RBD
byte sizes — `cache_fs_used` (the filesystem view of the mounted cache) is the closest
in-workflow proxy. The exact RBD snapshot size is read out-of-band from the Ceph side
(`rbd du`).
