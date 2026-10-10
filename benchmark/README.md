# Staging PostHog cache benchmark

Workflow: `.github/workflows/posthog-cache-benchmark.yml`.
Runs only on `Monk-CI-Test2/docker-cache-test`, the staging test organization.

One dispatch runs **3 uncached PostHog builds in parallel → one cache writer →
3 warm builds in parallel → report**. Every build uses `monkci-ubuntu-24.04-4`
(4 vCPU, x64), the same resolved PostHog SHA, BuildKit v0.28.0 and the full
unmodified PostHog Dockerfile. The default is the October 8, 2026 revision
`10f9ad720e7ac8ee16705bf60033f188aa987e7d`, also used by the inspected
[Depot benchmark](https://github.com/depot-demo-org/benchmark-posthog/actions/runs/37708336515).
Every job exports a complete gzip-compressed OCI image to local disk. Build
time includes image compression/export. The workflow verifies the manifest,
config, platform, final PostHog command, layer count and every referenced blob's
presence/size, then deletes the artifact. Registry push and external cache
export are excluded. Depot's dual-platform build is a different workload;
this test uses native amd64 on both sides.

The previous June revision built in about six minutes with zero cached
RUN/COPY/ADD steps, but measured only a cache solve, without an image export.
Those durations describe that older workload and must not be reused for this
new comparison. The October cache writer may still be slow on staging storage:
the previous writer's Python environment COPY took 1,160.5 seconds, compared
with 29.1 seconds on local disk. This workflow change does not repair Ceph.

The three uncached jobs each start an empty local docker-container builder.
Before building, each must return **zero records** from `docker buildx du
--format=json`. A fresh state volume clears dependency cache mounts as well
as layer cache. Cold builds also pass `--no-cache`; any cached executable
step invalidates the result. There is no external cache import/export;
cache mounts within a build retain normal behavior. Warm jobs require the persistent builder, `cache-hit=true`
and at least one cached executable step. The seed requires the canonical writer;
its action post hook publishes the snapshot before the warm matrix starts.
Older staging agents omit the optional `cache-role` output. In that case the
workflow verifies the role from the returned volume: `buildkit-cache` is the
canonical image and `clone-<hex>` is a disposable reader. A clone is never
accepted as a seed writer; conflicting roles and unknown volumes fail.
The summary labels roles derived from the volume with `(volume)`.
Local builder removal uses a three-minute timeout to allow deletion of the
large state volume; full-job timings still include this cleanup.
On later dispatches the seed may reuse an existing cache. Choose `warmup=false`
only when that PostHog SHA is already warm.

Do not run other cache workflows in this repository during the benchmark.
They share the repository's cache. The workflow serializes its own dispatches.
`max-parallel: 3` requests parallelism; the report verifies that all three
runners and all three timed builds actually overlap. It also compares workload
identities (source, Dockerfile SHA256, platform, BuildKit and export settings).
If staging capacity serializes them, it withholds
the headline instead of claiming a 3x parallel comparison.

## Capacity and 100 GB budget

Checked 2026-10-09: this staging organization's active Max plan already has
**100 GB configured and 100 GB included**. No quota or billing change is required.
Product GB is binary GiB here (`100 * 2^30` bytes). The control plane enforces
the organization's quota and supplies the BuildKit GC budget. The uncached
builder uses a matching 100 GiB GC retention policy.

The report prints cached-step counts, unique cached RUN/COPY/ADD counts,
builder setup time, build time, full job time, and cache filesystem bytes/GiB.
The writer also attempts `docker buildx du --verbose` for logical cache usage,
with a 120-second deadline. Required results are saved first, and a timeout or
command failure produces a warning instead of failing the build. The six
comparison jobs skip this optional scan so its latency cannot distort their
comparison. A missing required filesystem measurement still invalidates a leg,
while preserving its diagnostic result. Image archive size and export time
are shown separately from cache filesystem size.
The filesystem usage is checked against the 100 GiB benchmark budget. It
includes filesystem metadata; it is not the physical Ceph allocation.
The thin image can show 300 GiB provisioned even with a 100 GiB entitlement.

Staging Ceph at inspection: **354.3 GB raw total, 34.8 GB raw used,
319.5 GB raw free; 301.8 GB (281.1 GiB) usable pool capacity left**, about 10%
pool utilization. It has one OSD and replica size 1, with a health warning for
no replicas; all placement groups are active+clean. A 100 GiB retained cache
fits. Copy-on-write readers share the snapshot but consume additional space
when they write, so these numbers are a point-in-time check, not a reservation.

An operator can recheck capacity and true allocated repository cache bytes:

```sh
kubectl --context gke_monkcidev_us-central1_monkci-non-prod-us-central1 \
  -n staging exec deploy/mig-controller -- ceph df --format json
kubectl --context gke_monkcidev_us-central1_monkci-non-prod-us-central1 \
  -n staging exec deploy/mig-controller -- rbd du --pool docker-cache \
  --namespace org_279648556_repo_1294796178 --format json
```

## Cost and advertising claims

Defaults verified on 2026-10-11: [GitHub Linux x64 4-core is $0.012/minute](https://docs.github.com/en/billing/reference/actions-runner-pricing)
and [Monk CI 4-vCPU is $0.008/minute](https://monkci.com/pricing).
Override the inputs for a customer's contracted rates. GitHub is rounded up
per job to whole minutes. Monk minutes are rounded to 0.01 per job, matching
the billing service's usage calculation.

**GitHub runner durations are projected from the measured uncached Monk jobs.**
This organization has no GitHub larger runners configured. This workflow
measures the effect of Monk's Docker cache on identical 4-vCPU Monk runners;
it does not measure a GitHub-versus-Monk hardware speedup.

The report uses the sum of three full job durations for cost and the earliest
job start to latest job completion for parallel batch elapsed time. Full-job
duration includes checkout, cache acquisition, source fetching, action post
release and cleanup, using the current run attempt's GitHub jobs API. Queue
time is excluded. A separate median ratio for build plus complete image export
is also printed. Export seconds are a subset of build seconds; do not add them.

Seed cost is shown separately, along with the first cached batch including
seed. Monthly projections include one seed plus the extra cache fee, allocated
over `batches_per_month` three-job batches. `cache_usd_per_month=0` is correct
for this test org's included 100 GB; enter the customer's additional cache fee
otherwise. Usage estimates exclude plan subscriptions, included minutes,
and resolve/report orchestration jobs. Logs and results use job outputs, not
paid artifact uploads. Failed, incomplete, mismatched or nonparallel samples
produce diagnostics and **no speedup/savings headline**.

Run the same pinned SHA several times before choosing a claim for an advert,
and keep the run URLs so the exact measurements and assumptions are available.
The workflow is manual only; creating it does not run the heavy benchmark.

## Local checks

```sh
python3 -m unittest discover -s benchmark -p 'test_*.py'
actionlint -shellcheck= -ignore 'label "monkci-ubuntu-24.04-4" is unknown' \
  .github/workflows/posthog-cache-benchmark.yml
```
