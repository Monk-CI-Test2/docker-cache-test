"""Report all six jobs; never turn partial/failed runs into a speedup claim."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request


def fetch_jobs():
    jobs, page = [], 1
    while True:
        url = (f"{os.environ['GITHUB_API_URL']}/repos/{os.environ['GITHUB_REPOSITORY']}"
               f"/actions/runs/{os.environ['GITHUB_RUN_ID']}/attempts/"
               f"{os.environ['GITHUB_RUN_ATTEMPT']}/jobs?per_page=100&page={page}")
        req = urllib.request.Request(url, headers={
            'Authorization': f"Bearer {os.environ['GH_TOKEN']}",
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        })
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.load(response)
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(3)
        jobs.extend(data['jobs'])
        if len(jobs) >= data['total_count']:
            return jobs
        page += 1


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read_results(key):
    values = json.loads(os.environ[key])
    return {f'{key.split("_")[0].lower()}-{i}': json.loads(values[f'r{i}'])
            for i in (1, 2, 3) if values.get(f'r{i}')}


def job_seconds(job):
    return timestamp(job['completed_at']) - timestamp(job['started_at'])


def batch_metrics(jobs):
    seconds = [job_seconds(j) for j in jobs]
    return {
        'seconds': seconds, 'runner_minutes': sum(seconds) / 60,
        'monk_billable_minutes': sum(float((Decimal(str(s)) / 60).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP)) for s in seconds),
        'github_rounded_minutes': sum(math.ceil(s / 60) for s in seconds),
        'span': max(timestamp(j['completed_at']) for j in jobs)
                - min(timestamp(j['started_at']) for j in jobs),
        'overlap': min(timestamp(j['completed_at']) for j in jobs)
                   - max(timestamp(j['started_at']) for j in jobs),
        'median': statistics.median(seconds),
    }


def render(jobs):
    by_name = {j['name']: j for j in jobs}
    results = {**read_results('UNCACHED_RESULTS'), **read_results('CACHED_RESULTS')}
    needs = json.loads(os.environ['NEEDS'])
    if os.environ.get('WRITER_RESULT'):
        results['warmup-writer'] = json.loads(os.environ['WRITER_RESULT'])
    gh_rate, monk_rate = float(os.environ['GH_RATE']), float(os.environ['MONK_RATE'])
    cache_fee, batches = float(os.environ['CACHE_FEE']), int(os.environ['BATCHES'])
    errors = []
    lines = [
        '# PostHog: 3 parallel builds, 4 vCPU each (staging)', '',
        f"PostHog commit: `{os.environ['POSTHOG_SHA']}`. Full Dockerfile, linux/amd64, BuildKit v0.28.0.",
        '', '| Job | Outcome | Cache hit / role | Setup s | Build s | Full job s | Cached steps (RUN/COPY/ADD) | Cache FS used GiB |',
        '|---|---|---|---:|---:|---:|---:|---:|',
    ]
    names = [f'uncached-{i}' for i in (1, 2, 3)]
    names += ['warmup-writer'] + [f'cached-{i}' for i in (1, 2, 3)]
    for name in names:
        j, r = by_name.get(name), results.get(name)
        if name == 'warmup-writer' and needs['warmup']['result'] == 'skipped':
            continue
        if not j or not r:
            errors.append(f'{name}: missing job or result')
            lines.append(f'| {name} | missing | | | | | | |')
            continue
        if j['conclusion'] != 'success' or not r['valid']:
            errors.append(f"{name}: {j['conclusion']}; {r.get('errors', [])}")
        if r['sha'] != os.environ['POSTHOG_SHA'] or r['cpu'] != 4 or r['arch'] != 'x86_64':
            errors.append(f'{name}: source or runner mismatch')
        seconds = job_seconds(j) if j.get('completed_at') else None
        used = r['cache_fs_used_bytes']
        size = f'{used / 2**30:.3f}' if used is not None else 'local'
        lines.append(f"| {name} | {j['conclusion']} | {r['cache_hit']} / {r['cache_role']} | "
                     f"{r['setup_sec']} | {r['build_sec']} | {seconds} | "
                     f"{r['cached_steps']} ({r['cached_executable_steps']}/{r['executable_steps']}) | {size} |")
    groups = {}
    for group in ('uncached', 'cached'):
        legs = [by_name.get(f'{group}-{i}') for i in (1, 2, 3)]
        if all(j and j.get('completed_at') and j['conclusion'] == 'success' for j in legs):
            groups[group] = batch_metrics(legs)
            if groups[group]['overlap'] <= 0:
                errors.append(f'{group}: all three runners never overlapped; not a valid 3x parallel sample')
    if errors:
        lines += ['', '**Incomplete or invalid benchmark: speedup and cost-saving claims withheld.**', '']
        lines += [f'- {e}' for e in errors]
        return '\n'.join(lines) + '\n', False
    cold, warm = groups['uncached'], groups['cached']
    cold_build = statistics.median(results[f'uncached-{i}']['build_sec'] for i in (1, 2, 3))
    warm_build = statistics.median(results[f'cached-{i}']['build_sec'] for i in (1, 2, 3))
    speedup = cold['span'] / warm['span']
    monk_cold = cold['monk_billable_minutes'] * monk_rate
    monk_warm = warm['monk_billable_minutes'] * monk_rate
    gh_cold = cold['github_rounded_minutes'] * gh_rate
    seed_job = by_name.get('warmup-writer')
    seed_cost = batch_metrics([seed_job])['monk_billable_minutes'] * monk_rate if seed_job and seed_job['conclusion'] == 'success' else 0
    amortized_cache = cache_fee / batches
    saving = gh_cold - monk_warm - amortized_cache
    lines += [
        '', f'**Measured parallel batch speedup: {speedup:.2f}x** '
        f"({cold['span']:.1f}s uncached → {warm['span']:.1f}s warm; "
        f"{100 * (1 - warm['span'] / cold['span']):.1f}% less elapsed time).",
        f'Build-only median speedup: {cold_build / warm_build:.2f}x '
        f'({cold_build:.3f}s → {warm_build:.3f}s).', '',
        '| Metric | Uncached 3x | Warm cache 3x |', '|---|---:|---:|',
        f"| Batch elapsed seconds | {cold['span']:.1f} | {warm['span']:.1f} |",
        f"| Median full-job seconds | {cold['median']:.1f} | {warm['median']:.1f} |",
        f"| Total runner minutes (sum of 3 jobs) | {cold['runner_minutes']:.3f} | {warm['runner_minutes']:.3f} |",
        f"| All-three-runner overlap seconds | {cold['overlap']:.1f} | {warm['overlap']:.1f} |", '',
        '| Cost scenario for one 3-job batch | Estimated USD |', '|---|---:|',
        f'| GitHub x64 4-vCPU uncached, projected from Monk uncached durations | ${gh_cold:.4f} |',
        f'| Monk CI 4-vCPU uncached | ${monk_cold:.4f} |',
        f'| Monk CI 4-vCPU warm | ${monk_warm:.4f} |',
        f'| Additional cache fee allocated to one batch | ${amortized_cache:.4f} |',
        f'| GitHub projection minus Monk warm + allocated cache | ${saving:.4f} |',
        f'| Measured Monk uncached minus Monk warm compute | ${monk_cold - monk_warm:.4f} |',
        f'| Separate seed job compute cost (this run) | ${seed_cost:.4f} |',
        f'| First cached batch including seed and allocated cache | ${monk_warm + seed_cost + amortized_cache:.4f} |',
        f'| {batches:,} batches/month: GitHub uncached projection | ${gh_cold * batches:.2f} |',
        f'| {batches:,} batches/month: Monk warm + seed once + cache fee | ${monk_warm * batches + seed_cost + cache_fee:.2f} |',
        f'| Monthly difference | ${(gh_cold - monk_warm) * batches - seed_cost - cache_fee:.2f} |', '',
        f'Rates: GitHub ${gh_rate}/minute (each job rounded up to a whole minute); '
        f'Monk ${monk_rate}/minute (elapsed minutes rounded to 0.01 per job). Additional cache ${cache_fee}/month.',
        '[GitHub rates](https://docs.github.com/en/billing/reference/actions-runner-pricing) · '
        '[Monk CI rates](https://monkci.com/pricing), verified 2026-10-09.', '',
        'GitHub durations are **modeled**, not measured on GitHub hardware. Costs are usage-rate estimates, '
        'not invoices; included minutes, subscriptions and control/report jobs are excluded from the batch comparison.',
        'Full-job time comes from the GitHub jobs API and includes checkout, cache setup and action post/cleanup. '
        'Queue time is excluded. The warmup is separate and may itself hit an existing cache.',
        'Cache FS used is the mounted filesystem view, including metadata; the workflow also prints '
        '`docker buildx du --verbose`. The 100 GiB budget is an asserted benchmark limit. '
        'Tenant entitlement is configured on the staging control plane, not through this workflow. '
        'The thin provisioned filesystem capacity may be 300 GiB. Ceph physical allocation must be checked with `rbd du`.',
    ]
    if saving > 0 and gh_cold > 0:
        lines.append(f'Projected GitHub-to-Monk warm usage savings: {100 * saving / gh_cold:.1f}%.')
    # Make the limits available in logs in a machine-readable form, too.
    print(json.dumps({'batch_speedup': speedup, 'build_median_speedup': cold_build / warm_build,
                      'github_uncached_estimated_usd': gh_cold, 'monk_warm_estimated_usd': monk_warm,
                      'seed_estimated_usd': seed_cost, 'allocated_cache_usd': amortized_cache,
                      'estimated_savings_usd': saving}, sort_keys=True))
    return '\n'.join(lines) + '\n', True


def main():
    summary, valid = render(fetch_jobs())
    print(summary)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write(summary)
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
