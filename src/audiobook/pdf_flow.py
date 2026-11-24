"""Detect layout insertions which separate a continued sentence in printed pages.

This is a review locator, not an automatic caption classifier or word guesser.
"""
import re


def overlaps(blocks,inventory):
    """Hold a small body region nested in a larger OCR paragraph.

    This is ambiguous affiliation, not permission to discard duplicate text.
    """
    pages={i['page']:i for i in inventory};grouped={};result=[]
    for b in blocks:
        page=b.locator.get('page');rect=b.locator.get('rect')
        if b.role!='body' or page not in pages or not rect or pages[page].get('route')!='visual_ocr' or not pages[page].get('critical_localization',{}).get('qualified_geometry'):continue
        grouped.setdefault(page,[]).append(b)
    for page,items in grouped.items():
        for small in items:
            if not 1<=len(small.source.split())<=12:continue
            a=small.locator['rect'];area=max(0,a[2]-a[0])*max(0,a[3]-a[1])
            if not area:continue
            for large in items:
                if small.id==large.id or len(large.source.split())<20:continue
                b=large.locator['rect'];intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
                if intersection/area>=.9:
                    result.append({'page':page,'block':small.id,'source_blocks':[large.id,small.id],'reason':'A small OCR body region overlaps a larger paragraph. Verify word and reading-order affiliation before narration; neither region is silently discarded.'})
                    break
    return result


def insertions(blocks,inventory,expanded=False,margin_numbers=False,*,page_label='PDF pages'):
    heights={i['page']:i['size'][1] for i in inventory}
    sizes={i['page']:i['size'] for i in inventory}
    # A damaged folio can hide a caption from the three-region locator.
    # Look through it only to locate review, never to classify/delete it.
    margins=set()
    if margin_numbers:
        for b in blocks:
            rect=b.locator.get('rect');size=sizes.get(b.locator.get('page'))
            if b.role=='body' and rect and len(rect)==4 and size and re.fullmatch(r'\d{1,4}[.)\]]',b.source.strip()) and 0<=rect[1]<=rect[3]<=size[1]*.08 and 0<rect[2]-rect[0]<=size[0]*.12 and 0<rect[3]-rect[1]<=size[1]*.03:
                margins.add(b.id)
    body=[b for b in blocks if b.role in ('body','heading') and not re.fullmatch(r'\s*\d+\s*',b.source)]
    body=[b for b in body if b.id not in margins]
    if expanded:body=[b for b in body if not re.fullmatch(r'''[\s.,…—–\-"'“”‘’!?;:*]+''',b.source)]
    result=[];positions={b.id:i for i,b in enumerate(blocks)}
    for previous,current,following in zip(body,body[1:],body[2:]):
        p,q,r=[b.locator.get('page') for b in (previous,current,following)]
        rect=current.locator.get('rect',[]);text=current.source.strip()
        if None in (p,q,r) or len(rect)!=4 or q not in heights:continue
        if p<=q<=r and r-p==1 and re.search(r'[A-Za-z-]$',previous.source.strip()) and re.match(r'^[a-z]',following.source.strip()) and re.search(r'[.!?]$',text) and len(text.split())<=(60 if expanded else 35) and rect[3]-rect[1]<heights[q]*(.08 if expanded else .05):
            ambiguous=[b.id for b in blocks if b.id in margins and positions[previous.id]<positions[b.id]<positions[following.id]]
            result.append({'block':current.id,'source_blocks':[previous.id,current.id,following.id]+ambiguous,
                           'reason':f'A short isolated source region interrupts a sentence continued across {page_label}. Verify its affiliation and reading order before narration.'})
    return result
