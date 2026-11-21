"""Occurrence-based chapter selection always projects in original source order."""
import re
from .core import Error


def parse(value):
    if value is None or value=='all':return None
    if isinstance(value,list):
        if not value or any(type(v) is not int or not 1<=v<=4096 for v in value):raise Error('chapter_selection','Select chapter numbers from1to4096, or all.')
        return sorted(set(value))
    selected=set()
    for item in str(value).split(','):
        m=re.fullmatch(r'\s*([1-9]\d*)(?:-([1-9]\d*))?\s*',item)
        if not m:raise Error('chapter_selection','Use chapter numbers/ranges such as5,2-3,2, or all.')
        a,b=int(m[1]),int(m[2] or m[1])
        if not 1<=a<=b<=4096:raise Error('chapter_selection','Chapter ranges must increase and remain within1to4096.')
        selected.update(range(a,b+1))
    return sorted(selected)


def project(decisions,utterances,issues,selection):
    catalog=[];seen=set()
    for d in decisions:
        if d['action'] not in ('speak','unreadable','pause') and not d.get('connector'):continue
        identity=d['chapter_occurrence']
        if identity in seen:continue
        seen.add(identity);catalog.append({'number':len(catalog)+1,'id':identity,'title':d['chapter']})
    if selection and max(selection)>len(catalog):raise Error('chapter_selection','Selected chapter does not exist in this source plan.')
    wanted={r['id'] for r in catalog if selection is None or r['number'] in selection}
    selected_blocks={d['block'] for d in decisions if d['chapter_occurrence'] in wanted}
    for d in decisions:
        if d['block'] not in selected_blocks:
            d.update(action='companion',spoken='',connector=False,reason='Explicitly unselected chapter occurrence retained in the complete source companion.')
            d.pop('normalization',None)
            d.pop('context_events',None)
            d.pop('context_input',None)
    utterances=[u for u in utterances if any(b in selected_blocks for b in u['sources'])]
    for i,u in enumerate(utterances):u['id']=f'u{i+1:05d}'
    for row in catalog:row['selected']=row['id'] in wanted
    return utterances,[i for i in issues if i['block'] in selected_blocks],catalog
