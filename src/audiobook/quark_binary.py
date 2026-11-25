"""Bounded QuarkXPress 3 publication records, placements and story extents."""

from dataclasses import dataclass
from struct import calcsize, unpack_from

from .core import Error
from .document_intake import MAX_STRUCTURE

BLOCK = 256
VARIANTS = {0x39:'3.1',0x3c:'3.2',0x3e:'3.1',0x3f:'3.3'}
OLD_TYPES = {0:(2,0),1:(2,1),3:(3,2),11:(1,2),12:(5,2),13:(5,3),14:(5,4),15:(5,5)}
EXPORT = 'Export a readable tagged PDF or EPUB from a compatible QuarkXPress installation.'


def byte_order(prefix):
    if len(prefix)>=12 and prefix[:2]==b'\0\0' and prefix[4:7]==b'XPR':
        return {b'MM':'>',b'II':'<'}.get(prefix[2:4])


def invalid(detail):
    return Error('source_quark_records',f'The QuarkXPress publication has {detail}.',EXPORT)


class Reader:
    def __init__(self,data,order):
        self.data,self.order = memoryview(data),order
        self.offset = 0

    @property
    def remaining(self):
        return len(self.data)-self.offset

    def take(self,length):
        start = self.offset
        if not 0<=length<=self.remaining:
            raise invalid(f'an incomplete record at section byte {start:,}')
        self.offset += length
        return self.data[start:self.offset]

    def values(self,pattern):
        format_code = self.order+pattern
        return unpack_from(format_code,self.take(calcsize(format_code)))

    def number(self,pattern):
        return self.values(pattern)[0]

    def field(self):
        return self.take(self.number('I'))


@dataclass(frozen=True)
class Placement:
    page: int
    offset: int
    left: int
    top: int
    ref: int
    link: int
    start: int
    successor: int

    @property
    def order(self):
        return self.page,self.top,self.left,self.offset


@dataclass(frozen=True)
class Story:
    data: bytes
    pieces: tuple
    formats: tuple


class Publication:
    def __init__(self,data):
        if len(data)>MAX_STRUCTURE:
            raise Error('source_structure_size','The QuarkXPress publication exceeds the structured reader memory budget.',EXPORT)
        self.order = byte_order(data)
        if self.order is None:
            raise Error('source_quark_signature','The input has no recognized binary QuarkXPress publication header.',EXPORT)
        if len(data)<3*BLOCK:
            raise invalid('an incomplete publication header')
        self.data = data
        self.version = self.header('H',8)
        if self.version not in VARIANTS or data[7]!=0x33:
            raise Error('source_quark_version',f'This QuarkXPress publication uses layout 0x{self.version:04x} and language marker 0x{data[7]:02x}.',EXPORT)
        self.new_objects = self.version==0x3f
        short = (self.new_objects and self.order=='>' and not data[26]&128
                 or self.version==0x3e and self.order=='<' and not data[26]&1)
        self.link_size = 2 if short else 4
        self.encoding = 'mac_roman' if self.order=='>' else 'cp1252'
        self.page_count = self.header('H',64)
        self.masters = data[117]
        if not 0<self.page_count<=20_000:
            raise invalid('an invalid page count')
        self.seed,self.increment = unpack_from(self.order+'HH',data,272)
        self.placements = []
        self.figures = []
        self.fonts = {}
        self.styles = ()
        self.remaining = MAX_STRUCTURE
        self.chain_budget = 2*MAX_STRUCTURE
        self.read_pages()

    def header(self,pattern,offset):
        return unpack_from(self.order+pattern,self.data,offset)[0]

    def chain(self,block):
        seen = set()
        parts = []
        grouped = False
        while block:
            start = (block-1)*BLOCK
            if not 2*BLOCK<=start<=len(self.data)-BLOCK:
                raise invalid(f'a block reference outside the file ({block})')
            count = self.header('H',start) if grouped else 1
            end = start+count*BLOCK
            extent = range(block,block+count)
            if not count or end>len(self.data) or any(number in seen for number in extent):
                raise invalid(f'a repeated or incomplete block chain at block {block}')
            seen.update(extent)
            first = start+2 if grouped else start
            last = end-self.link_size
            self.chain_budget -= last-first
            if self.chain_budget<0:
                raise Error('source_structure_size','The QuarkXPress block chains exceed the reader budget.',EXPORT)
            parts.append(self.data[first:last])
            link = self.header('h' if self.link_size==2 else 'i',last)
            grouped = link<0
            block = abs(link)
        return b''.join(parts)

    def read_fonts(self,data):
        if not data:
            return
        reader = Reader(data,self.order)

        def label():
            if self.order=='>':
                return bytes(reader.take(reader.number('B')))
            end = reader.data.obj.find(b'\0',reader.offset) if isinstance(reader.data.obj,bytes) else -1
            if end<reader.offset:
                raise invalid('an incomplete font name')
            return bytes(reader.take(end-reader.offset+1))[:-1]

        for _ in range(reader.number('H')):
            ident = reader.number('h')
            name = label().decode(self.encoding,'replace')
            label()
            self.fonts[ident] = name

    def read_pages(self):
        reader = Reader(self.chain(3),self.order)
        for _ in range(4):
            reader.field()
        self.read_fonts(bytes(reader.field()))
        for _ in range(6 if self.new_objects else 5):
            reader.field()
        chars = reader.field()
        if len(chars)%46:
            raise invalid('an incomplete character style table')
        self.styles = tuple(unpack_from(self.order+'hH',chars,index+2)
                            for index in range(0,len(chars),46))
        reader.field()
        reader.field()
        ordinal = 0
        total = 0
        for spread in range(self.page_count+self.masters):
            reader.take(6)
            page_number = reader.number('H')
            if page_number not in (1,2):
                raise invalid('an invalid spread width')
            reader.take(2)
            rectangles = []
            for _ in range(page_number):
                rectangles.append(reader.values('iiii'))
                reader.take(44)
            for _ in range(2*page_number+3+(self.order=='<')):
                reader.field()
            count = reader.number('I')
            total += count
            if total>80_000:
                raise invalid('too many page objects')
            for _ in range(count):
                self.read_object(reader,rectangles,ordinal if spread>=self.masters else None)
            if spread>=self.masters:
                ordinal += page_number
        self.physical_pages = ordinal

    def read_object(self,reader,rectangles,ordinal):
        address = reader.offset
        kind = reader.number('B')^(self.seed&255)
        reader.take(5)
        ref = (reader.number('I')&65535)^self.seed
        self.seed = (self.seed+self.increment)&65535
        reader.take(10)
        link,extra = reader.values('II')
        reader.take(6)
        if self.new_objects:
            content,shape = reader.values('BB')
            reader.take(4)
        else:
            content,shape = OLD_TYPES.get(kind,(0,0))
        if not 1<=content<=5 or shape>5:
            raise invalid(f'an unknown page object at main-chain byte {address:,}')
        if extra:
            reader.field()
        top,left,_,_ = reader.values('iiii')
        page = None if ordinal is None else ordinal+1+sum(left>=rect[1] for rect in rectangles[1:])
        if content==1:
            reader.take(10)
            members = reader.number('H')
            reader.take(6+4*members)
            return
        if shape in (0,1):
            reader.take(6)
            return
        reader.take(14)
        if content==3:
            around,start = reader.values('II')
            reader.take(42)
            successor = reader.number('I')
            reader.take(8)
            if shape==5:
                reader.field()
            if not ref or not start:
                reader.take(4)
                info = reader.number('I')
                reader.take(4)
                if info:
                    reader.field()
                if not ref:
                    reader.take(12)
            if around:
                reader.field()
            if page is not None and ref:
                self.placements.append(Placement(page,address,left,top,ref,link,start,successor))
        elif content==5:
            around = 0
            if self.new_objects:
                around = reader.number('I')
                reader.take(2)
            else:
                reader.take(10)
            info = reader.number('I')
            reader.take(60)
            object_info = reader.number('I')
            reader.take(4)
            if shape==5:
                reader.field()
            for payload in (info,around,object_info):
                if payload:
                    reader.field()
            if page is not None and (ref or info):
                self.figures.append((page,top,left,address))
        else:
            around = reader.number('I')
            reader.take(74)
            if shape==5:
                reader.field()
            if around:
                reader.field()

    def story(self,first):
        reader = Reader(self.chain(first),self.order)
        size = reader.number('I')
        directory = Reader(reader.field(),self.order)
        if size>self.remaining:
            raise Error('source_structure_size','The QuarkXPress story text exceeds the reader budget.',EXPORT)
        parts = []
        locations = []
        position = 0
        while directory.remaining:
            block = directory.number('H' if self.link_size==2 else 'I')
            length = directory.number('H')
            origin = (block-1)*BLOCK
            if not 0<length<=BLOCK or not 2*BLOCK<=origin<=len(self.data)-length or position+length>size:
                raise invalid(f'an invalid text extent in story block {first}')
            locations.append((position,position+length,origin))
            parts.append(self.data[origin:origin+length])
            position += length
        if position!=size:
            raise invalid(f'an incomplete text extent in story block {first}')
        self.remaining -= size
        styles = self.runs(reader.field(),size)
        self.runs(reader.field(),size)
        return Story(b''.join(parts),tuple(locations),styles)

    def runs(self,data,size):
        reader = Reader(data,self.order)
        runs = []
        position = 0
        while reader.remaining:
            index,length = reader.values('HI')
            if position+length>size:
                raise invalid('a style extent beyond its story')
            if length:
                runs.append((position,position+length,index))
            position += length
        if runs and position!=size:
            raise invalid('an incomplete style extent')
        return tuple(runs)
