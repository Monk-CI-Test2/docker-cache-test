"""Verify the exported OCI artifact; cache-only solves are not complete samples."""
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile


def verify(path):
    with tarfile.open(path, 'r:') as archive:
        def read_json(name):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 16 * 1024 * 1024:
                raise ValueError(f'Invalid image metadata: {name}')
            return archive.extractfile(member).read()

        def blob(descriptor, metadata=False):
            digest = descriptor['digest']
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('Invalid OCI blob digest')
            name = 'blobs/sha256/' + digest.split(':')[1]
            member = archive.getmember(name)
            if not member.isfile() or member.size != descriptor['size']:
                raise ValueError(f'Missing or truncated image blob: {digest}')
            if metadata:
                content = read_json(name)
                if hashlib.sha256(content).hexdigest() != digest.split(':')[1]:
                    raise ValueError(f'Image metadata digest mismatch: {digest}')
                return json.loads(content)

        if json.loads(read_json('oci-layout'))['imageLayoutVersion'] != '1.0.0':
            raise ValueError('Unsupported OCI image layout')
        index = json.loads(read_json('index.json'))
        if len(index['manifests']) != 1:
            raise ValueError('Expected exactly one native image')
        descriptor = index['manifests'][0]
        manifest = blob(descriptor, metadata=True)
        # Some exporters wrap the image manifest in a single-platform index.
        if 'manifests' in manifest:
            if len(manifest['manifests']) != 1:
                raise ValueError('Expected exactly one image in nested index')
            descriptor = manifest['manifests'][0]
            manifest = blob(descriptor, metadata=True)
        config = blob(manifest['config'], metadata=True)
        if config.get('os') != 'linux' or config.get('architecture') != 'amd64':
            raise ValueError('Exported image must be linux/amd64')
        layers = manifest['layers']
        if not layers or len(layers) != len(config['rootfs']['diff_ids']):
            raise ValueError('Missing image layers or inconsistent root filesystem')
        if config.get('config', {}).get('Cmd') != ['./bin/docker']:
            raise ValueError('Exported artifact is not the final PostHog runtime image')
        for layer in layers:
            blob(layer)
        return dict(valid=True, manifest_digest=descriptor['digest'],
                    archive_bytes=path.stat().st_size, layer_count=len(layers),
                    compressed_layer_bytes=sum(layer['size'] for layer in layers))


def main():
    root = Path(os.environ['RUNNER_TEMP']) / 'posthog-benchmark'
    try:
        result = verify(root / 'image.oci.tar')
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        result = dict(valid=False, error=str(error))
        print(f'::error::Image verification failed: {error}')
    (root / 'image.json').write_text(json.dumps(result))
    print(json.dumps(result, sort_keys=True))
    return int(not result['valid'])


if __name__ == '__main__':
    raise SystemExit(main())
