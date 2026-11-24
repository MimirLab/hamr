"""Decode plain text without guessing legacy encodings or inventing structure."""
import codecs
import re

from .core import Document, Error, digest


def physical_lines(text):
    """Yield lines with exact endings, counting only CR and LF as line breaks."""
    return re.finditer(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$', text)


def plain(data,encoding=None):
    from .importers import block

    if encoding is None:
        encoding = 'utf-8'
        marks = ((codecs.BOM_UTF32_LE,'utf-32'),(codecs.BOM_UTF32_BE,'utf-32'),
                 (codecs.BOM_UTF16_LE,'utf-16'),(codecs.BOM_UTF16_BE,'utf-16'),(codecs.BOM_UTF8,'utf-8-sig'))
        for mark,codec in marks:
            if data.startswith(mark):
                encoding = codec
                break
    try:
        encoding = codecs.lookup(encoding).name
        ''.encode(encoding)
    except (LookupError, ValueError) as exc:
        raise Error('text_encoding',f'{encoding} does not name a recognized text encoding.',
                    'Choose the known source encoding, such as windows-1252, or export as UTF-8.') from exc
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise Error('text_encoding', f'Plain text cannot be decoded as {encoding} at byte {exc.start}.',
                    'Use --encoding for a known legacy encoding or export as UTF-8.') from exc
    if '\0' in text:
        raise Error('text_encoding','The decoded file has embedded NUL bytes and may be binary.',
                    'Choose a readable text export or a correctly encoded Unicode copy.')
    out = []
    start = first = None
    lines = blank = 0

    def finish(end, last):
        exact = text[start:end]
        loc = {'format': 'text', 'lines': [first, last], 'start': start, 'end': end, 'exact': exact}
        out.append(block(exact.rstrip('\r\n'), loc, evidence=['Exact decoded lines in source order']))

    for status in physical_lines(text):
        lines += 1
        if not status[0].strip(' \t\r\n'):
            blank += 1
            if start is not None:
                finish(status.start(), lines - 1)
                start = None
        elif start is None:
            start, first = status.start(), lines
    if start is not None:
        finish(len(text), lines)
    inventory = [{'kind': 'decoded_lines', 'encoding': encoding, 'line_count': lines, 'blank_lines': blank,
                  'represented_lines': lines - blank, 'coordinate_basis': 'Decoded Unicode code points, excluding any byte-order mark'}]
    return Document('Text document', 'text', digest(data), out, inventory=inventory)
