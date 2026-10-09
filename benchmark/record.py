"""Record a benchmark leg without artifact storage or privileged Ceph access."""
import json
import os
from pathlib import Path
import re
import subprocess
import time


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


def main():
    root = Path(os.environ['RUNNER_TEMP']) / 'posthog-benchmark'
    log_path = root / 'build.log'
    log = log_path.read_text(errors='replace') if log_path.exists() else ''
    total, cached, all_cached = count_steps(log)
    result = {
        'mode': os.environ['MODE'], 'sha': os.environ['POSTHOG_SHA'],
        'build_outcome': os.environ['BUILD_OUTCOME'],
        'cache_hit': os.environ.get('CACHE_HIT') == 'true',
        'cache_role': os.environ.get('CACHE_ROLE', ''),
        'setup_sec': duration(root, 'setup-start', 'setup-end'),
        'build_sec': duration(root, 'build-start', 'build-end'),
        'executable_steps': total, 'cached_executable_steps': cached,
        'cached_steps': all_cached, 'cache_fs_size_bytes': None,
        'cache_fs_used_bytes': None, 'cache_fs_available_bytes': None,
        'cpu': os.cpu_count(), 'arch': os.uname().machine,
    }
    result['step_elapsed_sec'] = round(time.time() - int((root / 'start').read_text()) / 1000, 3)
    mount = os.environ.get('CACHE_MOUNT', '')
    if mount:
        usage = subprocess.run(['df', '-B1', '--output=size,used,avail', mount],
                               text=True, capture_output=True, timeout=30, check=True)
        size, used, available = map(int, usage.stdout.splitlines()[-1].split())
        result.update(cache_fs_size_bytes=size, cache_fs_used_bytes=used,
                      cache_fs_available_bytes=available)
    builder = os.environ.get('MONKCI_BUILDER') or os.environ.get('BENCHMARK_BUILDER')
    if builder:
        # BuildKit's logical size/reclaimable breakdown is printed alongside the
        # filesystem bytes. Neither is the Ceph physical snapshot allocation.
        print('BuildKit cache usage (logical/reclaimable):', flush=True)
        subprocess.run(['docker', 'buildx', 'du', '--builder', builder, '--verbose'],
                       timeout=60, check=True)
    errors = []
    if result['build_outcome'] != 'success' or not result['build_sec']:
        errors.append('Build did not finish successfully')
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
                f"cache filesystem used bytes={result['cache_fs_used_bytes']}.\n")
    for error in errors:
        print(f'::error::{error}')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
