"""Source-owned table cells shared by structured readers."""
from collections import Counter

from .core import Error

MAX_COLUMNS = 2000
MAX_ROWS = 10000
MAX_CELLS = 250000
HEADER_ISSUE = 'Authored table header references need a verified association.'


def count(value, *, default=1, minimum=1, maximum=MAX_COLUMNS):
    if value is None:
        return default
    try:
        result = int(value)
    except (ValueError,TypeError):
        result = minimum-1
    if not minimum<=result<=maximum:
        raise Error('source_structure_size','An authored table span or repetition is outside the supported range.')
    return result


def passage(source,spans=(),notes=()):
    return {'source':source,'spans':list(spans),'notes':list(notes)}


class Grid:
    """Track logical coordinates without copying a spanning cell's text."""
    def __init__(self):
        self.cells = []
        self.issues = []
        self.active = {}
        self.row = -1
        self.column = self.width = 0
        self.caption = None

    def begin_row(self, skip=0):
        self.row += 1
        if self.row>=MAX_ROWS:
            raise Error('source_structure_size','An authored table exceeds the row budget.')
        self.column = skip
        self.active = {column:cell for column,cell in self.active.items()
                       if cell['row']+cell['rows']>self.row}

    def end_row(self, skip=0):
        width = self.column+skip
        if width>MAX_COLUMNS:
            raise Error('source_structure_size','An authored table exceeds the column budget.')
        self.width = max(self.width,width)

    def issue(self,message):
        if message not in self.issues:
            self.issues.append(message)

    def claim(self,cell,first,last):
        if last>MAX_COLUMNS or len(self.cells)>=MAX_CELLS:
            raise Error('source_structure_size','An authored table exceeds the cell budget.')
        for index in range(first,last):
            if index in self.active:
                self.issue('Two source cells claim the same position in a table.')
            else:
                self.active[index] = cell
        self.column = last
        self.width = max(self.width,last)

    def place(self,parts,location,*,columns=1,rows=1,role='data',identifier=None,references=(),implicit=False,hidden=False):
        if implicit:
            while self.column in self.active:
                self.column += 1
        cell = {'row':self.row,'column':self.column,'columns':columns,'rows':rows,'role':role,
                'location':location,'parts':parts}
        if identifier:
            cell['id'] = identifier
        if references:
            cell['headers'] = list(references)
        if hidden:
            cell['hidden'] = True
        self.claim(cell,self.column,self.column+columns)
        self.cells.append(cell)
        return cell

    def cover(self,location,*,columns=1,continuation=None):
        """Validate a covered coordinate or extend a vertical Word origin."""
        first,last = self.column,self.column+columns
        if last>MAX_COLUMNS:
            raise Error('source_structure_size','An authored table exceeds the column budget.')
        if continuation is not None:
            if (continuation['column'],continuation['columns'],continuation['row']+continuation['rows'])!=(first,columns,self.row):
                self.issue('A vertical table continuation has no matching origin.')
            else:
                continuation['rows'] += 1
                continuation.setdefault('continuations',[]).append(location)
                self.claim(continuation,first,last)
        elif any(column not in self.active for column in range(first,last)):
            self.issue('A covered table position has no matching origin.')
        else:
            owner = self.active[first]
            owner.setdefault('continuations',[]).append(location)
        self.column = last

    def finish(self):
        if any(cell['row']+cell['rows']>self.row+1 for cell in self.cells):
            self.issue('An authored table cell extends past the end of its table.')
        identifiers = Counter(cell.get('id') for cell in self.cells if cell.get('id'))
        headers = {cell['id']:cell for cell in self.cells
                   if cell.get('id') and identifiers[cell['id']]==1 and cell['role']!='data' and not cell.get('hidden')}
        for cell in self.cells:
            if cell.get('hidden'):
                continue
            if any(ref not in headers for ref in cell.get('headers',())):
                self.issue(HEADER_ISSUE)
            for part in cell['parts']:
                if part.get('table',{}).get('issue'):
                    self.issue(part['table']['issue'])
        return {'height':self.row+1,'width':self.width,'cells':self.cells,
                'caption':self.caption,'issue':' '.join(self.issues) or None}


def source_text(grid):
    rows = [' | '.join(values) for values in source_rows(grid)]
    if grid.get('caption'):
        rows.insert(len(rows) if grid['caption'].get('after') else 0,grid['caption']['source'])
    return '\n'.join(rows).strip(' |\n')


def source_rows(grid):
    by_row = {}
    for cell in grid['cells']:
        text = ' '.join(source_text(part['table']) if 'table' in part else part['source'] for part in cell['parts'])
        by_row.setdefault(cell['row'],[]).append(text)
    return list(by_row.values())


def join_passages(parts):
    text, spans, notes = [],[],[]
    offset = 0
    for source,items,links in parts:
        if source and text:
            offset += 1
        spans.extend({**span,'source_start':span['source_start']+offset,'source_end':span['source_end']+offset}
                     for span in items)
        notes.extend({**note,'source_start':note['source_start']+offset} for note in links)
        if source:
            text.append(source)
            offset += len(source)
    return passage(' '.join(text),spans,notes)


def passages(grid, *, visible=True):
    caption = grid.get('caption')
    if caption and not caption.get('after'):
        yield caption
    for cell in grid['cells']:
        if visible and cell.get('hidden'):
            continue
        for part in cell['parts']:
            if 'table' in part:
                yield from passages(part['table'],visible=visible)
            else:
                yield part
    if caption and caption.get('after'):
        yield caption


def simple_matrix(grid):
    """Keep the established narration contract for a plain rectangular table."""
    if grid['issue'] or grid['caption'] or not grid['width'] or len(grid['cells'])!=grid['width']*grid['height']:
        return None
    rows = [[] for _ in range(grid['height'])]
    headers, entries = [],[]
    for cell in grid['cells']:
        row,column = cell['row'],cell['column']
        parts = cell['parts']
        if (column!=len(rows[row]) or cell['columns']!=1 or cell['rows']!=1 or
                cell['role'] in ('row','both') or (cell['role']=='column' and row) or cell.get('headers') or
                cell.get('hidden') or len(parts)>1 or parts and 'table' in parts[0]):
            return None
        if cell['role']=='column':
            headers.append(column)
        part = parts[0] if parts else passage('')
        rows[row].append(part['source'])
        if part['spans'] or part['notes']:
            entries.append({'row':row,'column':column,**part})
    if headers and len(headers)!=grid['width']:
        return None
    return rows,([0] if headers else []),entries
