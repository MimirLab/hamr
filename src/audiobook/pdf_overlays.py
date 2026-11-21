"""Confirm PDFium's duplicate paint observations against isolated objects."""
from collections import defaultdict
import ctypes

from pypdfium2 import PdfiumError, raw

from .core import Error


MAX_PROBES = 8


def extract(obj, textpage):
    obj.textpage = textpage
    try:
        return obj.extract().strip()
    finally:
        obj.textpage = None


def fill_color(obj):
    components = [ctypes.c_uint() for _ in range(4)]
    if raw.FPDFPageObj_GetFillColor(obj, *[ctypes.byref(item) for item in components]):
        return tuple(item.value for item in components)
    return None


def verified_overlays(page, textpage):
    """Keep one admitted text occurrence when two coincident paints are proven equal.

    The ordinary PDFium reading has already admitted only one of the two.
    Probe the second object in memory and restore the page before returning.
    """
    groups = defaultdict(list)
    for position, obj in enumerate(page.get_objects(max_depth=1)):
        if obj.type != raw.FPDF_PAGEOBJ_TEXT:
            continue
        try:
            groups[tuple(obj.get_bounds())].append((position, obj))
        except PdfiumError:
            continue

    findings = []
    probes = 0
    for rect, group in groups.items():
        if len(group) != 2:
            continue
        if probes >= MAX_PROBES:
            break
        (first_index, first), (second_index, second) = group
        if first.get_matrix().get() != second.get_matrix().get() or first.get_font_size() != second.get_font_size():
            continue
        if not all(raw.FPDFTextObj_GetTextRenderMode(obj) == raw.FPDF_TEXTRENDERMODE_FILL for obj in (first, second)):
            continue
        color = fill_color(first)
        if not color or color[3] != 255 or color != fill_color(second):
            continue
        if ctypes.cast(raw.FPDFTextObj_GetFont(first), ctypes.c_void_p).value != ctypes.cast(raw.FPDFTextObj_GetFont(second), ctypes.c_void_p).value:
            continue
        try:
            text = extract(first, textpage)
            if not text or extract(second, textpage):
                continue
            if not raw.FPDFPageObj_SetIsActive(first, False):
                continue
            probes += 1
            try:
                isolated = page.get_textpage()
                try:
                    other = extract(second, isolated)
                finally:
                    isolated.close()
            finally:
                if not raw.FPDFPageObj_SetIsActive(first, True):
                    raise Error('pdf_geometry', 'Unable to restore the PDF page after checking a coincident text object.')
        except (PdfiumError, UnicodeError):
            continue
        if other == text:
            findings.append({'rect':list(rect), 'text':text, 'objects':[first_index,second_index],
                             'evidence':'Identical text, geometry, font and paint in two isolated PDF objects at one visible position.'})
    return findings
