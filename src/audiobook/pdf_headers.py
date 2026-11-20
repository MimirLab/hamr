"""Repeated margin labels need folio, position and document recurrence."""
from collections import defaultdict
import re


def margin_label(words):
    letters = ''.join(c for c in ' '.join(words) if c.isalpha())
    return re.sub(r'[^A-Z]', '', letters) if len(letters) >= 6 and letters.isupper() else None


def decimal_folio(token):
    return int(token) if re.fullmatch(r'[1-9][0-9]{0,3}', token) else None


def same_line(left, right):
    return ((left[2] < right[0] or right[2] < left[0])
            and min(left[3], right[3]) - max(left[1], right[1])
            >= min(left[3] - left[1], right[3] - right[1]) / 2)


def sequence_headers(margins):
    """Identify top-margin folios and labels separated by OCR segmentation."""
    offsets = defaultdict(set)
    labels = defaultdict(set)
    observations = []
    for page, blocks in margins.items():
        folios = [(block, number) for block in blocks if (number := decimal_folio(block.source.strip())) is not None]
        for block in blocks:
            words = block.source.split()
            if not words:
                continue
            number = decimal_folio(words[0]) or decimal_folio(words[-1])
            if len(words) > 1 and number is not None:
                label_words = words[1:] if decimal_folio(words[0]) else words[:-1]
                label = margin_label(label_words)
                neighbors = [block]
            else:
                label = margin_label(words)
                if not label or any(c.isdigit() for c in block.source):
                    continue
                peers = [(peer, n) for peer, n in folios if same_line(block.locator['rect'], peer.locator['rect'])]
                if len(peers) != 1:
                    continue
                peer, number = peers[0]
                neighbors = [block, peer]
            if label:
                offset = page - number
                offsets[offset].add(page)
                labels[offset, label].add(page)
                observations.append((offset, label, neighbors))
    return [(block, label, len(labels[offset, label])) for offset, label, blocks in observations
            if len(offsets[offset]) >= 5 and len(labels[offset, label]) >= 2 for block in blocks]


def mark_headers(blocks,inventory):
    pages={item['page']:item for item in inventory};candidates=[];corroborated=[];margins=defaultdict(list)
    def folio(token):return bool(re.fullmatch(r'\d+|[ivxlcdmIVXLCDM]{1,8}',token))
    def label(text,allow_corrupt=False):
        words=text.split();has_folio=False
        def edge(token):return folio(token) or allow_corrupt and bool(re.fullmatch(r'[0-9nNlI1Tt|]{1,6}',token))
        if words and edge(words[0]):words=words[1:];has_folio=True
        if words and edge(words[-1]):words=words[:-1];has_folio=True
        return margin_label(words) if has_folio else None
    for b in blocks:
        loc=b.locator;page=loc.get('page');rect=loc.get('rect')
        if b.role not in ('body','furniture') or page not in pages or not rect:continue
        height=pages[page]['size'][1]
        if pages[page].get('rotation',0)!=0:continue
        if rect[1]<height*.9 or rect[3]-rect[1]>height*.05:continue
        if rect[1]>=height*.92 and rect[3]-rect[1]<=height*.025:
            margins[page].append(b)
        identity=label(b.source)
        if identity:candidates.append((b,identity));continue
        # A corrupted visual folio must not split otherwise continued prose.
        # Require the same label in an independently localized native region
        # with a real folio, plus recurrence supported by ordinary OCR folios.
        loc_evidence=pages[page].get('critical_localization',{})
        if pages[page].get('route')!='visual_ocr' or loc_evidence.get('scope')!='paragraph' or not all(loc_evidence.get(k) for k in ('qualified_geometry','native_critical_coverage','visual_critical_coverage')):continue
        regions=[r for r in loc_evidence.get('regions',[]) if r.get('paragraph')==loc.get('paragraph') and len(r.get('rect',[]))==4 and all(abs(a-c)<.01 for a,c in zip(r['rect'],rect))]
        if len(regions)!=1:continue
        identity=label(b.source,True);native=label(regions[0].get('native_observation',''))
        if identity and identity==native:corroborated.append((b,identity))
    support=defaultdict(set)
    for b,identity in candidates:support[identity].add(b.locator['page'])
    candidates+=[(b,identity) for b,identity in corroborated if len(support[identity])>=5]
    new_ids={b.id for b,_ in corroborated}
    for b,identity in candidates:
        if len(support[identity])<5:continue
        b.role='furniture';b.locator['running_header']={'label':identity,'occurrences':len(support[identity])}
        if b.id in new_ids:b.locator['running_header']['native_folio_corroborated']=True
        evidence='Upper-margin uppercase label with printed folio recurs on at least five pages. Original uncertainty remains in the source companion.'
        if evidence not in b.evidence:b.evidence.append(evidence)
    for b,identity,occurrences in sequence_headers(margins):
        if 'running_header' in b.locator:continue
        b.role='furniture'
        b.locator['running_header']={'label':identity,'occurrences':occurrences,'sequence_folio':True}
        b.evidence.append('Recurring upper-margin label aligns with a folio in a verified page-number sequence. The source companion retains both.')
