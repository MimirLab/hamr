"""Identify bounded printed index pages from competing observations and layout.

An index heading alone never starts an unbounded reference section. An interior
continuation needs qualifying index pages on both sides, at most two pages
apart. Body text after the last positively identified page keeps its own role.
"""
from collections import defaultdict
import re


def dense_entries(text,page_count):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if len(lines)<20 or any(len(line.split())>18 for line in lines):return False
    entries=sum(bool(re.search(r',\s*(?:\d{1,3}|[ivxlcdm]+)(?:[-–—]\d{1,3})?\s*$',line,re.I)) for line in lines)
    values=[int(value) for value in re.findall(r'\b\d+\b',text)]
    return entries/len(lines)>=.55 and bool(values) and sum(0<value<=page_count for value in values)/len(values)>=.9


def mark_indexes(blocks,inventory):
    by_page=defaultdict(list)
    for b in blocks:by_page[b.locator.get('page')].append(b)
    candidates={};anchors=set()
    for entry in inventory:
        if entry.get('route')!='visual_ocr' or entry.get('rotation')!=0:continue
        page=entry['page'];width,height=entry['size'];native=entry.get('native_observation','');visual=entry.get('ocr_observation','')
        if not all(dense_entries(text,len(inventory)) for text in (native,visual)):continue
        words=[word for word in entry.get('ocr_words',[]) if height*.12<(word['rect_pixels'][1]+word['rect_pixels'][3]/2)/2<height*.9]
        if not words:continue
        centers=[(word['rect_pixels'][0]+word['rect_pixels'][2]/2)/2 for word in words]
        if min(sum(x<width*.48 for x in centers),sum(x>width*.52 for x in centers))<len(centers)*.2:continue
        candidates[page]=entry
        headings=[]
        for b in by_page[page]:
            rect=b.locator.get('rect',[0,0,0,0]);label=b.source.strip()
            running=rect[1]>height*.85 and bool(re.fullmatch(r'(?:\d+\s+)?INDEX(?:\s+\d+)?',label))
            opening=label=='INDEX' and b is by_page[page][0] and rect[1]>height*.5 and all(other.locator.get('rect',[0,0,0,0])[3]<rect[1] for other in by_page[page][1:])
            if running or opening:headings.append(b)
        native_heading=any(re.fullmatch(r'(?:\d+\s+)?INDEX(?:\s+\d+)?',line.strip()) for line in native.splitlines()[:3])
        if headings and native_heading:anchors.add(page)
    selected=set(anchors)
    for page in candidates:
        if page-1 in anchors and page+1 in anchors:selected.add(page)
    for page in sorted(selected):
        evidence={'kind':'printed_index','scope':'page','heading_pages':[page] if page in anchors else [page-1,page+1],
                  'basis':'Native and visual dense page-reference entries, bilateral column geometry, and corroborated upper-margin INDEX heading or bounded continuation.'}
        candidates[page]['reference_classification']=evidence
        for b in by_page[page]:
            if b.role not in ('body','heading'):continue
            b.role='reference';b.locator['reference_classification']=evidence
            b.evidence.append(evidence['basis'])
