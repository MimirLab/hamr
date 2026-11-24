"""Read DrawingML text runs, paragraphs and authored table coordinates."""

from . import table_grid
from .document_intake import attr, name
from .epub_semantics import uncertainty


DRAWING = frozenset(('http://schemas.openxmlformats.org/drawingml/2006/main',
                     'http://purl.oclc.org/ooxml/drawingml/main'))
TRUE = frozenset(('1','true','on'))


def is_drawing(node):
    return isinstance(node.tag,str) and node.tag.startswith('{') and node.tag[1:].split('}',1)[0] in DRAWING


def children(node,kind):
    return [item for item in node if name(item)==kind and is_drawing(item)] if node is not None else []


def child(node,kind):
    return next(iter(children(node,kind)),None)


def paragraphs(body):
    for node in children(body,'p'):
        pieces,spans,position = [],[],0
        for run in node:
            kind = name(run)
            if not is_drawing(run):
                continue
            if kind=='br':
                value = '\n'
                spans.append({'kind':'line_break','source':value,'source_start':position,'source_end':position+1})
            elif kind in ('r','fld'):
                value = ''.join(t.text or '' for t in children(run,'t'))
                properties = child(run,'rPr')
                baseline = attr(properties,'baseline') if properties is not None else None
                if value and baseline and baseline.lstrip('+-').isascii() and baseline.lstrip('+-').isdigit():
                    digits = baseline.lstrip('+-')
                    if digits.strip('0'):
                        spans.append({'kind':'subscript' if baseline.startswith('-') else 'superscript',
                                      'source':value,'source_start':position,'source_end':position+len(value)})
            else:
                continue
            pieces.append(value)
            position += len(value)
        raw = ''.join(pieces)
        text = raw.strip()
        if not text:
            continue
        left = len(raw)-len(raw.lstrip())
        for span in spans:
            span['source_start'] = max(0,span['source_start']-left)
            span['source_end'] = max(0,min(len(text),span['source_end']-left))
            span['source'] = text[span['source_start']:span['source_end']]
        spans = [span for span in spans if span['source']]
        yield node,text,spans


def cell_role(row,column,first_row,first_col):
    if first_row and not row:
        return 'both' if first_col and not column else 'column'
    return 'row' if first_col and not column else 'data'


def table(node,location):
    grid = table_grid.Grid()
    tracks = children(child(node,'tblGrid'),'gridCol')
    properties = child(node,'tblPr')
    first_row = properties is not None and properties.get('firstRow') in TRUE
    first_col = properties is not None and properties.get('firstCol') in TRUE
    if not tracks:
        grid.issue('The slide table is missing its declared columns.')
    for row_index,row in enumerate(children(node,'tr')):
        grid.begin_row()
        records = children(row,'tc')
        if len(records)!=len(tracks):
            grid.issue('The slide table row does not match its declared columns.')
        for column,cell in enumerate(records):
            grid.column = column
            place = location|{'xml_path':cell.getroottree().getpath(cell)}
            hmerge,vmerge = cell.get('hMerge') in TRUE,cell.get('vMerge') in TRUE
            parts = [table_grid.passage(text,spans) for _,text,spans in paragraphs(child(cell,'txBody'))]
            if hmerge or vmerge:
                owner = grid.active.get(column)
                if (owner and (hmerge and column<=owner['column'] or vmerge and row_index<=owner['row'])
                        or parts):
                    grid.issue('A slide table continuation conflicts with its authored cell.')
                grid.cover(place)
                continue
            for part in parts:
                if issue:=uncertainty(part['spans']):
                    grid.issue(issue)
            span = table_grid.count(cell.get('gridSpan'))
            if column+span>len(tracks):
                grid.issue('A slide table cell extends beyond its declared columns.')
            grid.place(parts,place,columns=span,
                       rows=table_grid.count(cell.get('rowSpan'),maximum=table_grid.MAX_ROWS),
                       role=cell_role(row_index,column,first_row,first_col))
        grid.column = max(grid.column,len(tracks))
        grid.end_row()
    return grid.finish()
