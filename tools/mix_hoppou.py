"""Create a local Hoppou Q8 evaluation model with depth and codec from pinned F16."""
import argparse
from contextlib import ExitStack
import math
import os
from pathlib import Path
import struct

from audiobook.backend import CATALOG
from audiobook.core import filehash, loadjson, writejson
from tools.inspect_gguf import inspect, read_header

BLOCKS = {0: (1, 4), 1: (1, 2), 8: (32, 34)}
PROTECTED = ('dd.', 'codec.')


def size_of(shape, kind):
    width, byte_count = BLOCKS[kind]
    if not shape or any(not dim for dim in shape) or shape[0] % width:
        raise ValueError('Invalid GGML tensor shape or block encoding')
    return math.prod(shape) // width * byte_count


def logical_shape(shape):
    while len(shape) > 1 and shape[-1] == 1:
        shape = shape[:-1]
    return shape


def mix(f16, q8, output):
    catalog = loadjson(CATALOG)
    models = {}
    for name, path in (('hoppou-f16', f16), ('hoppou-q8', q8)):
        expected = catalog['packages'][name]['files'][0]
        if path.stat().st_size != expected['bytes'] or filehash(path) != expected['sha256']:
            raise ValueError(f'{path} does not match the pinned {name} model')
        header = read_header(str(path))
        models[name] = (path, header, inspect(header, layout=True), expected['sha256'])
    fp_path, _, fp_layout, fp_digest = models['hoppou-f16']
    q_path, q_header, q_layout, q_digest = models['hoppou-q8']
    fp_entries = {row[0]: row for row in fp_layout['tensors']}
    q_entries = {row[0]: row for row in q_layout['tensors']}
    if (fp_entries.keys() != q_entries.keys()
            or any(logical_shape(fp_entries[n][1]) != logical_shape(q_entries[n][1]) for n in fp_entries)):
        raise ValueError('F16 and Q8 tensor layouts differ')
    alignment = q_layout['alignment']
    entries = []
    data_size = 0
    header = bytearray(q_header[:q_layout['metadata_end']])
    for name, _, _, _ in q_layout['tensors']:
        protected = name.startswith(PROTECTED)
        _, shape, kind, offset = fp_entries[name] if protected else q_entries[name]
        layout = fp_layout if protected else q_layout
        count = size_of(shape, kind)
        encoded = name.encode()
        header.extend(struct.pack('<Q', len(encoded)))
        header.extend(encoded)
        header.extend(struct.pack('<I' + 'Q' * len(shape) + 'IQ', len(shape), *shape, kind, data_size))
        entries.append((protected, layout['data_offset'] + offset, count, data_size))
        data_size = (data_size + count + alignment - 1) // alignment * alignment
    header.extend(bytes(-len(header) % alignment))
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    partial = output.with_suffix(output.suffix + '.partial')
    with ExitStack() as stack:
        left = stack.enter_context(fp_path.open('rb'))
        right = stack.enter_context(q_path.open('rb'))
        target = stack.enter_context(partial.open('xb'))
        target.write(header)
        origin = len(header)
        for protected, source_offset, remaining, target_offset in entries:
            source = left if protected else right
            source.seek(source_offset)
            target.write(bytes(origin + target_offset - target.tell()))
            while remaining:
                chunk = source.read(min(remaining, 8 * 1024**2))
                if not chunk:
                    raise ValueError('Unexpected end of source tensor')
                target.write(chunk)
                remaining -= len(chunk)
        target.flush()
        os.fsync(target.fileno())
    # The app uses this same no-replace operation for final audio.
    from audiobook.jobs import rename_new
    rename_new(partial, output)
    result = inspect(read_header(str(output)))
    result.update(sha256=filehash(output), size=output.stat().st_size,
                  sources={'f16':fp_digest,'q8':q_digest}, higher_precision_groups=list(PROTECTED))
    writejson(output.with_suffix('.recipe.json'),result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('f16', type=Path)
    parser.add_argument('q8', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = mix(args.f16,args.q8,args.output)
    print(f'{result["size"]} bytes · SHA-256 {result["sha256"]}')


if __name__=='__main__':
    main()
