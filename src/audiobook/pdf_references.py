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


BIBLIOGRAPHY = re.compile(r'(?i:bibliography|references|literature cited|works cited)')
FOLLOWING_SECTION = re.compile(r'(?i:appendix(?: [A-Z0-9]+)?|index|glossary|about the author|photo credits|notes|'
                               r'methods|results|conclusions?|acknowledgments?|chapter [0-9IVXLCDM]+(?:[ .:–—-].*)?)')
CITATION_START = re.compile(r"^(?:[\-–—]+[.,]?|[.]|[^\W\d_][^;:!?]{0,89})[ \t]+(?:1[6-9]\d{2}|20\d{2})[a-z]?\.", re.I)


def citation_page(text):
    """Require repeated author-year entries occupying a substantial part of a page."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().isdigit()]
    heading = bool(lines and BIBLIOGRAPHY.fullmatch(lines[0]))
    if heading:
        lines = lines[1:]
    starts = [i for i,line in enumerate(lines) if CITATION_START.match(line)]
    # The first entry can be preceded by a short continuation from the prior page.
    # A section heading or other prose introducing the list breaks that evidence.
    if (len(starts) < 2 or len(lines) < 3 or starts[0] > 2 or len(starts)*5 < len(lines)
            or len(lines)-starts[-1]>6 or any(FOLLOWING_SECTION.fullmatch(line) for line in lines)):
        return False, False
    if starts[0] and (heading or any(
            re.fullmatch(r'[\w -]{3,55}',line) and line[:1].isupper() for line in lines[:starts[0]])):
        return False, False
    return True, heading


def mark_bibliographies(blocks, inventory):
    """Bind an explicit bibliography heading and consecutive citation-shaped pages."""
    by_page = defaultdict(list)
    for block in blocks:
        by_page[block.locator.get('page')].append(block)
    anchor = previous = None
    for entry in sorted(inventory,key=lambda page:page['page']):
        page = entry['page']
        if entry.get('route')!='visual_ocr' or entry.get('rotation')!=0 or not entry.get('native_geometry_qualified'):
            anchor = previous = None
            continue
        native,visual = (citation_page(entry.get(field,'')) for field in ('native_observation','ocr_observation'))
        if not (native[0] and visual[0]):
            anchor = previous = None
            continue
        substantive = [block for block in by_page[page] if block.role in ('body','heading') and block.source.strip()]
        if native[1] and visual[1] and substantive and BIBLIOGRAPHY.fullmatch(substantive[0].source.strip()):
            anchor = page
        elif anchor is None or previous!=page-1 or native[1] or visual[1]:
            anchor = previous = None
            continue
        evidence = {'kind':'printed_bibliography','scope':'page','heading_pages':[anchor],
                    'basis':'Corroborated bibliography heading followed by dense author-year references in native and visual text.'}
        entry['reference_classification'] = evidence
        for block in substantive:
            block.role = 'reference'
            block.locator['reference_classification'] = evidence
            block.evidence.append(evidence['basis'])
        previous = page
