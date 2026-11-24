"""Read HTML and XHTML table geometry from the same authored model."""
from collections import Counter
import re

from .epub_semantics import hidden
from .table_grid import Grid, join_passages, passage

PARAGRAPH_TAGS = {'div','p','ul','ol','li','dl','dt','dd','section','blockquote','address',
                  'pre','h1','h2','h3','h4','h5','h6'}
NONNEGATIVE = re.compile(r'[\t\n\f\r ]*\+?([0-9]+)')


def span(value,maximum,*,zero=False):
    match = NONNEGATIVE.match(value or '')
    if not match:
        return 1
    digits = match[1].lstrip('0')
    if not digits:
        return 0 if zero else 1
    return maximum if len(digits)>len(str(maximum)) else min(maximum,int(digits))


def own_rows(table):
    rows = [row for row in table.find_all('tr') if row.find_parent('table') is table]
    return sorted(rows,key=lambda row:row.parent.name=='tfoot')


def cells(row):
    return row.find_all(('td','th'),recursive=False)


def requires_grid(table):
    if table.find('table'):
        return True
    rows = own_rows(table)
    if any(row.parent.name=='thead' for row in rows[1:]):
        return True
    if len(rows)>1 and all(cells(row) and all(cell.name=='th' for cell in cells(row)) for row in rows[:2]):
        return True
    return any(span(cell.get('colspan'),1000)!=1 or span(cell.get('rowspan'),65534,zero=True)!=1
               for row in rows for cell in cells(row))


def read_grid(table,read,locate,*,identifiers=None):
    """Read source fragments and nested tables before flattening the grid."""
    grid = Grid()
    caption = table.find('caption',recursive=False)
    if caption is not None and not hidden(caption):
        text,spans,issue = read(caption,None)
        grid.caption = {**passage(text,spans),'location':locate(caption)}
        if issue:
            grid.issue(issue)
    lines = own_rows(table)
    groups = Counter(id(row.parent) for row in lines)
    column_band = True
    first_band_width = None

    def fragments(node):
        block = []
        for child in node.children:
            tag = getattr(child,'name',None)
            if tag not in PARAGRAPH_TAGS and tag!='table' and not (tag and child.find('table')):
                block.append(child)
                continue
            if block:
                yield node,tuple(block)
                block.clear()
            if hidden(child):
                continue
            if tag=='table':
                yield child,None
            else:
                yield from fragments(child)
        if block:
            yield node,tuple(block)

    for row in lines:
        grid.begin_row()
        members = cells(row)
        band = row.parent.name=='thead' or column_band and bool(members) and all(
            cell.name=='th' and cell.get('scope') not in ('row','rowgroup') for cell in members)
        column_band = column_band and band
        for cell in members:
            invisible = any(hidden(parent) for parent in (cell,*cell.parents))
            role = 'data'
            if cell.name=='th':
                scope = cell.get('scope','').lower()
                role = ('column' if scope in ('col','colgroup') or band and scope not in ('row','rowgroup')
                        else 'row')
            columns = span(cell.get('colspan'),1000)
            rows = span(cell.get('rowspan'),65534,zero=True)
            rows = groups[id(row.parent)] if rows==0 else rows
            if rows>groups[id(row.parent)]:
                grid.issue('An authored table cell extends beyond its row group.')
            loc = locate(cell)
            parts, pending, locations = [],[],[]

            def flush():
                part = join_passages(pending)
                if part['source'] or part['spans']:
                    part['location'] = locations[0] if len(locations)==1 else loc
                    if len(locations)>1:
                        part['source_members'] = locations.copy()
                    parts.append(part)
                pending.clear()
                locations.clear()

            # Keep physical hidden cells for occupancy and source proof.
            for owner,selection in fragments(cell):
                if selection is None:
                    flush()
                    parts.append({'table':read_grid(owner,read,locate,identifiers=identifiers)})
                    continue
                text,spans,issue = read(owner,selection)
                if issue and not invisible:
                    grid.issue(issue)
                if text or spans:
                    pending.append((text,spans,()))
                    locations.append(locate(owner))
            flush()
            identifier = cell.get('id')
            if identifiers is not None and identifiers.get(identifier)!=1:
                identifier = None
            refs = cell.get('headers') or []
            grid.place(parts,loc,columns=columns,rows=rows,role=role,identifier=identifier,
                       references=refs.split() if isinstance(refs,str) else refs,
                       implicit=True,hidden=invisible)
        if row.parent.name==table.name and first_band_width is not None and grid.column>first_band_width:
            # A leading ungrouped label band with a new implicit data column
            # has no reliable complete association.
            grid.issue('A table row extends past its authored column labels.')
        if band and row.parent is table:
            first_band_width = grid.column
        grid.end_row()
        groups[id(row.parent)] -= 1
    return grid.finish()
