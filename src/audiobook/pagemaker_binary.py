"""Bounded access to the page, frame and story records of a PageMaker publication."""

from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from struct import Struct

from .core import Error
from .document_intake import MAX_STRUCTURE

COMPOUND = bytes.fromhex('d0cf11e0a1b11ae1')
PAGE, LINES, TEXT, FONTS, SHAPES, STORIES, FRAMES, RUNS = 5,9,13,19,25,26,27,28
VERSION_SHAPES = {0x0600:136,0x0632:258}
STRIDES = {PAGE:472,LINES:16,TEXT:1,FONTS:94,STORIES:36,FRAMES:24,RUNS:30}
MAX_DIRECTORY_ENTRIES = 65_536
MAX_STRUCTURAL_RECORDS = 300_000
EXPORT = 'Open the original publication or export a readable tagged PDF from PageMaker.'


def byte_order(header):
    signature = header[6:8],header[0x6c:0x6e]
    return {(b'\xff\x99',b'MP'):'<',(b'\x99\xff',b'PM'):'>'}.get(signature)


def is_pagemaker_compound(stream):
    try:
        return byte_order(read_stream(stream.read(MAX_STRUCTURE+1))) is not None
    except Error:
        return False


def read_stream(raw):
    if len(raw)>MAX_STRUCTURE:
        raise Error('source_structure_size','The PageMaker publication exceeds the structured reader memory budget.',EXPORT)
    if not raw.startswith(COMPOUND):
        return raw
    import olefile

    try:
        with olefile.OleFileIO(BytesIO(raw),raise_defects=olefile.DEFECT_INCORRECT) as compound:
            key = ['PageMaker']
            if not compound.exists(key) or compound.get_type(key)!=olefile.STGTY_STREAM:
                raise Error('source_pagemaker_signature','The compound file has no PageMaker publication stream.',EXPORT)
            if compound.get_size(key)>MAX_STRUCTURE:
                raise Error('source_structure_size','The PageMaker stream exceeds the structured reader memory budget.',EXPORT)
            with compound.openstream(key) as stream:
                return stream.read()
    except (OSError,ValueError,IndexError,OverflowError,RecursionError) as exc:
        raise Error('source_pagemaker_container','The compound PageMaker publication is incomplete or unreadable.',EXPORT) from exc


@dataclass(frozen=True)
class Array:
    kind: int
    count: int
    offset: int


class Publication:
    def __init__(self,raw):
        self.data = read_stream(raw)
        self.order = byte_order(self.data)
        if self.order is None:
            raise Error('source_pagemaker_signature','The input does not identify a versioned Adobe PageMaker publication.',EXPORT)
        self.word = Struct(self.order+'H')
        self.long = Struct(self.order+'I')
        self.point = Struct(self.order+'hhhh')
        self.version = self.u16(0x6e)
        if self.version not in VERSION_SHAPES:
            raise Error('source_pagemaker_version',f'This PageMaker publication uses record version 0x{self.version:04x}.',
                        'Open a PageMaker 6, 6.5 or 7 publication or export it to a readable PDF.')
        self.strides = STRIDES|{SHAPES:VERSION_SHAPES[self.version]}
        self.sequences = defaultdict(list)
        self.arrays = defaultdict(list)
        self.read_directory()

    def extent(self,offset,size):
        if not 0<=offset<=offset+size<=len(self.data):
            raise Error('source_pagemaker_records',f'A PageMaker record at stream byte {offset:,} is incomplete.',EXPORT)
        return memoryview(self.data)[offset:offset+size]

    def u16(self,offset):
        return self.word.unpack(self.extent(offset,2))[0]

    def u32(self,offset):
        return self.long.unpack(self.extent(offset,4))[0]

    def read_directory(self):
        visited = set()
        sequence = count = saved_records = 0

        def visit(offset,length,group=None,depth=0):
            nonlocal count,sequence
            if offset in visited or depth>24 or not offset:
                raise Error('source_pagemaker_directory','The PageMaker record directory repeats or exceeds its nesting limit.',EXPORT)
            visited.add(offset)
            for _ in range(length):
                count += 1
                if count>MAX_DIRECTORY_ENTRIES:
                    raise Error('source_structure_size','The PageMaker record directory exceeds the reader budget.',EXPORT)
                record = self.extent(offset,10)
                kind,rows,start = record[1],self.u16(offset+2),self.u32(offset+4)
                if group is None and kind==0 and rows:
                    visit(start,rows,depth=depth+1)
                    offset += 10
                    continue
                if group is None:
                    self.extent(offset,16)
                    if kind==1 and rows:
                        visit(start,rows,sequence,depth+1)
                    elif kind and rows:
                        bind(sequence,kind,rows,start)
                    sequence += 1
                    offset += 16
                else:
                    if kind and rows:
                        bind(group,kind,rows,start)
                    offset += 10

        def bind(sequence,kind,rows,start):
            nonlocal saved_records
            if not start:
                raise Error('source_pagemaker_directory','A PageMaker record array has no stored byte offset.',EXPORT)
            if stride:=self.strides.get(kind):
                self.extent(start,rows*stride)
                if kind!=TEXT:
                    saved_records += rows
                    if saved_records>MAX_STRUCTURAL_RECORDS:
                        raise Error('source_structure_size','The PageMaker publication has too many structured records.',EXPORT)
            info = Array(kind,rows,start)
            self.sequences[sequence].append(info)
            self.arrays[kind].append(info)

        visit(self.u32(0x30),self.u16(0x2e))
        self.slot_count = sequence

    def records(self,kind,sequence=None):
        stride = self.strides[kind]
        arrays = self.arrays[kind] if sequence is None else self.sequences[sequence]
        for array in arrays:
            if array.kind==kind:
                yield from range(array.offset,array.offset+array.count*stride,stride)

    def story_bytes(self,sequence,remaining):
        fragments = [array for array in self.sequences[sequence] if array.kind==TEXT]
        if sum(array.count for array in fragments)>remaining:
            raise Error('source_structure_size','The PageMaker story text exceeds the reader budget.',EXPORT)
        return b''.join(self.extent(array.offset,array.count) for array in fragments),fragments
