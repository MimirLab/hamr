"""Resolve source-bound structural numbers before sentence planning."""
from collections import defaultdict
from dataclasses import dataclass
import re

from .spoken_forms import roman
from .typed_readings import decimal


ROMAN_HEADING = re.compile(r'^([IVXLCDM]{1,8})[.)][ \t]+(?=\S)')
PARAGRAPH = re.compile(r'(?:(?<=\n)|(?<=\r)|\A)[ \t]*([1-9][0-9]{0,2}(?:,[ \t]*[1-9][0-9]{0,2})?)[.)][ \t]+(?=\S)')
OBSTRUCTIONS = frozenset(('literal','hidden','note_reference','ruby','mathml','formula','generated_label'))


@dataclass(frozen=True)
class Label:
    start: int
    end: int
    values: tuple[int,...]
    evidence: str

    @property
    def speaking(self):
        values = ' and '.join(decimal(str(value)) for value in self.values)
        if self.evidence=='successive Roman headings':
            return values.capitalize()+', '
        return ('Number ' if len(self.values)==1 else 'Numbers ')+values+', '

    def span(self,source):
        return {'kind':'typed_reading','source_start':self.start,'source_end':self.end,
                'source':source[self.start:self.end],'reading':self.speaking,
                'ordinal_values':list(self.values),'origin':self.evidence}


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
        if (block.role=='body' and type(declared) is int and clear(block,0,0)
                and not block.locator.get('checklist')):
            finish_run()
            first = next(paragraph_labels(block),None)
            end = first.end if first and first.start==0 and first.values==(declared,) else 0
            labels[block.id].append(Label(0,end,(declared,),'authored ordered list'))
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
    """Return the authored decimal ordinal values of HTML list items."""
    def integer(value,default):
        value = str(value).strip()
        return int(value) if SIGNED_INTEGER.fullmatch(value) else default

    result = {}
    for node in root.find_all('ol'):
        if node.get('type','1')!='1' or 'list-style' in node.get('style','').lower():
            continue
        items = node.find_all('li',recursive=False)
        reverse = node.has_attr('reversed')
        value = integer(node.get('start'),len(items) if reverse else 1)
        for item in items:
            value = integer(item.get('value'),value)
            if item.get('type','1')=='1' and 'list-style' not in item.get('style','').lower():
                result[id(item)] = value
            value += -1 if reverse else 1
    return result


def bind_ordered(block,node,ordinals):
    if not ordinals or not block or block.role!='body' or not block.source.strip():
        return
    item = node if node.name=='li' else node.find_parent('li')
    if item is not None and (value:=ordinals.pop(id(item),None)) is not None:
        block.locator['ordered_item'] = value
