# Staging PostHog cache benchmark

Workflow: `.github/workflows/posthog-cache-benchmark.yml`.
Runs only on `Monk-CI-Test2/docker-cache-test`, the staging test organization.

One dispatch runs **3 uncached PostHog builds in parallel → one cache writer →
3 warm builds in parallel → report**. Every build uses `monkci-ubuntu-24.04-4`
(4 vCPU, x64), the same resolved PostHog SHA, BuildKit v0.28.0 and the full
unmodified PostHog Dockerfile. Output is `type=cacheonly`, as in the existing
cache tests; image export/push is outside this measurement.

The three uncached jobs each start an empty local docker-container builder.
There is no external cache import/export; cache mounts within a build retain
normal behavior. Warm jobs require the persistent builder, `cache-hit=true`
and at least one cached executable step. The seed requires the canonical writer;
its action post hook publishes the snapshot before the warm matrix starts.
On later dispatches the seed may reuse an existing cache. Choose `warmup=false`
only when that PostHog SHA is already warm.

Do not run other cache workflows in this repository during the benchmark.
They share the repository's cache. The workflow serializes its own dispatches.
`max-parallel: 3` requests parallelism; the report verifies that all three
runners actually overlap. If staging capacity serializes them, it withholds
the headline instead of claiming a 3x parallel comparison.

## Capacity and 100 GB budget

Checked 2026-10-09: this staging organization's active Max plan already has
**100 GB configured and 100 GB included**. No quota or billing change is required.
Product GB is binary GiB here (`100 * 2^30` bytes). The control plane enforces
the organization's quota and supplies the BuildKit GC budget. The uncached
builder uses a matching 100 GiB GC retention policy.

The report prints cached-step counts, unique cached RUN/COPY/ADD counts,
builder setup time, build time, full job time, and cache filesystem bytes/GiB.
Each job also prints `docker buildx du --verbose` for logical cache usage.
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

Defaults verified on 2026-10-09: [GitHub Linux x64 4-core is $0.012/minute](https://docs.github.com/en/billing/reference/actions-runner-pricing)
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
time is excluded. A separate build-only median ratio is also printed.

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
