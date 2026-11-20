"""Resolve only source-defined ditto marks in the same table column."""
import re


def rows_for_speech(rows,context):
    if not rows or not rows[0] or any(not str(h).strip() for h in rows[0]):return None,'Table headers need verified column labels.'
    defined=bool(re.search(r'''〃[”"’']?\s+(?:means|denotes)\s+[“"‘']?same as above''',context,re.I))
    result=[list(rows[0])];previous=None
    for row in rows[1:]:
        if len(row)!=len(rows[0]):return None,'Table cells do not have reliable header associations.'
        resolved=[]
        for column,value in enumerate(row):
            if value.strip()=='〃':
                if not defined or previous is None or not previous[column].strip():return None,'Ditto cell has no source-defined value in its immediately preceding row and column.'
                value=previous[column]
            resolved.append(value)
        previous=resolved
        result.append([value if value.strip() else 'unspecified' for value in resolved])
    return result,None
