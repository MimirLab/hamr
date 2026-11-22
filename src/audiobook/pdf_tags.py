"""Map visible PDF glyphs to authored paragraph tags on their own page."""
from collections import defaultdict
from ctypes import create_string_buffer

from pypdfium2 import raw


INLINE_TAGS = {'Span', 'Quote', 'Link', 'Em', 'Strong'}


def paragraph_owners(page):
    """Return unambiguous paragraph owners for page-local marked content IDs."""
    tree = raw.FPDF_StructTree_GetForPage(page)
    if not tree:
        return {}
    owners = defaultdict(set)
    remaining = 10000

    def visit(element, path, paragraph=None):
        nonlocal remaining
        if not element or not remaining or len(path) > 32:
            return
        buffer = create_string_buffer(32)
        size = raw.FPDF_StructElement_GetType(element, buffer, len(buffer))
        role = buffer.raw[:size-2].decode('utf-16-le', errors='replace') if 2 <= size <= len(buffer) else ''
        if role == 'P':
            paragraph = path
        elif role not in INLINE_TAGS:
            paragraph = None
        for index in range(raw.FPDF_StructElement_CountChildren(element)):
            if not remaining:
                return
            remaining -= 1
            child = raw.FPDF_StructElement_GetChildAtIndex(element, index)
            if child:
                visit(child, (*path, index), paragraph)
            elif paragraph is not None:
                content_id = raw.FPDF_StructElement_GetChildMarkedContentID(element, index)
                if content_id >= 0:
                    owners[content_id].add(paragraph)

    try:
        for index in range(raw.FPDF_StructTree_CountChildren(tree)):
            if not remaining:
                break
            remaining -= 1
            visit(raw.FPDF_StructTree_GetChildAtIndex(tree, index), (index,))
    finally:
        raw.FPDF_StructTree_Close(tree)
    if not remaining:
        return {}
    return {content_id: '.'.join(map(str, next(iter(paths)))) for content_id, paths in owners.items()
            if len(paths) == 1}


def line_paragraph(textpage, glyphs, owners):
    """Identify a paragraph only when it owns every visible glyph of the line."""
    groups = set()
    for _, _, character, _, index in glyphs:
        if character.isspace():
            continue
        obj = raw.FPDFText_GetTextObject(textpage, index)
        paragraph = owners.get(raw.FPDFPageObj_GetMarkedContentID(obj)) if obj else None
        if paragraph is None:
            return None
        groups.add(paragraph)
        if len(groups) > 1:
            return None
    return next(iter(groups), None)
