"""A cache solve, incomplete tar or wrong-stage artifact cannot pass as an image."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import verify_image


def artifact(path, missing_layer=False, arch='amd64', cmd=None, truncated=False):
    contents = {}

    def add(content):
        content = content if isinstance(content, bytes) else json.dumps(content).encode()
        digest = hashlib.sha256(content).hexdigest()
        contents[f'blobs/sha256/{digest}'] = content
        return dict(digest=f'sha256:{digest}', size=len(content))

    layer = add(b'compressed layer bytes')
    config = add(dict(os='linux', architecture=arch,
                      config=dict(Cmd=cmd or ['./bin/docker']),
                      rootfs=dict(diff_ids=['sha256:' + 'a' * 64])))
    manifest = add(dict(schemaVersion=2, config=config, layers=[layer]))
    contents['index.json'] = json.dumps(dict(schemaVersion=2, manifests=[manifest])).encode()
    contents['oci-layout'] = b'{"imageLayoutVersion":"1.0.0"}'
    if missing_layer:
        del contents['blobs/sha256/' + layer['digest'].split(':')[1]]
    if truncated:
        contents['blobs/sha256/' + layer['digest'].split(':')[1]] = b'short'
    with tarfile.open(path, 'w') as archive:
        for name, content in contents.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


class ImageTests(unittest.TestCase):
    def test_complete_native_posthog_image(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            artifact(path)
            result = verify_image.verify(path)
            self.assertTrue(result['valid'])
            self.assertEqual(result['layer_count'], 1)
            self.assertEqual(result['archive_bytes'], path.stat().st_size)

    def test_missing_layer_wrong_platform_wrong_stage_and_truncated_blob(self):
        for kwargs in (dict(missing_layer=True), dict(arch='arm64'),
                       dict(cmd=['/bin/sh']), dict(truncated=True)):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'image.tar'
                artifact(path, **kwargs)
                with self.assertRaises((ValueError, KeyError)):
                    verify_image.verify(path)


if __name__ == '__main__':
    unittest.main()
