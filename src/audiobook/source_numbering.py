"""Resolve source-bound structural numbers before sentence planning."""
from collections import defaultdict
from dataclasses import dataclass
import re

from .spoken_forms import roman
from .typed_readings import decimal


ROMAN_HEADING = re.compile(r'^([IVXLCDM]{1,8})[.)][ \t]+(?=\S)')
PARAGRAPH = re.compile(r'(?:(?<=\n)|(?<=\r)|\A)[ \t]*([1-9][0-9]{0,2}(?:,[ \t]*[1-9][0-9]{0,2})?)[.)][ \t]+(?=\S)')
OBSTRUCTIONS = frozenset(('literal','hidden','note_reference','ruby','mathml','formula','generated_label'))
ROMAN_DIGITS = ((1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),
                (40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I'))
HTML_STYLE = {'1':'decimal','i':'lowerRoman','I':'upperRoman','a':'lowerLetter','A':'upperLetter'}
CSS_STYLE = {'decimal':'decimal','lower-roman':'lowerRoman','upper-roman':'upperRoman',
             'lower-alpha':'lowerLetter','upper-alpha':'upperLetter',
             'lower-latin':'lowerLetter','upper-latin':'upperLetter'}
UNNUMBERED = frozenset(('none','disc','circle','square','disclosure-open','disclosure-closed'))


def marker_component(value, form, *, word_letters=False):
    """Return the printed form and spoken value of one authored counter."""
    if form=='decimal':
        return str(value),decimal(str(value))
    if form in ('upperRoman','lowerRoman') and 0<value<4000:
        printed = []
        remaining = value
        for magnitude,symbol in ROMAN_DIGITS:
            copies,remaining = divmod(remaining,magnitude)
            printed.append(symbol*copies)
        label = ''.join(printed)
        return label if form=='upperRoman' else label.lower(),decimal(str(value))
    if form in ('upperLetter','lowerLetter') and 0<value<=10000:
        if word_letters:
            group,digit = divmod(value-1,26)
            label = chr(65+digit)*(group+1)
        else:
            label = ''
            while value:
                value,digit = divmod(value-1,26)
                label = chr(65+digit)+label
        if len(label)<=6:
            return label if form=='upperLetter' else label.lower(),' '.join(label)
    return None


@dataclass(frozen=True)
class Label:
    start: int
    end: int
    values: tuple[int,...]
    evidence: str
    marker: dict | None = None

    @property
    def speaking(self):
        values = ' and '.join(decimal(str(value)) for value in self.values)
        if self.evidence=='successive Roman headings':
            return values.capitalize()+', '
        if self.marker:
            return self.marker['reading']
        return ('Number ' if len(self.values)==1 else 'Numbers ')+values+', '

    def span(self,source):
        return {'kind':'typed_reading','source_start':self.start,'source_end':self.end,
                'source':source[self.start:self.end],'reading':self.speaking,
                'ordinal_values':list(self.values),'origin':self.evidence,
                **({'authored_marker':self.marker['notation'],
                    'marker_location':self.marker['location']} if self.marker else {})}


def clear(block,start,end):
    return not block.issue and not any(
        span['kind'] in OBSTRUCTIONS and (span['source_start']<end and start<span['source_end']
                                         or start==end==span['source_start'])
        for span in block.locator.get('inline_semantics',()))


def paragraph_labels(block):
    for hit in PARAGRAPH.finditer(block.source):
        values = tuple(int(part) for part in hit[1].split(','))
        if (len(values)==1 or values[0]+1==values[1]) and clear(block,*hit.span()):
            yield Label(*hit.span(),values,'successive numbered paragraphs')


def ordered_label(block, value, marker=None):
    first = next(paragraph_labels(block),None)
    end = first.end if first and first.start==0 and first.values==(value,) else 0
    if marker:
        match = re.match(re.escape(marker['notation'])+r'[ \t]+(?=\S)',block.source)
        if match and clear(block,*match.span()):
            end = match.end()
    return Label(0,end,tuple(marker['values']) if marker else (value,),'authored ordered list',marker)


def identity(block):
    loc = block.locator
    return (block.chapter,loc.get('chapter_id'),loc.get('member'),loc.get('occurrence'))


def collect(document):
    labels = defaultdict(list)
    run = []
    previous = None
    headings = []

    def finish_run():
        if len(run)>=3:
            for block,label in run:
                labels[block.id].append(label)
        run.clear()

    def finish_headings():
        if len(headings)>=2:
            for block,label in headings:
                labels[block.id].append(label)
        headings.clear()

    for block in document.blocks:
        if block.role=='heading':
            hit = ROMAN_HEADING.match(block.source)
            value = roman(hit[1]) if hit and clear(block,*hit.span()) else None
            if not value or headings and value!=headings[-1][1].values[-1]+1:
                finish_headings()
            if value:
                headings.append((block,Label(*hit.span(),(value,),'successive Roman headings')))
        if block.role in ('furniture','navigation','hidden'):
            continue
        chapter = identity(block)
        if chapter!=previous:
            finish_run()
            previous = chapter
        declared = block.locator.get('ordered_item')
        if (block.role in ('body','heading') and type(declared) is int and clear(block,0,0)
                and not block.locator.get('checklist')):
            finish_run()
            labels[block.id].append(ordered_label(block,declared,block.locator.get('ordered_marker')))
        elif (block.role=='body' and not block.locator.get('authored_line_breaks')
              and not block.locator.get('block_quote')):
            candidates = list(paragraph_labels(block))
            if not candidates or candidates[0].start!=0:
                finish_run()
            for label in candidates:
                if run and label.values[0]!=run[-1][1].values[-1]+1:
                    finish_run()
                run.append((block,label))
        else:
            finish_run()
    finish_run()
    finish_headings()
    return labels


def split_numbered(text, labels):
    """Retain each confirmed numbered line as its own narrated paragraph."""
    prefixes = tuple(label.speaking for label in labels if label.start)
    breaks = [match.end() for match in re.finditer(r'\n[ \t]*',text)
              if text.startswith(prefixes,match.end())]
    starts = [0,*breaks]
    return [(start,end) for start,end in zip(starts,[*breaks,len(text)])]


SIGNED_INTEGER = re.compile(r'[+-]?[0-9]{1,9}')


def html_ordered_items(root):
    """Return resolved HTML list counters and their authored marker forms."""
    def integer(value,default):
        value = str(value).strip()
        return int(value) if SIGNED_INTEGER.fullmatch(value) else default

    def form(node, inherited):
        chosen = HTML_STYLE.get(node.get('type'),inherited)
        for declaration in node.get('style','').split(';'):
            field,sep,raw = declaration.partition(':')
            if sep and field.strip().lower() in ('list-style','list-style-type'):
                fields = [part for part in raw.strip().lower().split() if part not in ('inside','outside','!important')]
                if len(fields)==1:
                    selected, = fields
                    chosen = CSS_STYLE.get(selected,selected)
                elif fields:
                    chosen = 'unresolved'
        return chosen

    result = {}
    for node in root.find_all('ol'):
        items = node.find_all('li',recursive=False)
        reverse = node.has_attr('reversed')
        value = integer(node.get('start'),len(items) if reverse else 1)
        inherited = form(node,'decimal')
        for item in items:
            value = integer(item.get('value'),value)
            style = form(item,inherited)
            if style not in UNNUMBERED:
                component = marker_component(value,style)
                if not component and style in HTML_STYLE.values():
                    component = marker_component(value,'decimal')
                if component:
                    notation,reading = component
                    label = 'Letter' if style.endswith('Letter') and not notation.lstrip('-').isdigit() else 'Number'
                    result[id(item)] = (value,{'notation':notation+'.','reading':f'{label} {reading}, ',
                                               'values':[value],'location':{'element':'HTML ordered-list item','form':style}})
                else:
                    result[id(item)] = (value,{'issue':'This HTML ordered list uses a marker that needs a verified reading.',
                                               'form':style})
            value += -1 if reverse else 1
    return result


def bind_ordered(block,node,ordinals):
    if not ordinals or not block or block.role!='body' or not block.source.strip():
        return
    item = node if node.name=='li' else node.find_parent('li')
    if item is not None and (declared:=ordinals.pop(id(item),None)) is not None:
        value,marker = declared
        block.locator['ordered_item'] = value
        if issue:=marker.get('issue'):
            block.locator['ordered_marker'] = marker
            block.issue = block.issue or issue
        elif marker['location']['form']!='decimal':
            block.locator['ordered_marker'] = marker
