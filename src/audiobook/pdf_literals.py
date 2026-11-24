"""Recognize boxed terminal transcripts using text and print geometry."""

from ctypes import byref, c_double, c_int
from statistics import median
import re

from pypdfium2 import raw


PROMPT = re.compile(r'^\$ (?:\./)?[a-z][\w./-]*(?:[ \t]+\S.*)?$')


def terminal_width(textpage, glyphs):
    x,y = c_double(),c_double()
    locations = []
    for _,_,char,_,index in glyphs:
        if not char.isspace():
            if not raw.FPDFText_GetCharOrigin(textpage,index,byref(x),byref(y)):
                return None
            locations.append((index,x.value,y.value))
    if len(locations)<3:
        return None
    steps = [(right[1]-left[1])/(right[0]-left[0])
             for left,right in zip(locations,locations[1:]) if 0<right[0]-left[0]<=4]
    if len(steps)!=len(locations)-1 or max(z for _,_,z in locations)-min(z for _,_,z in locations)>.12:
        return None
    width = median(steps)
    if width>1 and all(abs(width-step)<=.08 for step in steps):
        return width
    return None


def overlaps(a,b):
    return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]


def contains(box,line):
    return box[0]<=line[0] and box[1]<=line[1] and line[2]<=box[2] and line[3]<=box[3]


def group_terminal_transcripts(page,textpage,segments):
    """Return a single literal block when a full shaded panel is a transcript."""
    if not any(PROMPT.fullmatch(row['text']) for row in segments):
        return segments
    panels = []
    fill,stroke = c_int(),c_int()
    for item in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
        if raw.FPDFPath_GetDrawMode(item,byref(fill),byref(stroke)) and fill.value and not stroke.value:
            box = list(item.get_bounds())
            if box[2]-box[0]>=100 and box[3]-box[1]>=12:
                panels.append(box)
    replacements, used = {},set()
    for box in sorted(panels,key=lambda r:(r[2]-r[0])*(r[3]-r[1])):
        rows = [index for index,row in enumerate(segments) if overlaps(box,row['rect'])]
        if not rows or set(rows)&used or rows[-1]-rows[0]+1!=len(rows):
            continue
        covered = [segments[index] for index in rows]
        if not PROMPT.fullmatch(covered[0]['text']) or any(
                row.get('issue') or not contains(box,row['rect']) or 'glyphs' not in row for row in covered):
            continue
        widths = [terminal_width(textpage,row['glyphs']) for row in covered]
        if None in widths or max(widths)-min(widths)>.1:
            continue
        ranges = [[min(glyph[-1] for glyph in row['glyphs']),max(glyph[-1] for glyph in row['glyphs'])+1]
                  for row in covered]
        rects = [row['rect'] for row in covered]
        replacements[rows[0]] = {'text':'\n'.join(row['text'] for row in covered),'role':'code','y':covered[0]['y'],
                                 'rect':[min(r[0] for r in rects),min(r[1] for r in rects),
                                         max(r[2] for r in rects),max(r[3] for r in rects)],
                                 'locator':{'native_ranges':ranges,'terminal_panel':box,
                                            'terminal_evidence':'Shell prompt and fixed-pitch glyphs fill a single painted panel.'}}
        used.update(rows)
    return [replacements.get(index,row) for index,row in enumerate(segments)
            if index not in used or index in replacements]
