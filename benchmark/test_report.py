"""Check the cost/claim boundary before using the benchmark in advertisements."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import record
import report
import validate_cache


def iso(seconds):
    return datetime.fromtimestamp(1800000000 + seconds, timezone.utc).isoformat()


def job(name, start, seconds, conclusion='success'):
    return dict(name=name, started_at=iso(start), completed_at=iso(start + seconds),
                conclusion=conclusion)


def result(mode, build_seconds=10):
    return dict(mode=mode, sha='a' * 40, cpu=4, arch='x86_64', valid=True,
                errors=[], setup_sec=5, build_sec=build_seconds, export_sec=2,
                build_start_ms=1000, build_end_ms=1000 + build_seconds * 1000,
                workload=dict(sha='a' * 40, dockerfile_sha256='b' * 64,
                              platform='linux/amd64', buildkit='v0.28.0', output='oci,gzip'),
                image=dict(valid=True, archive_bytes=2**30), initial_cache_records=0,
                cache_hit=mode != 'uncached', cache_role='canonical_writer',
                cached_steps=10 if mode != 'uncached' else 0,
                executable_steps=10, cached_executable_steps=10 if mode != 'uncached' else 0,
                cache_fs_used_bytes=20 * 2**30 if mode != 'uncached' else None)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(
            POSTHOG_SHA='a' * 40, GH_RATE='0.012', MONK_RATE='0.008',
            CACHE_FEE='40', BATCHES='1000',
            NEEDS=json.dumps({'warmup': {'result': 'success'}}),
            UNCACHED_RESULTS=json.dumps({f'r{i}': json.dumps(result('uncached', 500)) for i in (1, 2, 3)}),
            CACHED_RESULTS=json.dumps({f'r{i}': json.dumps(result('cached')) for i in (1, 2, 3)}),
            WRITER_RESULT=json.dumps(result('writer', 500)),
        )
        self.jobs = [job(f'uncached-{i}', i, 601) for i in (1, 2, 3)]
        self.jobs += [job('warmup-writer', 620, 610)]
        self.jobs += [job(f'cached-{i}', 1250 + i, 61) for i in (1, 2, 3)]

    def render(self):
        with patch.dict(os.environ, self.env):
            return report.render(self.jobs)

    def test_cost_uses_sum_of_jobs_and_per_job_rounding(self):
        summary, valid = self.render()
        self.assertTrue(valid)
        # 3 * ceil(601/60) * $0.012 = $0.396; not batch span / 60.
        self.assertIn('| GitHub x64 4-vCPU uncached, projected from Monk uncached durations | $0.3960 |', summary)
        self.assertIn('| Monk CI 4-vCPU warm | $0.0245 |', summary)
        self.assertIn('| Additional cache fee allocated to one batch | $0.0400 |', summary)
        self.assertIn('| Separate seed job compute cost (this run) | $0.0814 |', summary)
        self.assertIn('9.57x', summary)  # 603s batch / 63s batch, with scheduling skew.

    def test_failed_job_withholds_claims(self):
        self.jobs[1]['conclusion'] = 'failure'
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertNotIn('Measured parallel batch speedup:', summary)
        self.assertNotIn('Projected GitHub-to-Monk warm usage savings:', summary)

    def test_missing_matrix_leg_withholds_claims(self):
        del self.jobs[0]
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('uncached-1: missing', summary)

    def test_serial_jobs_are_not_parallel_benchmark(self):
        self.jobs[:3] = [job(f'uncached-{i}', i * 700, 601) for i in (1, 2, 3)]
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('all three runners never overlapped', summary)

    def test_runner_mismatch_withholds_claims(self):
        values = json.loads(self.env['CACHED_RESULTS'])
        bad = result('cached')
        bad['cpu'] = 8
        values['r2'] = json.dumps(bad)
        self.env['CACHED_RESULTS'] = json.dumps(values)
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('cached-2: source or runner mismatch', summary)

    def test_reused_uncached_step_withholds_claims(self):
        values = json.loads(self.env['UNCACHED_RESULTS'])
        bad = result('uncached', 500)
        bad['cached_executable_steps'] = 1
        values['r2'] = json.dumps(bad)
        self.env['UNCACHED_RESULTS'] = json.dumps(values)
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('not a verified empty uncached build', summary)

    def test_different_dockerfile_withholds_claims(self):
        values = json.loads(self.env['CACHED_RESULTS'])
        bad = result('cached')
        bad['workload']['dockerfile_sha256'] = 'c' * 64
        values['r2'] = json.dumps(bad)
        self.env['CACHED_RESULTS'] = json.dumps(values)
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('Build workload differs', summary)

    def test_missing_image_withholds_claims(self):
        values = json.loads(self.env['CACHED_RESULTS'])
        bad = result('cached')
        bad['image'] = None
        values['r2'] = json.dumps(bad)
        self.env['CACHED_RESULTS'] = json.dumps(values)
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('final image was not verified', summary)

    def test_serial_builds_on_overlapping_runners_withholds_claims(self):
        values = {}
        for i in (1, 2, 3):
            r = result('cached')
            r['build_start_ms'] = i * 100000
            r['build_end_ms'] = r['build_start_ms'] + 10000
            values[f'r{i}'] = json.dumps(r)
        self.env['CACHED_RESULTS'] = json.dumps(values)
        summary, valid = self.render()
        self.assertFalse(valid)
        self.assertIn('all three builds never overlapped', summary)

    def test_skipped_warmup_has_zero_seed_cost(self):
        self.jobs = [j for j in self.jobs if j['name'] != 'warmup-writer']
        self.env['WRITER_RESULT'] = ''
        self.env['NEEDS'] = json.dumps({'warmup': {'result': 'skipped'}})
        summary, valid = self.render()
        self.assertTrue(valid)
        self.assertIn('| Separate seed job compute cost (this run) | $0.0000 |', summary)

    def test_count_unique_steps_excludes_metadata(self):
        log = '#1 [internal] load metadata for image\n#1 CACHED\n'
        log += '#2 [stage 1/2] COPY package.json .\n#2 CACHED\n'
        log += '#3 [stage 2/2] RUN pnpm build\n#3 0.5 progress\n#3 DONE 1.0s\n'
        self.assertEqual(record.count_steps(log), (2, 1, 2))

    def test_legacy_agent_canonical_volume_is_valid_writer(self):
        self.assertEqual(validate_cache.validate('writer', '', 'buildkit-cache', 'false',
                                                'buildkit-cache'), ('canonical_writer', 'volume'))

    def test_legacy_agent_clone_cannot_seed_cache(self):
        with self.assertRaisesRegex(ValueError, 'canonical writer'):
            validate_cache.validate('writer', '', 'clone-abcdef', 'true', 'buildkit-cache')

    def test_warm_clone_requires_real_hit(self):
        self.assertEqual(validate_cache.validate('cached', '', 'clone-abcdef', 'true',
                                                'buildkit-cache'), ('disposable_clone', 'volume'))
        with self.assertRaisesRegex(ValueError, 'cache-hit=true'):
            validate_cache.validate('cached', '', 'clone-abcdef', 'false', 'buildkit-cache')

    def test_explicit_role_cannot_contradict_volume(self):
        with self.assertRaisesRegex(ValueError, 'disagree'):
            validate_cache.validate('writer', 'canonical_writer', 'clone-abcdef', 'true', 'buildkit-cache')

    def test_unknown_volume_is_not_accepted_as_writer(self):
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            validate_cache.validate('writer', '', 'local-fallback', 'true', 'buildkit-cache')

    def test_cache_budget_and_missed_warm_cache_fail_leg(self):
        for hit, used in (('false', 20 * 2**30), ('true', 101 * 2**30)):
            with self.subTest(hit=hit, used=used), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / 'posthog-benchmark'
                root.mkdir()
                for name, value in {'start': 1000, 'setup-start': 1000, 'setup-end': 2000,
                                    'build-start': 2000, 'build-end': 3000}.items():
                    (root / name).write_text(str(value))
                (root / 'build.log').write_text('#1 [stage 1/1] RUN true\n#1 CACHED\n')
                env = dict(RUNNER_TEMP=temp, MODE='cached', POSTHOG_SHA='a' * 40,
                           BUILD_OUTCOME='success', CACHE_HIT=hit, CACHE_MOUNT='/cache',
                           IMAGE_OUTCOME='success',
                           MONKCI_BUILDER='test', CACHE_BUDGET_BYTES=str(100 * 2**30),
                           GITHUB_OUTPUT=str(Path(temp) / 'output'),
                           GITHUB_STEP_SUMMARY=str(Path(temp) / 'summary'))
                df = subprocess.CompletedProcess([], 0, f'Size Used Avail\n{300 * 2**30} {used} 100000\n')
                (root / 'workload.json').write_text(json.dumps(result('cached')['workload']))
                (root / 'image.json').write_text(json.dumps(result('cached')['image']))
                with patch.dict(os.environ, env), patch('record.subprocess.run', return_value=df):
                    self.assertEqual(record.main(), 1)
                payload = json.loads((Path(temp) / 'output').read_text().split('=', 1)[1])
                self.assertFalse(payload['valid'])
                self.assertEqual(len(payload['errors']), 1)
                self.assertIn('Warm leg' if hit == 'false' else 'exceeds', payload['errors'][0])


if __name__ == '__main__':
    unittest.main()
