"""Locate internally skipped printed folios without inventing missing text.

Only corroborated margin numbers with stable neighbouring sequences qualify.
The result is a source-review lead, not proof of an edition's completeness.
"""
import re


def gaps(blocks,inventory):
    pages={i['page']:i for i in inventory};observed={}
    for b in blocks:
        page=b.locator.get('page');rect=b.locator.get('rect');text=b.source.strip()
        if page not in pages or pages[page].get('route')!='visual_ocr' or not rect or not re.fullmatch(r'\d{1,4}',text):continue
        height=pages[page]['size'][1]
        if rect[3]>=height*.1:continue
        native=pages[page].get('native_observation','')
        final=re.search(r'(?<!\d)(\d{1,4})\s*$',native)
        if not final or final[1]!=text:continue
        observed.setdefault(page,[]).append(int(text))
    values={page:entries[0] for page,entries in observed.items() if len(entries)==1};result=[]
    for page in sorted(values):
        if page+1 not in values or values[page+1]<=values[page]+1:continue
        # Missing recognition of one folio does not erase sequence support:
        # require three corroborated observations with the same local offset.
        before=[p for p in range(max(1,page-4),page+1) if p in values]
        after=[p for p in range(page+1,page+6) if p in values]
        if sum(values[p]-p==values[page]-page for p in before)<3:continue
        if sum(values[p]-p==values[page+1]-(page+1) for p in after)<3:continue
        result.append({'prior_PDF_page':page,'page':page+1,'prior_printed_folio':values[page],
                       'next_printed_folio':values[page+1],'unavailable_printed_pages':[values[page]+1,values[page+1]-1],
                       'reason':'Corroborated printed page numbering skips a range between adjacent PDF pages. Verify source completeness or provide the missing material; no text is reconstructed.'})
    return result
