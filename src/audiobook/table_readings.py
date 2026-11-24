"""Resolve only source-defined ditto marks in the same table column."""
import re


def split_cell_fractions(rows, first_data_row=1, *, excluded=()):
    """Locate fraction-like fragments whose source cells must stay separate."""
    return [{'row':index,'columns':[column,column+1],'values':[first,second]}
            for index,row in enumerate(rows[first_data_row:],first_data_row)
            for column,(first,second) in enumerate(zip(row,row[1:]))
            if (index,column) not in excluded and (index,column+1) not in excluded
            and re.fullmatch(r'[0-9]+',first.strip()) and re.fullmatch(r'/\s*[0-9]+',second.strip())]


def semantic_cells(block,note_policy, *, projected=()):
    from .core import Block
    from .epub_semantics import reading

    readings=[]; protected={}; trailing={}; seen=set(); caption=''
    headers=set(block.locator.get('table_header_cells') or ())
    excluded={tuple(pair) for pair in block.locator.get('table_hidden_cells',())}
    entries=[*block.locator.get('table_cell_semantics',()),*projected]
    if entry:=block.locator.get('table_caption'):
        entries.insert(0,{'row':-1,'column':0,**entry})
    for cell in entries:
        row,column=cell['row'],cell['column']
        if (row,column) in excluded:continue
        location={'inline_semantics':cell['spans']}
        source=Block('',cell['source'],location,'body',block.chapter)
        hidden=[s for s in cell['spans'] if s['kind']=='hidden']
        for note in cell['notes']:
            start=note['source_start']
            ref=next((s for s in cell['spans'] if s['kind']=='note_reference' and s['source_start']==start
                      and s['href']==note['reference_href']),None)
            if ref is None or any(h['source_start']<=start and ref['source_end']<=h['source_end'] for h in hidden):continue
            if note['href'] in seen:continue
            seen.add(note['href'])
            if note_policy=='inline' and not (row==0 and column in headers):source.links.append(note)
            else:trailing[note['href']]=note['reading']
        text, spans=reading(source,'inline',with_spans=True)
        leading=len(text)-len(text.lstrip())
        text=text.strip()
        spans=[(max(0,a-leading),min(len(text),b-leading)) for a,b in spans
               if b>leading and a<leading+len(text)]
        if row==-1:caption=text
        else:readings.append({'row':row,'column':column,'source':cell['source'],'reading':text})
        if spans:protected[row,column]=spans
    suffix=(' A note is available in the companion.' if note_policy=='companion' and trailing else
            ''.join(' Note. '+text+' End note.' for text in trailing.values()))
    # A companion-only policy must not expand note text.
    return readings, protected, suffix, caption


def rows_for_speech(rows,context, *, marker=None,cell_readings=(),header_columns=None,column_headers=None,excluded=()):
    if cell_readings:
        rows=[list(row) for row in rows]
        for cell in cell_readings:
            row,column=cell['row'],cell['column']
            if rows[row][column]==cell['source']:rows[row][column]=cell['reading']
    if not rows or not rows[0] or column_headers is None and any(not str(h).strip() for h in rows[0]):return None,'Table headers need verified column labels.'
    defined=bool(re.search(r'''〃[”"’']?\s+(?:means|denotes)\s+[“"‘']?same as above''',context,re.I))
    markers={'〃'} if defined or marker=='ditto' else set()
    if marker=='double_quote':markers.add('"')
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
