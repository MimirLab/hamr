"""Summarize tensor precision from a bounded GGUF v3 header without loading weights."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct
from urllib.request import Request, urlopen

LIMIT = 16 * 1024 * 1024
NUMERIC = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f', 7: 'B', 10: 'Q', 11: 'q', 12: 'd'}
DTYPE = {0: 'F32', 1: 'F16', 8: 'Q8_0', 12: 'Q4_K', 14: 'Q6_K', 30: 'BF16'}


def inspect(data, *, layout=False):
    offset = 0

    def take(fmt):
        nonlocal offset
        value = struct.unpack_from('<' + fmt, data, offset)[0]
        offset += struct.calcsize('<' + fmt)
        return value

    def skip_bytes(count):
        nonlocal offset
        offset += count
        if offset > len(data):
            raise ValueError('GGUF header exceeds input budget')

    def string(decode=False):
        size = take('Q')
        start = offset
        skip_bytes(size)
        return data[start:offset].decode() if decode else None

    def skip(kind):
        if kind == 8:
            string()
        elif kind == 9:
            subtype, count = take('I'), take('Q')
            if subtype in NUMERIC:
                skip_bytes(count * struct.calcsize('<' + NUMERIC[subtype]))
            elif subtype == 8 and count <= (len(data) - offset) // 8:
                for _ in range(count):
                    string()
            else:
                raise ValueError('Unsupported or oversized metadata array')
        elif kind in NUMERIC:
            take(NUMERIC[kind])
        else:
            raise ValueError('Unknown metadata type')

    if take('I') != 0x46554747 or take('I') != 3:
        raise ValueError('Expected a GGUF v3 file')
    tensor_count, metadata_count = take('Q'), take('Q')
    if tensor_count > 65536 or metadata_count > 65536:
        raise ValueError('GGUF entry count exceeds inspection budget')
    alignment = 32
    for _ in range(metadata_count):
        name = string(decode=True)
        kind = take('I')
        if name == 'general.alignment' and kind == 4:
            alignment = take('I')
        else:
            skip(kind)
    if alignment < 8 or alignment > 4096 or alignment & (alignment - 1):
        raise ValueError('Unsupported GGUF alignment')
    metadata_end = offset
    stages = defaultdict(Counter)
    codec = defaultdict(Counter)
    total = Counter()
    tensors = []
    for _ in range(tensor_count):
        name = string(decode=True)
        dims = take('I')
        if not 1 <= dims <= 4:
            raise ValueError('Unexpected GGUF tensor rank')
        shape = tuple(take('Q') for _ in range(dims))
        kind = take('I')
        dtype = DTYPE.get(kind, str(kind))
        position = take('Q')  # Relative to the data section.
        if layout:
            tensors.append((name, shape, kind, position))
        total[dtype] += 1
        stages[name.split('.')[0]][dtype] += 1
        if name.startswith('codec.'):
            codec['.'.join(name.split('.')[:2])][dtype] += 1
    result = {'tensor_count': tensor_count, 'types': total, 'stages': stages, 'codec_groups': codec}
    if layout:
        result.update(alignment=alignment, metadata_end=metadata_end,
                      data_offset=(offset + alignment - 1) // alignment * alignment, tensors=tensors)
    return result


def read_header(source):
    if source.startswith('https://'):
        request = Request(source, headers={'Range': f'bytes=0-{LIMIT-1}'})
        with urlopen(request, timeout=30) as response:
            interval = response.headers.get('Content-Range', '')
            if response.status != 206 or not interval.startswith('bytes 0-'):
                raise ValueError('Server did not honor the bounded byte range')
            return response.read(LIMIT)
    with Path(source).open('rb') as stream:
        return stream.read(LIMIT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='+')
    args = parser.parse_args()
    print(json.dumps({source: inspect(read_header(source)) for source in args.source}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
