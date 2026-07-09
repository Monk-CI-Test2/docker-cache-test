# docker-cache-test

Parallel Docker cache test for the MonkCI Ceph RBD cache, using the (large) PostHog build.

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
