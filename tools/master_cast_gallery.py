"""Build the public cast introductions from their original Git revision.

Run this in a Hamr checkout with FFmpeg, pointing it at an empty output directory.
The tool measures every decoded export and leaves the checkout untouched.
"""
import argparse
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from audiobook.core import digest, run, writejson
from audiobook.mastering import GALLERY, METER, encode_checked, meter, run_audio


def master_gallery(revision, output):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Use the full Git commit containing the original introductions.')
    manifest = json.loads(run(['git', 'show', f'{revision}:docs/listen/manifest.json']).stdout)
    if manifest['schema_version'] != 1 or any(output.iterdir()):
        raise ValueError('Choose an original gallery and an empty output directory.')

    readings = []
    for recording in manifest['recordings']:
        name = recording['file']
        if name != recording['character'] + '.mp3' or not re.fullmatch(r'[a-z0-9-]+\.mp3', name):
            raise ValueError(f'Unexpected cast file {name}.')
        original = run(['git', 'show', f'{revision}:docs/listen/{name}']).stdout
        original_hash = digest(original)
        if original_hash != recording['sha256']:
            raise ValueError(f'Original introduction checksum differs for {name}.')
        path = output / name
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'original.mp3'
            source.write_bytes(original)
            reading = meter(source)

            def encode(gain):
                run_audio(['ffmpeg', '-v', 'error', '-nostdin', '-xerror', '-y',
                           '-i', str(source), '-map', '0:a:0', '-map_metadata', '-1',
                           '-af', f'volume={gain:.2f}dB:precision=double', '-ar', '24000', '-ac', '1',
                           '-c:a', 'libmp3lame', '-b:a', '96k', str(path)],
                          error_code='cast_mastering_failed', message=f'Could not encode {name}.')

            receipt = encode_checked(path, reading, GALLERY, encode)
        recording.update(source_sha256=original_hash, sha256=digest(path.read_bytes()))
        readings.append({'character': recording['character'], **receipt})

    values = [row['decoded']['integrated_lufs'] for row in readings]
    spread = round(max(values) - min(values), 2)
    within = sum(abs(value - GALLERY['target_lufs']) <= 1 for value in values)
    if len(readings) != 45 or spread > 6 or within < 40:
        raise ValueError(f'Cast balancing missed qualification ({spread} LU spread, {within} within 1 LU).')
    manifest.update(schema_version=2, mastering={'source_commit': revision, 'meter': METER, **GALLERY})
    writejson(output / 'manifest.json', manifest)
    return {'spread_lu': spread, 'within_1_lu': within,
            'max_true_peak_dbtp': max(row['decoded']['true_peak_dbtp'] for row in readings),
            'introductions': readings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('revision')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(master_gallery(args.revision, args.output), indent=2))


if __name__ == '__main__':
    main()
