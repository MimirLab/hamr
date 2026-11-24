"""Bind visibly raised PDF digits to their adjacent base-line glyphs."""

from pypdfium2 import raw


def raised_digits(textpage, glyphs, baselines):
    raised = {}
    previous = None
    for char,rect,index in glyphs:
        if previous and char in '0123456789+−-':
            left,edge,last = previous
            if index==last+1 and not left.isspace():
                base = raised.get(last,last)
                host_size = raw.FPDFText_GetFontSize(textpage,base)
                size = raw.FPDFText_GetFontSize(textpage,index)
                lift = baselines[index]-baselines[base]
                gap = rect[0]-edge
                if (.45*host_size<=size<=.85*host_size and .10*host_size<=lift<=.68*host_size
                        and -.15*host_size<=gap<=.30*host_size and host_size>0):
                    raised[index] = base
        previous = char,rect[2],index
    return raised


def spans(source, glyph_ranges):
    groups = []
    for first,last,index,base in glyph_ranges:
        if groups and first==groups[-1]['source_end'] and base==groups[-1]['native_base']:
            groups[-1]['source_end'] = last
            groups[-1]['native_range'][1] = index+1
        else:
            groups.append({'kind':'superscript','source_start':first,'source_end':last,
                           'native_base':base,'native_range':[index,index+1],
                           'origin':'Native PDF glyph size and rise relative to the adjacent base'})
    for item in groups:
        item['source'] = source[item['source_start']:item['source_end']]
    return groups
