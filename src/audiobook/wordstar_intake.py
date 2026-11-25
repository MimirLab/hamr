"""Read WordStar 7 publication text and bind it to the stored byte ranges."""

from collections import Counter
from dataclasses import dataclass
import codecs
import re

from .binary_source import byte_asset
from .core import Error
from .epub_semantics import uncertainty
from .source_notes import Definition, NoteGraph, note_text
from .source_scripts import prepare


HEADER = b'\x1d\x7d\x00\x00\x70'
HEADER_END = b'\x7d\x00\x1d'
HEADER_SIZE = 128
WORDSTAR_SUFFIXES = frozenset(('.ws','.ws3','.ws4','.ws5','.ws6','.ws7'))
STYLE_TAGS = {0x02:'emphasis',0x04:'emphasis',0x13:'underline',0x14:'superscript',
              0x16:'subscript',0x18:'strikeout',0x19:'emphasis'}
PRINT_CONTROLS = frozenset((0x00,0x01,0x03,0x0b,0x0e,0x1e))
PRINT_RECORDS = frozenset((1,0x0b,0x0e))
LAYOUT_COMMANDS = frozenset(('..','AW','BN','BP','CC','CO','CP','CS','CW','DM','E#','F#',
                            'F1','F2','F3','FO','H1','H2','H3','HE','HM','IG','JM','LH',
                            'LM','LS','MB','MT','OP','PA','PB','PC','PL','PM','PN','PO',
                            'PR','PS','RM','RR','SB','SR','TB','TC','UJ','UL'))
REVIEW_COMMANDS = frozenset(('AV','CV','DF','EI','EL','FI','GO','IF','IN','RP','RV','SV',
                            'XE','XQ','XR','XW','XL','XX'))
DOT = re.compile(rb'^\.(?:(?P<comment>\.(?![.\w]))|(?P<name>[A-Za-z][A-Za-z0-9#])(?=$|[\x1d\s0-9#-]|(?<=rr)))',re.I)
SEVEN_BIT = bytes(value&127 for value in range(256))
LAYOUT_RULE = re.compile(r'(?:[─━═]\s*){3,}')
BOX_ROW = re.compile(r'[\u2500-\u257f\s]{3,}')
HEADINGS = frozenset(('title','book title','chapter title','ms chapter title','h1','h2','h3',
                      'heading','heading 1','heading 2','heading 3'))


@dataclass(frozen=True,slots=True)
class Record:
    start: int
    end: int
    kind: int
    payload: bytes


def matches_header(prefix):
    return (len(prefix)>=HEADER_SIZE and prefix.startswith(HEADER)
            and prefix[HEADER_SIZE-3:HEADER_SIZE]==HEADER_END)


def number(data,offset,width=2):
    payload = data[offset:offset+width]
    if len(payload)!=width:
        raise ValueError('A stored integer is truncated.')
    return int.from_bytes(payload,'little')


def record(data,start,limit):
    size = number(data,start+1)
    trailer = start+size
    if size<4 or trailer+3>limit or number(data,trailer)!=size or data[trailer+2]!=0x1d:
        raise ValueError('A symmetrical record has an invalid length or closing marker.')
    return Record(start,trailer+3,data[start+3],data[start+4:trailer])


def style_names(data,start):
    if not start:
        return ()
    if (start%128 or start+13>=len(data) or data[start:start+2]!=b'\x1a\x55'
            or data[start+4]!=1 or number(data,start+7)!=102):
        raise ValueError('The saved paragraph-style catalogue has an unsupported header.')
    maximum = number(data,start+5)
    if not 0<maximum<=2048:
        raise ValueError('The saved paragraph-style catalogue exceeds its entry limit.')
    names,visited = [],set()
    offset = number(data,start+9,4)
    while offset:
        cursor = start+offset
        if offset in visited or cursor+5>len(data):
            raise ValueError('The saved paragraph-style catalogue has an invalid index link.')
        visited.add(offset)
        count = data[cursor]
        first, end = cursor+5,cursor+5+count*33
        if not count or len(names)+count>maximum or end>len(data):
            raise ValueError('The saved paragraph-style index block is incomplete.')
        names.extend(data[pos:pos+24].decode('cp437').rstrip(' \0') for pos in range(first,end,33))
        offset = number(data,cursor+1,4)
    return names


def command(line):
    if match:=DOT.match(line.translate(SEVEN_BIT)):
        name = '..' if match['comment'] else match['name'].decode('ascii').upper()
        if name in LAYOUT_COMMANDS or name in REVIEW_COMMANDS:
            return name
    return None


def line_bounds(data,start,limit):
    cursor = start
    while cursor<limit:
        value = data[cursor]
        if value==0x1a:
            break
        if cursor+1<limit and value in (0x0d,0x8d) and data[cursor+1]&127==10:
            return cursor,cursor+2
        if value==0x1d:
            cursor = record(data,cursor,limit).end
        elif value==0x1b:
            if cursor+3>limit or data[cursor+2]!=0x1c:
                raise ValueError('A WordStar extended character is incomplete.')
            cursor += 3
        else:
            cursor += 1
    return cursor,cursor


class Paragraph:
    def __init__(self):
        self.text = []
        self.spans = []
        self.position = 0
        self.problems = []

    def add(self,value,active=()):
        start = self.position
        self.text.append(value)
        self.position += len(value)
        for code in active:
            self.span(STYLE_TAGS[code],start,self.position,control=code)

    def span(self,kind,start,end,**fields):
        for previous in reversed(self.spans):
            if previous['source_end']<start:
                break
            if (start!=end and previous['kind']==kind and previous['source_end']==start
                    and previous.get('control')==fields.get('control')):
                previous['source_end'] = end
                return
        self.spans.append({'kind':kind,'source_start':start,'source_end':end,**fields})

    def reading(self):
        raw = ''.join(self.text)
        text = raw.strip()
        skip = len(raw)-len(raw.lstrip())
        spans = []
        for span in self.spans:
            a,b = (min(len(text),max(0,span[field]-skip)) for field in ('source_start','source_end'))
            if a<b or span['kind']=='note_reference':
                spans.append({**span,'source_start':a,'source_end':b,'source':text[a:b]})
        return text,prepare(text,spans)


class Manuscript:
    def __init__(self,data,builder,member,encoding,styles):
        self.data = data
        self.builder = builder
        self.member = member
        self.encoding = encoding
        self.codec = encoding or 'cp437'
        self.styles = styles
        self.active = {}
        self.name = ''
        self.following_style = None
        self.counts = Counter()
        self.definitions = {}
        self.paragraph = Paragraph()
        self.start = HEADER_SIZE

    def location(self,start,end):
        return {'source_file':self.member,'bytes':[start,end],
                'binary_asset':byte_asset(self.data,start,end)}

    def emit(self,text,role,start,end,**fields):
        if len(self.builder.doc.blocks)>=100000:
            raise Error('source_structure_size','The WordStar manuscript exceeds its structure budget.')
        return self.builder.add(text,role,f'{self.member}:{start}',loc={**self.location(start,end),**fields.pop('loc',{})},
                                **fields)

    def flush(self,end):
        text,spans = self.paragraph.reading()
        issues = self.paragraph.problems[:]
        role = 'heading' if self.name.casefold() in HEADINGS else 'body'
        if LAYOUT_RULE.fullmatch(text):
            role = 'scene_break'
        elif BOX_ROW.fullmatch(text):
            role = 'furniture'
        if self.name.casefold()=='bulleted list' and (bullet:=re.match(r'^[■•·]\s*',text)):
            spans.append({'kind':'hidden','source_start':0,'source_end':bullet.end(),
                          'source':bullet[0],'origin':'Authored WordStar list bullet'})
        if problem:=uncertainty(spans):
            issues.append(problem)
        if text or spans or issues:
            loc = {'wordstar_style':self.name}
            if 'quote' in self.name.casefold() or self.name.casefold()=='quotation':
                loc['block_quote'] = True
            if spans:
                loc['inline_semantics'] = spans
            self.emit(text,role,self.start,end,loc=loc,issue=' '.join(dict.fromkeys(issues)) or None)
        self.start,self.paragraph = end,Paragraph()
        if self.following_style is not None:
            self.name,self.following_style = self.following_style,None

    def note(self,item):
        data = item.payload
        if len(data)<5:
            self.paragraph.problems.append('An embedded WordStar note header is incomplete.')
            return
        conversion = data[4]&15
        kind = conversion if conversion in (3,4,6) else item.kind
        if number(data,2)&0x8000:
            self.paragraph.problems.append('A tagged WordStar note needs a verified reading.')
            return
        nested = Manuscript(self.data,self.builder,self.member,self.encoding,self.styles)
        nested.codec = self.codec
        nested.start = item.start+9
        view = nested.scan(nested.start,item.end-3,embedded=True)
        reading,spans = view.reading()
        loc = {'inline_semantics':spans} if spans else {}
        problem = ' '.join(view.problems) or uncertainty(spans)
        note = self.emit(reading,'note' if kind in (3,4,5) else 'hidden',item.start,item.end,
                         loc=loc,issue=problem if kind not in (3,4,5) else None)
        if kind in (3,4,5):
            href = f'#wordstar-note-{item.start}'
            if note:
                self.definitions[href] = [Definition(note,note_text(note,terminal=True),problem)]
            position = self.paragraph.position
            self.paragraph.span('note_reference',position,position,href=href,note_kind=kind,
                                native_bytes=[item.start,item.end])

    def record(self,item,embedded):
        kind = item.kind
        self.counts[kind] += 1
        if kind in (3,4,5,6) and not embedded:
            self.note(item)
        elif kind==9 and len(item.payload)==6:
            self.paragraph.add(' ')
        elif kind==0x11 and len(item.payload)==8 and not embedded:
            bank,selector = divmod(number(item.payload,0),256)
            if bank==2 and selector<len(self.styles):
                if any(value.strip() for value in self.paragraph.text):
                    self.following_style = self.styles[selector]
                else:
                    self.name = self.styles[selector]
            else:
                self.paragraph.problems.append('The selected WordStar paragraph style is missing.')
        elif kind in (2,0x15) and len(item.payload)>=(12 if kind==2 else 13):
            offset = 4 if kind==2 else 5
            mapping = (number(item.payload,offset)>>12)&3
            self.codec = self.encoding or {0:'cp437',1:'cp850'}.get(mapping)
        elif kind==0x10:
            self.paragraph.problems.append(f'An external WordStar illustration needs a verified description at byte {item.start}.')
        elif kind not in PRINT_RECORDS:
            self.paragraph.problems.append(f'WordStar record 0x{kind:02x} needs source review at byte {item.start}.')

    def scan(self,start,limit,*,embedded=False):
        data = self.data
        cursor = start
        beginning = True
        while cursor<limit:
            if cursor-self.start>262144:
                raise Error('source_structure_size','A WordStar paragraph exceeds its byte budget.')
            byte = data[cursor]
            if byte==0x1a and not embedded:
                self.flush(cursor)
                return cursor
            if byte==0x1d:
                item = record(data,cursor,limit)
                self.record(item,embedded)
                cursor = item.end
                continue
            if byte==0x1b:
                if cursor+3>limit or data[cursor+2]!=0x1c:
                    raise ValueError('A WordStar extended character is incomplete.')
                if self.codec is None:
                    self.paragraph.problems.append('An extended WordStar character uses a custom font mapping.')
                value = bytes([data[cursor+1]]).decode(self.codec or 'cp437')
                self.paragraph.add(value,self.active)
                cursor += 3
                beginning = False
                continue
            if beginning and byte==0x2e and not embedded and (name:=command(data[cursor:cursor+12])):
                self.flush(cursor)
                end,stop = line_bounds(data,cursor,min(limit,cursor+262144))
                if stop==cursor+262144 and stop<limit:
                    raise Error('source_structure_size','A WordStar dot command exceeds its byte budget.')
                line = ''.join(chr(code) for code in data[cursor:end].translate(SEVEN_BIT) if code>=32)
                issue = f'The WordStar .{name} command can change the printed content. Verify the original.' if name in REVIEW_COMMANDS else None
                self.emit(line.strip(),'unreadable' if issue else 'furniture',cursor,stop,issue=issue)
                self.start = cursor = stop
                continue
            if (byte==0x1f and cursor+2<limit and data[cursor+1]==0x8d and data[cursor+2]&127==10):
                cursor += 3
                continue
            if cursor+1<limit and byte in (0x0d,0x8d) and data[cursor+1]&127==10:
                cursor += 2
                if byte==0x0d and not embedded:
                    self.flush(cursor)
                    beginning = True
                elif self.paragraph.text and not self.paragraph.text[-1].isspace():
                    self.paragraph.add(' ')
                continue
            char = byte&127
            if char>=32:
                self.paragraph.add(chr(char),self.active)
                beginning = False
            elif char in STYLE_TAGS:
                if char in self.active:
                    del self.active[char]
                else:
                    self.active[char] = None
            elif char in (9,0x0f):
                self.paragraph.add(' ',self.active)
            elif char==0x0c and not embedded:
                self.flush(cursor+1)
                beginning = True
            elif char not in PRINT_CONTROLS:
                self.paragraph.problems.append(f'WordStar character control 0x{byte:02x} needs source review at byte {cursor}.')
            cursor += 1
        if embedded:
            return self.paragraph
        raise ValueError('The WordStar source ends without its end-of-text marker.')


def read_wordstar(data,builder,source,member):
    if not matches_header(data):
        raise Error('source_wordstar_version','The original does not identify a supported WordStar 7 document.',
                    'Choose the original WordStar 7 file or open a readable text export.')
    encoding = source.encoding
    if encoding:
        try:
            encoding = codecs.lookup(encoding).name
        except LookupError as exc:
            raise Error('source_encoding','Use CP437 or CP850 for WordStar extended characters.') from exc
        if encoding not in ('cp437','cp850'):
            raise Error('source_encoding','Use CP437 or CP850 for WordStar extended characters.')
    catalog = number(data,16,4)
    try:
        styles = style_names(data,catalog)
    except ValueError as exc:
        raise Error('source_wordstar_style',str(exc)) from exc
    reader = Manuscript(data,builder,member,encoding,styles)
    try:
        end = reader.scan(HEADER_SIZE,catalog or len(data))
        if data[end:catalog or len(data)].strip(b'\x1a\x00'):
            raise ValueError('The WordStar source has unaccounted bytes between text and the style catalogue.')
    except ValueError as exc:
        end = None
        reader.paragraph.problems.append(str(exc))
        reader.flush(catalog or len(data))
    graph = NoteGraph(reader.definitions.get)
    for block in builder.doc.blocks:
        block.links.extend(graph.links_for(block.locator.get('inline_semantics',())))
    builder.doc.inventory.append({'wordstar_version':'7.0','source_file':member,'end_of_text':end,
                                  'style_catalogue':catalog,'styles':styles,'record_counts':dict(reader.counts)})
