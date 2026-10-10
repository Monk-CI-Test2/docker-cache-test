"""Required measurements survive optional diagnostics; invalid cold samples fail."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import record


class RecordTests(unittest.TestCase):
    def run_record(self, mode='writer', diagnostic_error=None, df_error=None,
                   cached=False, empty=True, image=True):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'posthog-benchmark'
            root.mkdir()
            for name, value in dict(start=1000, **{'setup-start': 1000, 'setup-end': 2000,
                                   'build-start': 2000, 'build-end': 5000}).items():
                (root / name).write_text(str(value))
            (root / 'build.log').write_text('#1 [stage 1/1] RUN true\n' +
                                           ('#1 CACHED\n' if cached else '#1 DONE 1.0s\n') +
                                           '#2 exporting to oci image format\n#2 DONE 2.0s\n')
            (root / 'workload.json').write_text(json.dumps({'sha': 'a' * 40, 'output': 'oci,gzip'}))
            if image:
                (root / 'image.json').write_text(json.dumps(dict(valid=True, archive_bytes=10000)))
            if empty:
                (root / 'initial-cache-records').write_text('0')
            env = dict(RUNNER_TEMP=temp, MODE=mode, POSTHOG_SHA='a' * 40,
                       BUILD_OUTCOME='success', IMAGE_OUTCOME='success', CACHE_HIT='true',
                       CACHE_MOUNT='/cache' if mode != 'uncached' else '',
                       MONKCI_BUILDER='test', CACHE_BUDGET_BYTES=str(100 * 2**30),
                       GITHUB_OUTPUT=str(Path(temp) / 'output'),
                       GITHUB_STEP_SUMMARY=str(Path(temp) / 'summary'))

            def command(args, **kwargs):
                if args[0] == 'df':
                    if df_error:
                        raise df_error
                    return subprocess.CompletedProcess(args, 0, 'Size Used Avail\n300000000000 20000000000 280000000000\n')
                # Reproduces the original failure after the measurements must
                # already have been passed to the downstream report.
                self.assertTrue((Path(temp) / 'output').exists())
                if diagnostic_error:
                    raise diagnostic_error
                return subprocess.CompletedProcess(args, 0)

            with patch.dict(os.environ, env), patch('record.subprocess.run', side_effect=command) as run:
                exit_code = record.main()
            payload = json.loads((Path(temp) / 'output').read_text().split('=', 1)[1])
            return exit_code, payload, [c.args[0][0] for c in run.call_args_list]

    def test_diskusage_timeout_and_failure_keep_successful_result(self):
        for error in (subprocess.TimeoutExpired(['docker'], 120),
                      subprocess.CalledProcessError(1, ['docker'])):
            with self.subTest(error=error):
                code, payload, _ = self.run_record(diagnostic_error=error)
                self.assertEqual(code, 0)
                self.assertTrue(payload['valid'])
                self.assertEqual(payload['cache_fs_used_bytes'], 20000000000)
                self.assertEqual(payload['export_sec'], 2)

    def test_required_filesystem_failure_keeps_invalid_result(self):
        code, payload, _ = self.run_record(df_error=subprocess.TimeoutExpired(['df'], 30))
        self.assertEqual(code, 1)
        self.assertFalse(payload['valid'])
        self.assertTrue(any('filesystem measurement failed' in e for e in payload['errors']))

    def test_cold_build_requires_empty_builder_and_no_cached_steps(self):
        code, payload, commands = self.run_record(mode='uncached')
        self.assertEqual(code, 0)
        self.assertTrue(payload['valid'])
        self.assertNotIn('docker', commands)
        for kwargs in (dict(empty=False), dict(cached=True)):
            code, payload, _ = self.run_record(mode='uncached', **kwargs)
            self.assertEqual(code, 1)
            self.assertFalse(payload['valid'])

    def test_image_export_required_and_warm_jobs_skip_diskusage(self):
        code, payload, _ = self.run_record(image=False)
        self.assertEqual(code, 1)
        self.assertIn('Final PostHog image export was not verified', payload['errors'])
        code, payload, commands = self.run_record(mode='cached', cached=True)
        self.assertEqual(code, 0)
        self.assertNotIn('docker', commands)


if __name__ == '__main__':
    unittest.main()
