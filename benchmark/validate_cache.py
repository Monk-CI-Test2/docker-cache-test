"""Validate lease identity even when an older agent omits the role output."""
import os
import re


def validate(mode, reported_role, volume, cache_hit, canonical_volume):
    if mode not in ('writer', 'cached'):
        raise ValueError('Invalid persistent-cache benchmark mode')
    if not canonical_volume:
        raise ValueError('Expected canonical cache volume is not configured')
    if volume == canonical_volume:
        volume_role = 'canonical_writer'
    elif re.fullmatch(r'clone-[0-9a-f]+', volume):
        volume_role = 'disposable_clone'
    else:
        raise ValueError(f'Unrecognized persistent cache volume: {volume!r}')
    if reported_role and reported_role != volume_role:
        raise ValueError('Cache role and volume identity disagree')
    if mode == 'writer' and volume_role != 'canonical_writer':
        raise ValueError('Warmup needs the canonical writer to publish a snapshot')
    if mode == 'cached' and cache_hit != 'true':
        raise ValueError('Warm benchmark requires cache-hit=true')
    return volume_role, 'action' if reported_role else 'volume'


def main():
    try:
        role, source = validate(os.environ['MODE'], os.environ.get('CACHE_ROLE', ''),
                                os.environ.get('CACHE_VOLUME', ''), os.environ.get('CACHE_HIT', ''),
                                os.environ['CANONICAL_CACHE_VOLUME'])
        if not os.environ.get('MONKCI_BUILDER'):
            raise ValueError('Persistent Monk CI builder is missing')
    except ValueError as error:
        print(f'::error::{error}')
        return 1
    if source == 'volume':
        print(f'Agent omitted cache-role; verified {role} from volume '
              f"{os.environ['CACHE_VOLUME']!r}.")
    with open(os.environ['GITHUB_ENV'], 'a') as f:
        f.write(f'BENCHMARK_CACHE_ROLE={role}\nBENCHMARK_CACHE_ROLE_SOURCE={source}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
