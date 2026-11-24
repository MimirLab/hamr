"""Source-backed PDF paragraph structure, folios and line-wrap readings."""
from collections import Counter, OrderedDict
import re
from .core import Error
from .linguistic import safe_unicode


WORD = r"[^\W\d_]+(?:['’][^\W\d_]+)*"
VISUAL_TOKENS = re.compile(r'(?P<wrap>[A-Za-z]+-\n[ \t]*[a-z]+)|(?P<hard>[A-Za-z]+-[a-z]+)|'+WORD)
NATIVE_TOKENS = re.compile(r'(?P<soft>[A-Za-z]+[\u00ad\u00ac]\s*[a-z]+)|(?P<hard>[A-Za-z]+-[a-z]+)|'+WORD)


def line_wrap_readings(text, native_region):
    """Corroborate a wrap against the corresponding native paragraph."""
    if '-\n' not in text:
        return []
    visual=list(VISUAL_TOKENS.finditer(text))
    native=list(NATIVE_TOKENS.finditer(native_region))
    if len(visual)!=len(native):
        return []
    joins=[]
    for printed, encoded in zip(visual,native):
        if printed.lastgroup!='wrap':
            if safe_unicode(printed[0]).replace('’',"'")!=safe_unicode(encoded[0]).replace('’',"'"):
                return []
            continue
        left,right=map(str.strip,printed[0].split('-\n',1))
        kind=encoded.lastgroup
        joined=left+right if kind=='soft' else left+'-'+right
        observed=re.sub(r'[\u00ad\u00ac]\s*','',encoded[0]) if kind=='soft' else encoded[0]
        if kind not in ('soft','hard') or joined!=observed:
            return []
        joins.append({'original':printed[0],'spoken':joined,'source_span':list(printed.span()),
                      'native_region_span':list(encoded.span()),'native':encoded[0],
                      'kind':'discretionary' if kind=='soft' else 'lexical',
                      'evidence':'Matching native text and visual line fragments in the same paragraph.'})
    return joins


def line_wrap_edits(text, joins):
    for join in joins:
        original=join['original']
        if 'source_span' in join:
            a,b=join['source_span']
            if not (0<=a<b<=len(text) and text[a:b]==original):
                continue
        elif text.count(original)==1:
            a=text.index(original)
            b=a+len(original)
        else:
            continue
        yield a,b,join['spoken']


def apply_line_wraps(text, joins):
    """Change only the bound occurrence, including in paragraphs with repeats."""
    for a,b,spoken in sorted(line_wrap_edits(text,joins),reverse=True):
        text=text[:a]+spoken+text[b:]
    return text


def column_order(items, width, rect, *, min_lines=3):
    """Keep whole columns in order when geometry establishes a central gutter."""
    left = [item for item in items if rect(item)[2] <= width * .52]
    right = [item for item in items if rect(item)[0] >= width * .48 and item not in left]
    if len(left) < min_lines or len(right) < min_lines:
        return None
    if min(rect(item)[0] for item in right)-max(rect(item)[2] for item in left) < 12:
        return None
    if min(max(rect(item)[3] for item in side) for side in (left,right)) <= max(
            min(rect(item)[1] for item in side) for side in (left,right)):
        return None
    spanning = [item for item in items if item not in left and item not in right]
    ordered, high = [], float('inf')
    for item in sorted(spanning,key=lambda value: -rect(value)[3]):
        y = rect(item)[3]
        for side in (left,right):
            ordered.extend(sorted((part for part in side if y <= rect(part)[3] < high), key=lambda part: -rect(part)[3]))
        ordered.append(item)
        high = y
    for side in (left,right):
        ordered.extend(sorted((part for part in side if rect(part)[3] < high), key=lambda part: -rect(part)[3]))
    return ordered


def adjacent_native_lines(left, right):
    """Use geometry for reading flow between two native body lines."""
    a, b = left.locator, right.locator
    if (left.role != 'body' or right.role != 'body' or left.issue or right.issue
            or left.chapter != right.chapter or a.get('chapter_id') != b.get('chapter_id')
            or a.get('format') != 'pdf' or b.get('format') != 'pdf'
            or not a.get('page') or a['page'] != b.get('page')
            or 'line' not in a or b.get('line') != a['line'] + 1
            or not a.get('rect') or not b.get('rect')):
        return False
    ax, ay, _, atop = a['rect']
    bx, by, _, btop = b['rect']
    height = max(atop - ay, btop - by)
    advance = (atop + ay - btop - by) / 2
    a_tag, b_tag = a.get('tagged_paragraph'), b.get('tagged_paragraph')
    if a_tag != b_tag:
        return False
    max_indent = 2 if a_tag is not None else .24
    return abs(ax - bx) <= max_indent * height and .85 * height <= advance <= 1.75 * height


def paragraphs(words, page, height, page_issue=None, *, region=None, width=None, scale=2, format='pdf'):
    origin_left, origin_top = (region[0], region[3]) if region else (0, height)
    groups = OrderedDict()
    for word in words:
        groups.setdefault(tuple(word[1:4]), []).append(word)

    def rectangle(group):
        left = min(int(x[6]) for x in group) / scale
        top = min(int(x[7]) for x in group) / scale
        right = max(int(x[6]) + int(x[8]) for x in group) / scale
        bottom = max(int(x[7]) + int(x[9]) for x in group) / scale
        return [origin_left+left, origin_top-bottom, origin_left+right, origin_top-top]

    layout = None
    if width:
        line_parts = []
        for identity, group in groups.items():
            lines = OrderedDict()
            for word in group:
                lines.setdefault(word[4], []).append(word)
            for line in lines.values():
                left, right = [], []
                for word in line:
                    coordinate = origin_left + int(word[6]) / scale
                    (left if coordinate < width / 2 else right).append(word)
                if left and right and rectangle(right)[0] - rectangle(left)[2] >= 18:
                    line_parts.extend((identity, side, rectangle(side)) for side in (left, right))
                else:
                    line_parts.append((identity, line, rectangle(line)))
        if ordered := column_order(line_parts, width, lambda part: part[2]):
            groups = OrderedDict()
            previous = None
            for identity, line, rect in ordered:
                side = 'left' if rect[2] <= width * .52 else 'right' if rect[0] >= width * .48 else 'spanning'
                key = (identity, side)
                if key != previous:
                    groups[len(groups)] = []
                groups[len(groups)-1].extend(line)
                previous = key
            layout = 'geometric_two_columns'

    result = []
    for index, group in enumerate(groups.values()):
        lines = OrderedDict()
        for x in group:
            lines.setdefault(x[4], []).append(x)
        text = '\n'.join(' '.join(x[11] for x in line) for line in lines.values())
        confidence = sum(float(x[10]) for x in group) / len(group)
        issue = None
        if confidence < 65:
            issue = 'Visible paragraph text could not be read reliably.'
        if any(float(x[10]) < 70 and re.search(r'\d', x[11]) for x in group):
            issue = 'Low-confidence numeric OCR requires verification.'
        if page_issue and page_issue.startswith('Native and visual'):
            issue = page_issue
        locator = {'format': format, 'page': page, 'rect': rectangle(group),
                   'paragraph': index, 'ocr_confidence': confidence}
        if layout:
            locator['ocr_layout'] = layout
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


def spoken_paragraph(block, *, layout=None):
    text = block.source
    if layout is None:
        layout = block.locator.get('format') in ('pdf','scan')
    if layout:
        if block.role=='heading' and block.locator.get('ruled_panel') and text[-1:] not in '.!?':
            return text+'.'
        equation = block.locator.get('pdf_equation')
        if equation and equation['fields']['notation']==text:
            return equation['reading']
        inline = block.locator.get('pdf_inline_formula')
        if inline and inline['source']==text:
            return inline['reading']
        return apply_line_wraps(text,block.locator.get('line_wrap_joins',[]))
    return text


def structure_policy(doc, document_policy,by_source=False, *, paginated_source=None):
    structure = document_policy.get('structure')
    if not structure:
        return None, {}
    if paginated_source is None:
        paginated_source = doc.format in ('pdf','scan')
    if not paginated_source or not isinstance(structure, dict):
        raise Error('document_structure', 'Verified page structure requires a paginated source.')
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
