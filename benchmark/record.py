"""Record a benchmark leg without artifact storage or privileged Ceph access."""
import json
import os
from pathlib import Path
import re
import subprocess


def duration(root, start, end):
    try:
        a, b = int((root / start).read_text()), int((root / end).read_text())
        return round((b - a) / 1000, 3) if b >= a else None
    except (FileNotFoundError, ValueError):
        return None


def count_steps(log):
    # BuildKit repeats an ID for its heading, progress and completion. Count
    # unique executable steps, excluding metadata, context and export messages.
    executable = set(re.findall(r'^#(\d+) \[[^\n]+\] (?:RUN|COPY|ADD)\b', log, re.M))
    cached = set(re.findall(r'^#(\d+) CACHED\s*$', log, re.M))
    return len(executable), len(executable & cached), len(cached)


def export_seconds(log):
    ids = re.findall(r'^#(\d+) exporting to oci image format\s*$', log, re.M)
    times = [float(t) for i in set(ids)
             for t in re.findall(rf'^#{i} DONE ([\d.]+)s\s*$', log, re.M)]
    return max(times) if times else None


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def main():
    root = Path(os.environ['RUNNER_TEMP']) / 'posthog-benchmark'
    log_path = root / 'build.log'
    log = log_path.read_text(errors='replace') if log_path.exists() else ''
    total, cached, all_cached = count_steps(log)
    result = {
        'mode': os.environ['MODE'], 'sha': os.environ['POSTHOG_SHA'],
        'build_outcome': os.environ['BUILD_OUTCOME'],
        'cache_hit': os.environ.get('CACHE_HIT') == 'true',
        'cache_role': os.environ.get('BENCHMARK_CACHE_ROLE') or os.environ.get('CACHE_ROLE', ''),
        'cache_role_source': os.environ.get('BENCHMARK_CACHE_ROLE_SOURCE', 'action'),
        'setup_sec': duration(root, 'setup-start', 'setup-end'),
        'build_sec': duration(root, 'build-start', 'build-end'),
        'export_sec': export_seconds(log),
        'workload': read_json(root / 'workload.json'),
        'image': read_json(root / 'image.json'),
        'executable_steps': total, 'cached_executable_steps': cached,
        'cached_steps': all_cached, 'cache_fs_size_bytes': None,
        'cache_fs_used_bytes': None, 'cache_fs_available_bytes': None,
        'cpu': os.cpu_count(), 'arch': os.uname().machine,
    }
    for key in ('build-start', 'build-end'):
        path = root / key
        result[key.replace('-', '_') + '_ms'] = int(path.read_text()) if path.exists() else None
    result['step_elapsed_sec'] = duration(root, 'start', 'build-end')
    errors = []
    warnings = []
    mount = os.environ.get('CACHE_MOUNT', '')
    if mount:
        try:
            usage = subprocess.run(['df', '-B1', '--output=size,used,avail', mount],
                                   text=True, capture_output=True, timeout=30, check=True)
            size, used, available = map(int, usage.stdout.splitlines()[-1].split())
            result.update(cache_fs_size_bytes=size, cache_fs_used_bytes=used,
                          cache_fs_available_bytes=available)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(f'Persistent cache filesystem measurement failed: {error}')
    if result['build_outcome'] != 'success' or not result['build_sec']:
        errors.append('Build did not finish successfully')
    if not result['workload'] or result['workload'].get('output') != 'oci,gzip':
        errors.append('Complete image-export workload identity is missing')
    if os.environ.get('IMAGE_OUTCOME') != 'success' or not result['image'] or not result['image'].get('valid'):
        errors.append('Final PostHog image export was not verified')
    if result['mode'] == 'uncached':
        initial = root / 'initial-cache-records'
        result['initial_cache_records'] = int(initial.read_text()) if initial.exists() else None
        if result['initial_cache_records'] != 0:
            errors.append('Uncached builder was not verified empty before the build')
        if cached:
            errors.append('Uncached build unexpectedly reused executable steps')
    if result['mode'] != 'uncached':
        if result['cache_fs_used_bytes'] is None:
            errors.append('Persistent cache filesystem measurement is missing')
        elif result['cache_fs_used_bytes'] > int(os.environ['CACHE_BUDGET_BYTES']):
            errors.append('Filesystem cache usage exceeds the 100 GiB benchmark budget')
    if result['mode'] == 'cached' and (not result['cache_hit'] or cached == 0):
        errors.append('Warm leg did not return cache-hit=true and cached executable steps')
    result['valid'] = not errors
    result['errors'] = errors
    payload = json.dumps(result, separators=(',', ':'))
    print(payload)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        f.write(f'result={payload}\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write(f"PostHog {result['mode']}: setup={result['setup_sec']}s, "
                f"build={result['build_sec']}s, cached steps={all_cached}, "
                f"cached RUN/COPY/ADD={cached}/{total}, "
                f"cache filesystem used bytes={result['cache_fs_used_bytes']}, "
                f"OCI export={result['export_sec']}s, image={result['image']}.\n")
    for error in errors:
        print(f'::error::{error}')
    # Save the required measurements FIRST. A slow diskusage RPC must not
    # discard a successful build or prevent the warm matrix from running.
    # Only measure logical usage on the writer, avoiding diagnostic scan time
    # in the six comparison jobs. df above is the required cache-size metric.
    builder = os.environ.get('MONKCI_BUILDER')
    if result['mode'] == 'writer' and builder:
        print('Optional BuildKit cache usage (logical/reclaimable):', flush=True)
        try:
            subprocess.run(['docker', 'buildx', 'du', '--builder', builder, '--verbose'],
                           timeout=120, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            warning = f'BuildKit logical cache usage unavailable; filesystem measurement retained: {error}'
            print(f'::warning::{warning}')
            warnings.append(warning)
    if warnings:
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('\n'.join(warnings) + '\n')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
