"""Recover source rows where ruled panels and unique column gaps agree."""

import re
from bisect import bisect_left, bisect_right
from statistics import median

from pypdfium2 import raw

from .pdf_geometry import glyph_baseline


def intervals(pairs, gap):
    spans = []
    for left, right in sorted(pairs):
        if spans and left-spans[-1][1] <= gap:
            spans[-1][1] = max(right, spans[-1][1])
        else:
            spans.append([left, right])
    return spans


def horizontal_spans(glyphs, gap):
    return intervals(((rect[0], rect[2]) for ch, rect, _ in glyphs if ch.strip()), gap)


def cell_text(glyphs, baseline, origins, size, font_size):
    ordered = sorted(glyphs, key=lambda g: g[2])
    pieces = []
    scripts = []
    for ch, _, index in ordered:
        if not pieces and ch.isspace():
            continue
        position = len(pieces)
        pieces.append(ch)
        delta = origins[index]-baseline
        if ch.strip() and font_size[index] < .85*size and abs(delta) >= .8:
            relation = 'superscript' if delta > 0 else 'subscript'
            if scripts and scripts[-1]['kind'] == relation and scripts[-1]['span'][1] == position:
                scripts[-1]['span'][1] += 1
            else:
                scripts.append({'kind':relation, 'span':[position, position+1]})
    return {'text':''.join(pieces).rstrip(), 'scripts':scripts,
            'native_ranges':intervals(((index, index+1) for _, _, index in ordered), 0)}


def rows_in_panel(glyphs, panel, origins, font_size):
    left, bottom, right, top = panel['rect']
    levels = panel['rule_levels']
    if len(levels) < 3:
        return None
    header_rule = levels[1]
    header = [g for g in glyphs if (g[1][1]+g[1][3])/2 > header_rule]
    body = [g for g in glyphs if (g[1][1]+g[1][3])/2 <= header_rule]
    if not body or not any(ch.strip() for ch, _, _ in header):
        return None
    size = median(font_size[i] for ch, _, i in glyphs if ch.strip())
    if size <= 0:
        return None
    runs = horizontal_spans(header, max(5, .85*size))
    if not 2 <= len(runs) <= 12:
        return None
    occupied = horizontal_spans(glyphs, .08)
    gaps = [(a, b) for (_, a), (b, _) in zip(occupied, occupied[1:]) if b-a >= 1.6]
    boundaries = []
    for (_, edge), (other, _) in zip(runs, runs[1:]):
        choices = sorted(((b-a, (a+b)/2) for a, b in gaps if a >= edge-.05 and b <= other+.05), reverse=True)
        if not choices or len(choices)>1 and choices[0][0] < 2*choices[1][0]+2:
            return None
        boundaries.append(choices[0][1])

    def split(items, baseline):
        columns = [[] for _ in range(len(boundaries)+1)]
        for glyph in items:
            x0, _, x1, _ = glyph[1]
            columns[bisect_right(boundaries, (x0+x1)/2)].append(glyph)
        return [cell_text(col, baseline, origins, size, font_size) for col in columns]

    header_base = median(origins[i] for ch, _, i in header if ch.strip())
    headings = split(header, header_base)
    if any(not any(c.isalpha() for c in h['text']) or h['scripts'] for h in headings):
        return None
    anchors = intervals(((origins[i], origins[i]) for ch, _, i in body if ch.strip() and font_size[i] >= .85*size), .65)
    baselines = [(a+b)/2 for a, b in anchors]
    if len(baselines) < 2 or any(b-a < size*.85 for a, b in zip(baselines, baselines[1:])):
        return None
    divisions = [(a+b)/2 for a, b in zip(baselines, baselines[1:])]
    row_glyphs = [[] for _ in baselines]
    for glyph in body:
        row_glyphs[bisect_left(divisions, origins[glyph[2]])].append(glyph)
    result = []
    for baseline, group in reversed(list(zip(baselines, row_glyphs))):
        cells = split(group, baseline)
        if any(cell['text'] for cell in cells):
            result.append({'rect':[left, min(g[1][1] for g in group), right, max(g[1][3] for g in group)],
                           'cells':cells, 'glyphs':{g[2] for g in group}, 'y':baseline})
    return headings, boundaries, result


def framed_source(textpage, glyphs, panels, overlays):
    """Give each glyph in a ruled rectangle one owner, even for unresolved panels."""
    groups = [[] for _ in panels]
    for glyph in glyphs:
        left, bottom, right, top = glyph[1]
        x, y = (left+right)/2, (bottom+top)/2
        for panel, group in zip(panels, groups):
            x0, y0, x1, y1 = panel['rect']
            if x0 < x < x1 and y0 < y < y1:
                group.append(glyph)
                break
    claimed = set()
    results = []
    for panel, group in zip(panels, groups):
        if not any(g[0].strip() for g in group):
            continue
        origins = {i:glyph_baseline(textpage, i, 0, rect[1]) for _, rect, i in group}
        sizes = {i:raw.FPDFText_GetFontSize(textpage, i) for i in origins}
        observed = rows_in_panel(group, panel, origins, sizes)
        indices = set(origins)
        claimed.update(indices)
        if observed is None:
            raw_ranges = intervals(((i, i+1) for i in indices), 0)
            source = '\n'.join(textpage.get_text_range(a, b-a).strip() for a, b in raw_ranges).strip()
            results.append({**panel, 'source':source, 'native_ranges':raw_ranges, 'rows':None})
            continue
        headings, boundaries, rows = observed
        for row in rows:
            row['painted_marks'] = [mark for mark in overlays if row['glyphs'].intersection(mark['native_characters'])]
            del row['glyphs']
        results.append({**panel, 'headings':headings, 'columns':boundaries, 'rows':rows})
    return sorted(results, key=lambda p: (-p['rect'][3], p['rect'][0])), claimed


def source_segments(panels, native):
    from .pdf_table_readings import AUTHOR_UNCERTAINTY, readings

    header_start = min((a for panel in panels for cell in panel.get('headings',()) for a, _ in cell['native_ranges']), default=0)
    declarations = list(AUTHOR_UNCERTAINTY.finditer(native[:header_start]))
    witness = declarations[0] if len(declarations)==1 and not re.search(r'\b(?:not|never|example)\b',declarations[0][0],re.I) else None
    convention = {'native_text_span':list(witness.span()), 'source':witness[0],
                  'scope':'preamble above the ruled table'} if witness else None
    segments = []
    for panel in panels:
        rect = panel['rect']
        if panel['rows'] is None:
            segments.append({'text':panel['source'], 'rect':rect, 'y':rect[3], 'role':'table',
                             'locator':{'native_ranges':panel['native_ranges'], 'ruled_panel':rect},
                             'issue':'This ruled panel has no unique column and header association. Verify its cells before speech.'})
            continue
        headers = [h['text'] for h in panel['headings']]
        for row_index, row in enumerate(panel['rows'], 1):
            values = [cell['text'] for cell in row['cells']]
            location = {'ruled_panel':rect, 'panel_row':row_index, 'column_boundaries':panel['columns'],
                        'rows':[headers, values], 'pdf_table_cells':row['cells'], 'pdf_table_headings':panel['headings']}
            issue = None
            if row['painted_marks']:
                location['raster_text_marks'] = row['painted_marks']
                issue = 'A raster mark participates in mathematical text in this row. Verify the original notation before speech.'
            else:
                selected = readings(headers, row['cells'], convention)
                if selected is None:
                    issue = 'Scientific notation in this table row requires a verified reading.'
                elif selected:
                    location['pdf_cell_readings'] = selected
            segments.append({'text':' | '.join(values), 'rect':row['rect'], 'y':rect[3], 'role':'table',
                             'locator':location, 'issue':issue, 'ordered_group':rect[0]})
    return segments


def surrounding_segments(textpage, glyphs):
    """Keep native reading order around matched panels and identify attached scripts."""
    groups = []
    for glyph in glyphs:
        index = glyph[2]
        if not groups or any(raw.FPDFText_IsGenerated(textpage, i) != 1
                             for i in range(groups[-1][-1][2]+1,index)):
            groups.append([])
        groups[-1].append(glyph)
    segments = []
    for group in groups:
        painted = [g for g in group if g[0].strip()]
        if not painted:
            continue
        positions = [(raw.FPDFText_GetFontSize(textpage, i), glyph_baseline(textpage,i,0,rect[1]))
                     for _, rect, i in painted]
        shifted = any(min(a,b) < .85*max(a,b) and .8 <= abs(y-z) < .65*max(a,b)
                      for (a,y), (b,z) in zip(positions,positions[1:]))
        first, last = group[0][2], group[-1][2]+1
        source = textpage.get_text_range(first,last-first)
        # Use PDFium's own hyphen flag at the corresponding native index.
        count = sum(ch=='-' and raw.FPDFText_IsHyphen(textpage,i)==1 for ch,_,i in group)
        if count and source.count('\ufffe') == count:
            source = source.replace('\ufffe','-')
        rect = [min(g[1][0] for g in painted),min(g[1][1] for g in painted),
                max(g[1][2] for g in painted),max(g[1][3] for g in painted)]
        segments.append({'text':source.strip(),'rect':rect,'y':rect[3],
                         'issue':'Typography outside the ruled table has unbound mathematical scripts. Verify the original region before speech.' if shifted else None})
    return segments
