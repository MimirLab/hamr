"""Source-backed PDF paragraph structure, folios and line-wrap readings."""
from collections import Counter, OrderedDict
import re
from .core import Error


def paragraphs(words, native, page, width, height, page_issue=None):
    groups = OrderedDict()
    for word in words:
        groups.setdefault(tuple(word[1:4]), []).append(word)
    soft_words = {a.lower() + b.lower() for a, b in re.findall(r'([A-Za-z]+)[¬\u00ad]\s*([A-Za-z]+)', native)}
    result = []
    for index, group in enumerate(groups.values()):
        lines = OrderedDict()
        for x in group:
            lines.setdefault(x[4], []).append(x)
        text = '\n'.join(' '.join(x[11] for x in line) for line in lines.values())
        left = min(int(x[6]) for x in group) / 2
        top = min(int(x[7]) for x in group) / 2
        right = max(int(x[6]) + int(x[8]) for x in group) / 2
        bottom = max(int(x[7]) + int(x[9]) for x in group) / 2
        confidence = sum(float(x[10]) for x in group) / len(group)
        issue = None
        if confidence < 65:
            issue = 'Visible paragraph text could not be read reliably.'
        if any(float(x[10]) < 70 and re.search(r'\d', x[11]) for x in group):
            issue = 'Low-confidence numeric OCR requires verification.'
        if page_issue and page_issue.startswith('Native and visual'):
            issue = page_issue
        joins = []
        for match in re.finditer(r'([A-Za-z]+)-\n([a-z]+)', text):
            joined = match[1] + match[2]
            if joined.lower() in soft_words:
                joins.append({'original': match[0], 'spoken': joined,
                              'evidence': 'Visual line break matches native soft-hyphen word.'})
        locator = {'format': 'pdf', 'page': page, 'rect': [left, height-bottom, right, height-top],
                   'paragraph': index, 'ocr_confidence': confidence}
        if joins:
            locator['line_wrap_joins'] = joins
        result.append({'text': text, 'locator': locator, 'issue': issue})
    return result


def mark_folios(blocks, inventory):
    heights = {x['page']: x['size'][1] for x in inventory}
    candidates = []
    for b in blocks:
        loc = b.locator
        if loc.get('format') != 'pdf' or 'paragraph' not in loc or not b.source.strip().isdigit():
            continue
        page = loc['page'];rect = loc['rect'];height = heights[page]
        if rect[3] < height * .1:
            candidates.append((b, int(b.source.strip()) - page))
    offsets = Counter(offset for _, offset in candidates)
    for b, offset in candidates:
        if offsets[offset] >= 5:
            b.role = 'furniture'
            b.issue = None
            b.evidence.append('Isolated bottom-margin folio follows the same page-number offset on at least five pages.')


def spoken_paragraph(block):
    text = block.source
    if block.locator.get('format') == 'pdf':
        for join in block.locator.get('line_wrap_joins', []):
            text = text.replace(join['original'], join['spoken'])
    return text


def structure_policy(doc, document_policy,by_source=False):
    structure = document_policy.get('structure')
    if not structure:
        return None, {}
    if doc.format != 'pdf' or not isinstance(structure, dict):
        raise Error('document_structure', 'Verified page structure is supported only for PDF.')
    by_id = {b.id: b for b in doc.blocks}
    def verified(item):
        return isinstance(item, dict) and item.get('verified') is True and isinstance(item.get('reason'), str) and bool(item['reason'].strip())
    def canonical(text):
        return ''.join(c.lower() for c in text if c.isalnum())
    title = None
    metadata = structure.get('title')
    if metadata:
        if not verified(metadata) or not isinstance(metadata.get('text'), str) or not metadata.get('source_blocks'):
            raise Error('document_structure', 'PDF title needs verified source blocks and a reason.')
        if any(b not in by_id for b in metadata['source_blocks']):
            raise Error('document_structure', 'PDF title names an unknown source block.')
        source = ' '.join(by_id[b].source for b in metadata['source_blocks'])
        title = metadata['text']
        if not canonical(title) or canonical(title) not in canonical(source):
            raise Error('document_structure', 'PDF title does not match its cited source text.')
    chapters = {}
    last = -1 if by_source else 0
    for item in structure.get('chapters', []):
        if not verified(item) or not isinstance(item.get('title'), str) or item.get('source_block') not in by_id:
            raise Error('document_structure', 'Each PDF chapter needs a verified heading block and reason.')
        page = item.get('start_page');heading = by_id[item['source_block']]
        position=next(i for i,b in enumerate(doc.blocks) if b.id==heading.id) if by_source else page
        if type(page) is not int or position <= last or heading.locator.get('page') != page:
            raise Error('document_structure', 'PDF chapters must cite increasing heading pages.')
        if not canonical(item['title']) or canonical(item['title']) not in canonical(heading.source):
            raise Error('document_structure', 'PDF chapter title does not match its heading source.')
        chapters[position] = item['title'];last = position
    return title, chapters
