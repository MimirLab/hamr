"""Recover source rows where ruled panels and unique column gaps agree."""

import re
from bisect import bisect_left, bisect_right
from ctypes import byref, c_int, create_string_buffer
from statistics import median

from pypdfium2 import raw

from .pdf_geometry import glyph_baseline
from .pdf_formula import MATH_FAMILY,letter,read_formula


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


def math_fonts(textpage,glyphs):
    result = set()
    name = create_string_buffer(256)
    flags = c_int()
    for ch, _, index in glyphs:
        if not ch.strip():
            continue
        length = raw.FPDFText_GetFontInfo(textpage,index,name,len(name),byref(flags))
        if 0<length<=len(name) and MATH_FAMILY.fullmatch(name.value.decode('ascii','replace').rsplit('+',1)[-1]):
            result.add(index)
    return result


def bar_owner(rect, glyphs):
    left,bottom,right,top = rect
    owners = []
    for at, (ch,(a,b,c,d),_) in enumerate(glyphs):
        if letter(ch) and a-.2<=left and c+.2>=right and b+.55*(d-b)<=(bottom+top)/2<=d+.2*(d-b):
            owners.append(at)
    return owners[0] if len(owners)==1 else None


def cell_text(glyphs, baseline, origins, size, font_size, mathematical, overlays):
    ordered = sorted(glyphs, key=lambda g: g[2])
    while ordered and ordered[0][0].isspace():ordered.pop(0)
    while ordered and ordered[-1][0].isspace():ordered.pop()
    pieces = []
    scripts = []
    for ch, _, index in ordered:
        position = len(pieces)
        pieces.append(ch)
        delta = origins[index]-baseline
        if ch.strip() and font_size[index] < .85*size and abs(delta) >= .8:
            relation = 'superscript' if delta > 0 else 'subscript'
            if scripts and scripts[-1]['kind'] == relation and scripts[-1]['span'][1] == position:
                scripts[-1]['span'][1] += 1
            else:
                scripts.append({'kind':relation, 'span':[position, position+1]})
    source = ''.join(pieces)
    result = {'text':source,'scripts':scripts,'native_ranges':intervals(((i,i+1) for _,_,i in glyphs),0)}
    marks = []
    for at,(ch,rect,i) in enumerate(ordered):
        if ch in ('¯','\u0305') and (owner:=bar_owner(rect,ordered)) is not None:
            marks.append({'kind':'bar','span':[at,at+1],'owner':[owner,owner+1],'glyph':i})
    offsets = {i:at for at,(_,_,i) in enumerate(ordered)}
    for mark in overlays:
        indices = mark['native_characters']
        if not all(i in offsets for i in indices):
            continue
        if mark['relation']=='overlapping_paint' and len(indices)==1:
            owner = bar_owner(mark['rect'],ordered)
            # Distinguish visible mathematical paint from a line struck
            # through an ordinary identifier or a value in a table.
            if owner is not None and ordered[owner][2]==indices[0] and indices[0] in mathematical:
                marks.append({'kind':'bar','span':[owner,owner], 'owner':[owner,owner+1],'paint':mark})
        if mark['relation']=='stacked_digits':
            members = sorted(offsets[i] for i in indices)
            start,end = members[0],members[-1]+1
            if [i for i in range(start,end) if not source[i].isspace()] == members:
                numerator,denominator = (''.join(source[offsets[i]] for i in mark[side]) for side in ('above','below'))
                marks.append({'kind':'fraction','span':[start,end],'owner':[start,end],
                              'numerator':numerator,'denominator':denominator,'paint':mark})
    if marks:result['math_marks']=marks
    if (spans:=intervals(((at,at+1) for at,(_,_,index) in enumerate(ordered) if index in mathematical),0)):
        result['math_font_spans']=spans
    return result


def rows_in_panel(glyphs, panel, origins, font_size, mathematical, overlays):
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
    if not 1 <= len(runs) <= 12:
        return None
    equations = len(runs)==1
    occupied = horizontal_spans(body if equations else glyphs,.08)
    gaps = [(a, b) for (_, a), (b, _) in zip(occupied, occupied[1:]) if b-a >= 1.6]
    boundaries = [(a+b)/2 for a,b in gaps if b-a>=2.8*size] if equations else []
    if equations and not 1<=len(boundaries)<=5:
        return None
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
        return [cell_text(col,baseline,origins,size,font_size,mathematical,overlays) for col in columns]

    header_base = median(origins[i] for ch, _, i in header if ch.strip())
    headings = [cell_text(header,header_base,origins,size,font_size,mathematical,overlays)] if equations else split(header,header_base)
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
        if equations and any(c['text'].count('=')!=1 for c in cells):
            return None
        if any(cell['text'] for cell in cells):
            result.append({'rect':[left, min(g[1][1] for g in group), right, max(g[1][3] for g in group)],
                           'cells':cells, 'glyphs':{g[2] for g in group}, 'y':baseline})
    return {'headings':headings,'columns':boundaries,'rows':result,'equations':equations}


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
        observed = rows_in_panel(group,panel,origins,sizes,math_fonts(textpage,group),overlays)
        indices = set(origins)
        claimed.update(indices)
        if observed is None:
            raw_ranges = intervals(((i, i+1) for i in indices), 0)
            source = '\n'.join(textpage.get_text_range(a, b-a).strip() for a, b in raw_ranges).strip()
            results.append({**panel, 'source':source, 'native_ranges':raw_ranges, 'rows':None})
            continue
        for row in observed['rows']:
            row['painted_marks'] = [mark for mark in overlays if row['glyphs'].intersection(mark['native_characters'])]
            del row['glyphs']
        results.append({**panel,**observed})
    return sorted(results, key=lambda p: (-p['rect'][3], p['rect'][0])), claimed


def source_segments(panels, native):
    from .pdf_table_readings import AUTHOR_UNCERTAINTY,BRACKETED_UNIT_DECLARATION,ROW_READING_ISSUE,readings,typed_equation

    header_start = min((a for panel in panels for cell in panel.get('headings',()) for a, _ in cell['native_ranges']), default=0)
    declarations = list(AUTHOR_UNCERTAINTY.finditer(native[:header_start]))
    witness = declarations[0] if len(declarations)==1 and not re.search(r'\b(?:not|never|example)\b',declarations[0][0],re.I) else None
    convention = {'native_text_span':list(witness.span()), 'source':witness[0],
                  'scope':'preamble above the ruled table'} if witness else None
    unit_definitions = {}
    declarations = list(BRACKETED_UNIT_DECLARATION.finditer(native[:header_start]))
    if len(declarations)==1:
        note = declarations[0]
        base,power = note['unit'],note['power']
        evidence = {'source':note[0],'native_text_span':list(note.span()),'scope':'preamble above the ruled table'}
        unit_definitions[f'[{base}]'] = {'reading':note['positive'],'scripts':[],'evidence':evidence}
        for suffix in (power,power.replace('−','-')):
            unit_definitions[f'[{base}{suffix}]'] = {'reading':note['inverse'],'evidence':evidence,
                'scripts':[{'kind':'superscript','span':[1+len(base),1+len(base)+len(suffix)]}]}
    segments = []
    for panel in panels:
        rect = panel['rect']
        if panel['rows'] is None:
            segments.append({'text':panel['source'], 'rect':rect, 'y':rect[3], 'role':'table',
                             'locator':{'native_ranges':panel['native_ranges'], 'ruled_panel':rect},
                             'issue':'This ruled panel has no unique column and header association. Verify its cells before speech.'})
            continue
        headers = [h['text'] for h in panel['headings']]
        if panel['equations']:
            segments.append({'text':headers[0],'rect':rect,'y':rect[3],'role':'heading',
                             'locator':{'native_ranges':panel['headings'][0]['native_ranges'],'ruled_panel':rect}})
            edges = [rect[0],*panel['columns'],rect[2]]
            for row_index,row in enumerate(panel['rows'],1):
                for column,cell in enumerate(row['cells']):
                    location={'ruled_panel':rect,'panel_row':row_index,'panel_column':column,
                              'native_ranges':cell['native_ranges']}
                    compiled = typed_equation(cell,convention,unit_definitions) if not cell.get('math_marks') and not row['painted_marks'] else None
                    if compiled:location['pdf_equation']=compiled
                    segments.append({'text':cell['text'],'rect':[edges[column],row['rect'][1],edges[column+1],row['rect'][3]],
                                     'y':row['y'],'role':'equation','locator':location,
                                     'issue':None if compiled else 'This equation cell requires a verified reading.'})
            continue
        for row_index, row in enumerate(panel['rows'], 1):
            values = [cell['text'] for cell in row['cells']]
            location = {'ruled_panel':rect, 'panel_row':row_index, 'column_boundaries':panel['columns'],
                        'rows':[headers, values], 'pdf_table_cells':row['cells'], 'pdf_table_headings':panel['headings']}
            issue = None
            selected = readings(headers,row['cells'],convention,unit_definitions)
            resolved = [m['paint'] for cell in selected or () for m in cell['fields'].get('marks',()) if 'paint' in m]
            if row['painted_marks'] and any(mark not in resolved for mark in row['painted_marks']):
                location['raster_text_marks'] = row['painted_marks']
                issue = 'A raster mark participates in mathematical text in this row. Verify the original notation before speech.'
            elif selected is None:
                issue = ROW_READING_ISSUE
                location['pdf_conventions']={'uncertainty':convention,'units':unit_definitions}
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
        compiled = surrounding_math(source,first,painted,positions) if shifted else None
        location = {'native_ranges':[[first,last]]}
        if compiled:location['pdf_inline_formula']=compiled
        segments.append({'text':source.strip(),'rect':rect,'y':rect[3],'locator':location,
                         'issue':'Typography outside the ruled table has unbound mathematical scripts. Verify the original region before speech.' if shifted and not compiled else None})
    return segments


def surrounding_math(source,first,painted,measurements):
    from .pdf_table_readings import BRACKETED_UNIT_DECLARATION
    if not BRACKETED_UNIT_DECLARATION.search(source):
        return None
    size=median(size for size,_ in measurements)
    baselines=[(a+b)/2 for a,b in intervals(((y,y) for font,y in measurements if font>=.85*size),.65)]
    if not baselines:
        return None
    script_indices=[]
    for (ch,_,index),(font,y) in zip(painted,measurements):
        if font>=.85*size:
            continue
        delta = y-min(baselines,key=lambda b:abs(y-b))
        offset=index-first
        if not (.8<=delta<=.65*size and 0<=offset<len(source) and source[offset]==ch):
            return None
        script_indices.append((offset,offset+1))
    if not script_indices:return None
    result=[]
    for start,end in intervals(script_indices,0):
        raw=source[start:end]
        if not re.fullmatch(r'[+−-]?[0-9]{1,3}',raw):return None
        parent=re.search(r'(?:\[[A-Za-z]\]|\([A-Za-z]/cycle\)|(?<![A-Za-z])[A-Za-z])$',source[:start])
        if not parent:return None
        a=parent.start()
        cell={'text':source[a:end],'scripts':[{'kind':'superscript','span':[start-a,end-a]}]}
        spoken=read_formula(cell)
        if not spoken:return None
        result.append({'span':[a,end],'script':[start,end],'source':source[a:end],'reading':spoken})
    parts=[];cursor=0
    for item in result:
        a,b=item['span']
        if a<cursor:return None
        parts.extend((source[cursor:a],item['reading']));cursor=b
    parts.append(source[cursor:])
    padding=len(source)-len(source.lstrip())
    for item in result:
        item['span']=[v-padding for v in item['span']]
        item['script']=[v-padding for v in item['script']]
    return {'source':source.strip(),'reading':''.join(parts).strip(),'formulas':result}
