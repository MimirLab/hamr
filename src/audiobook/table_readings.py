"""Resolve only source-defined ditto marks in the same table column."""
import re


def split_cell_fractions(rows):
    """Locate fraction-like fragments whose source cells must stay separate."""
    return [{'row':index,'columns':[column,column+1],'values':[first,second]}
            for index,row in enumerate(rows[1:],1)
            for column,(first,second) in enumerate(zip(row,row[1:]))
            if re.fullmatch(r'[0-9]+',first.strip()) and re.fullmatch(r'/\s*[0-9]+',second.strip())]


def rows_for_speech(rows,context, *, marker=None):
    if not rows or not rows[0] or any(not str(h).strip() for h in rows[0]):return None,'Table headers need verified column labels.'
    defined=bool(re.search(r'''〃[”"’']?\s+(?:means|denotes)\s+[“"‘']?same as above''',context,re.I))
    markers={'〃'} if defined or marker=='ditto' else set()
    if marker=='double_quote':markers.add('"')
    result=[list(rows[0])];previous=None
    for row in rows[1:]:
        if len(row)!=len(rows[0]):return None,'Table cells do not have reliable header associations.'
        resolved=[]
        for column,value in enumerate(row):
            if value.strip() in markers or value.strip()=='〃':
                if value.strip() not in markers or previous is None or not previous[column].strip():return None,'Ditto cell has no source-defined value in its immediately preceding row and column.'
                value=previous[column]
            resolved.append(value)
        previous=resolved
        result.append([value if value.strip() else 'unspecified' for value in resolved])
    return result,None
