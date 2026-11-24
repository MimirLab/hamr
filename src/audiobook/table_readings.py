"""Resolve only source-defined ditto marks in the same table column."""
import re


def ditto_marks(context,marker):
    defined=bool(re.search(r'''〃[”"’']?\s+(?:means|denotes)\s+[“"‘']?same as above''',context,re.I))
    marks={'〃'} if defined or marker=='ditto' else set()
    if marker=='double_quote':
        marks.add('"')
    return marks


def split_cell_fractions(rows, first_data_row=1, *, excluded=()):
    """Locate fraction-like fragments whose source cells must stay separate."""
    return [{'row':index,'columns':[column,column+1],'values':[first,second]}
            for index,row in enumerate(rows[first_data_row:],first_data_row)
            for column,(first,second) in enumerate(zip(row,row[1:]))
            if (index,column) not in excluded and (index,column+1) not in excluded
            and re.fullmatch(r'[0-9]+',first.strip()) and re.fullmatch(r'/\s*[0-9]+',second.strip())]


class TableNotes:
    def __init__(self,note_policy,chapter):
        self.policy = note_policy
        self.chapter = chapter
        self.seen = set()
        self.trailing = {}

    def read(self,part,*,heading=False):
        from .core import Block
        from .epub_semantics import reading
        from .source_notes import references

        source = Block('',part['source'],{'inline_semantics':part['spans']},'body',self.chapter)
        visible = {(ref['source_start'],ref['href']) for ref in references(part['spans'])}
        for note in part['notes']:
            if (note['source_start'],note['reference_href']) not in visible or note['href'] in self.seen:
                continue
            self.seen.add(note['href'])
            if note.get('reference_only') and self.policy!='companion':
                continue
            if self.policy=='inline' and not heading:
                source.links.append(note)
            else:
                self.trailing[note['href']] = note['reading']
        text,spans = reading(source,'inline',with_spans=True)
        leading = len(text)-len(text.lstrip())
        text = text.strip()
        return text,[(max(0,a-leading),min(len(text),b-leading)) for a,b in spans
                     if b>leading and a<leading+len(text)]

    def suffix(self):
        if self.policy=='companion':
            return ' A note is available in the companion.' if self.trailing else ''
        return ''.join(' Note. '+text+' End note.' for text in self.trailing.values())


def semantic_cells(block,note_policy, *, projected=()):
    readings=[]; protected={}; caption=''
    notes = TableNotes(note_policy,block.chapter)
    headers=set(block.locator.get('table_header_cells') or ())
    excluded={tuple(pair) for pair in block.locator.get('table_hidden_cells',())}
    entries=[*block.locator.get('table_cell_semantics',()),*projected]
    if entry:=block.locator.get('table_caption'):
        entries.insert(0,{'row':-1,'column':0,**entry})
    for cell in entries:
        row,column=cell['row'],cell['column']
        if (row,column) in excluded:continue
        text,spans = notes.read(cell,heading=(row==0 and column in headers))
        if row==-1:caption=text
        else:readings.append({'row':row,'column':column,'source':cell['source'],'reading':text})
        if spans:protected[row,column]=spans
    return readings, protected, notes.suffix(), caption


class GridNarration:
    """Render the authored origins in recorded row order."""
    def __init__(self,note_policy,chapter,marks):
        self.notes = TableNotes(note_policy,chapter)
        self.fragments = []
        self.protected = []
        self.views = []
        self.offset = 0
        self.marks = marks
        self.issue = None

    def append(self,text,spans=()):
        self.fragments.append(text)
        self.protected.extend((self.offset+a,self.offset+b) for a,b in spans)
        self.offset += len(text)

    def sentence(self,text,spans=()):
        if text:
            self.append(text,spans)
            self.append(' ' if text.endswith(('.','?','!')) else '. ')

    @staticmethod
    def owns_column(header,cell):
        return (header['row']+header['rows']<=cell['row'] and header['column']<=cell['column'] and
                header['column']+header['columns']>=cell['column']+cell['columns'])

    def table(self,grid):
        from collections import defaultdict

        self.append('Table. ')
        if grid['caption'] and not grid['caption'].get('after'):
            self.sentence(*self.notes.read(grid['caption'],heading=True))
        origins = [cell for cell in grid['cells'] if not cell.get('hidden')]
        headers = [cell for cell in origins if cell['role'] in ('column','both')]
        row_headers = [cell for cell in origins if cell['role'] in ('row','both')]
        by_id = {cell['id']:cell for cell in origins if cell.get('id')}
        assigned = {}
        used = set()
        titles = {id(cell) for cell in headers if cell['columns']==grid['width'] and cell['columns']>1
                  and any(child['row']>cell['row'] for child in headers)}
        # Index the authored columns once. A value that spans distinct columns
        # only inherits a header covering the full span.
        by_column = defaultdict(list)
        by_row = defaultdict(list)
        for header in headers:
            for column in range(header['column'],header['column']+header['columns']):
                by_column[column].append(header)
        for header in row_headers:
            by_row[header['row']].append(header)
        active_rows = []
        current_row = -1
        for cell in origins:
            if cell['row']!=current_row:
                current_row = cell['row']
                active_rows = [header for header in active_rows if header['row']+header['rows']>current_row]
                active_rows.extend(by_row[current_row])
            if cell['role']!='data' or not cell['parts']:
                continue
            refs = cell.get('headers')
            if refs:
                path = [by_id[ref] for ref in dict.fromkeys(refs) if ref in by_id]
            else:
                path = [header for header in active_rows if header['column']+header['columns']<=cell['column']]
                path.extend(header for header in by_column[cell['column']] if self.owns_column(header,cell))
            assigned[id(cell)] = path
            used.update(id(header) for header in path)

        spoken_headers = {}
        for cell in origins:
            if cell['role'] in ('column','row','both'):
                prepared = [self.notes.read(part,heading=True) for part in cell['parts'] if 'table' not in part]
                if len(prepared)==1:
                    spoken_headers[id(cell)] = prepared[0]
                elif prepared:
                    spoken_headers[id(cell)] = (' '.join(value for value,_ in prepared),())
        previous_row = None
        previous_labels = ()
        previous_values, row_values = {},{}
        for cell in origins:
            if cell['role']=='column' and id(cell) in used and id(cell) not in titles:
                # A header reused as a label still owns any nested story.
                for part in cell['parts']:
                    if 'table' in part:
                        self.table(part['table'])
                continue
            path = assigned.get(id(cell),())
            common = 0
            if cell['row']!=previous_row:
                consecutive = previous_row is not None and cell['row']==previous_row+1
                previous_values,row_values = (row_values if consecutive else {}),{}
                previous_labels = ()
            else:
                for current,previous in zip(path[:-1],previous_labels):
                    if current is not previous:
                        break
                    common += 1
            before = self.offset
            self.cell(cell,path,spoken_headers,common,previous_values,row_values,titles)
            if before!=self.offset:
                previous_labels = path
            previous_row = cell['row']
        if grid['caption'] and grid['caption'].get('after'):
            self.sentence(*self.notes.read(grid['caption'],heading=True))

    def cell(self,cell,path,headers,common,previous,row_values,titles):
        labels = [(header,*headers[id(header)]) for header in path
                  if id(header) in headers and headers[id(header)][0]]
        if not cell['parts']:
            return
        display = [(h,text,protected) for h,text,protected in labels[common:]
                   if id(h) not in titles and (h['role'] not in ('row','both') or h['row']!=cell['row'])]
        label_written = False
        for part in cell['parts']:
            if 'table' in part:
                if display and not label_written:
                    self.sentence(', '.join(text for _,text,_ in display))
                    label_written = True
                self.table(part['table'])
                continue
            value,spans = (headers.get(id(cell)) if id(cell) in headers and len(cell['parts'])==1
                           else self.notes.read(part,heading=cell['role']!='data'))
            if not value:
                continue
            identity = (cell['column'],cell['columns'],tuple(id(header) for header in path))
            source_location = part.get('location',cell['location'])
            antecedent = None
            if value in self.marks or value=='〃':
                prior = previous.get(identity)
                if value not in self.marks or prior is None or len(cell['parts'])!=1 or cell['role']!='data':
                    self.issue = 'Ditto cell has no source-defined value in its preceding authored column.'
                    continue
                value,spans,antecedent = prior
            if len(cell['parts'])==1 and cell['role']=='data':
                row_values[identity] = value,spans,antecedent or source_location
            if display and not label_written:
                if len(display)==1 and re.match(re.escape(display[0][1])+r'\b',value,re.I):
                    display = []
                for index,(_,text,protected) in enumerate(display):
                    self.append(', ' if index else '')
                    self.append(text,protected)
                if display:
                    self.append(': ')
                label_written = True
            source = {'location':source_location,'source':part['source'],
                      'header_path':[{'label':text,'row':h['row'],'column':h['column'],'role':h['role'],
                                      'location':h['location'],**({'id':h['id']} if 'id' in h else {})}
                                     for h,text,_ in labels]}
            if part.get('source_members'):
                source['source_members'] = part['source_members']
            if antecedent:
                source['inherited_from'] = antecedent
            leaf = next((label for h,label,_ in reversed(labels) if h['role']!='row'),labels[-1][1] if labels else '')
            self.views.append((self.offset,self.offset+len(value),cell['row'],cell['column'],leaf,value,source))
            self.sentence(value,spans)


def grid_for_speech(block,note_policy,context,*,marker=None):
    voice = GridNarration(note_policy,block.chapter,ditto_marks(context,marker))
    voice.table(block.locator['table_grid'])
    text = ''.join(voice.fragments).rstrip()
    if text=='Table.' and not voice.notes.suffix():
        text = ''
    return text+voice.notes.suffix(),voice.views,voice.protected,voice.issue


def rows_for_speech(rows,context, *, marker=None,cell_readings=(),header_columns=None,column_headers=None,excluded=()):
    if cell_readings:
        rows=[list(row) for row in rows]
        for cell in cell_readings:
            row,column=cell['row'],cell['column']
            if rows[row][column]==cell['source']:rows[row][column]=cell['reading']
    if not rows or not rows[0] or column_headers is None and any(not str(h).strip() for h in rows[0]):return None,'Table headers need verified column labels.'
    markers=ditto_marks(context,marker)
    headers=header_columns or {}
    first_data_row=1 if column_headers is None or column_headers else 0
    result=[list(rows[0])] if first_data_row else []
    previous=None;previous_row=None
    for index,row in enumerate(rows[first_data_row:],first_data_row):
        if len(row)!=len(rows[0]) and column_headers!=[]:return None,'Table cells do not have reliable header associations.'
        resolved=[]
        for column,value in enumerate(row):
            if (index,column) in excluded:
                resolved.append('')
                continue
            if value.strip() in markers or value.strip()=='〃':
                if value.strip() not in markers or previous is None or column>=len(previous) or not previous[column].strip():return None,'Ditto cell has no source-defined value in its immediately preceding row and column.'
                if headers.get(f'{index}:{column}',column)!=headers.get(f'{previous_row}:{column}',column):
                    return None,'Ditto cell crosses different authored table columns.'
                value=previous[column]
            resolved.append(value)
        if any((index,column) not in excluded for column in range(len(row))):
            previous=resolved;previous_row=index
        result.append([value if value.strip() else 'unspecified' for value in resolved])
    return result,None
