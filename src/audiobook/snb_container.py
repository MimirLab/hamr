"""Read Bambook SNB members by their declared stream offsets."""

from bz2 import BZ2Decompressor
from dataclasses import dataclass
import os
from struct import Struct
import zlib

from .core import Error
from .document_intake import MAX_STRUCTURE
from .source_bundle import safe_name

MAGIC = b'SNBP000B'
HEADER = Struct('>8s9I')
FOOTER = Struct('>2I8s')
TABLE_ENTRY = Struct('>3I')
POINTER = Struct('>2I')
UINT = Struct('>I')
BLOCK_SIZE = 32768
TEXT = 0x41000000
BINARY = 0x01000000


@dataclass(frozen=True)
class Member:
    name: str
    index: int
    text: bool
    offset: int
    size: int

    def identity(self):
        return {'name':self.name,'index':self.index,'size':self.size}


class Package:
    def __init__(self,stream):
        self.stream = stream
        self.length = stream.seek(0,2)
        self.cached_block = (-1,b'')
        if self.length<HEADER.size+FOOTER.size:
            raise Error('source_snb_header','The Bambook file header or footer is incomplete.')
        magic,version,revision,reserved,count,fat_size,fat_stored,binary_size,text_size,reserved2 = (
            HEADER.unpack(self.read_range(0,HEADER.size)))
        if magic!=MAGIC or version!=0x8000 or reserved or reserved2:
            raise Error('source_snb_header','This does not identify a supported Bambook ebook.')
        if max(fat_size,text_size)>MAX_STRUCTURE:
            raise Error('source_structure_size','The Bambook directory or decoded text exceeds the structured reading budget.')
        if not count or count*(TABLE_ENTRY.size+2)>fat_size:
            raise Error('source_snb_directory','The Bambook member count does not fit its directory.')

        tail_size,self.tail_start,tail_magic = FOOTER.unpack(self.read_range(self.length-FOOTER.size,FOOTER.size))
        self.binary_start = HEADER.size+fat_stored
        self.text_start = self.binary_start+binary_size
        if (tail_magic!=MAGIC or self.text_start>self.tail_start
                or self.tail_start+tail_size!=self.length-FOOTER.size):
            raise Error('source_snb_header','The Bambook file streams or footer have invalid boundaries.')
        self.stream_sizes = {False:binary_size,True:text_size}
        self.binary_blocks = (binary_size+BLOCK_SIZE-1)//BLOCK_SIZE
        text_blocks = (text_size+BLOCK_SIZE-1)//BLOCK_SIZE
        block_count = self.binary_blocks+text_blocks
        tail_prefix = block_count*UINT.size
        tail = self.inflate(self.tail_start,tail_size,tail_prefix+count*POINTER.size,'footer index')
        offsets = [offset for offset, in UINT.iter_unpack(tail[:tail_prefix])]
        if any(value!=self.binary_start+index*BLOCK_SIZE for index,value in enumerate(offsets[:self.binary_blocks])):
            raise Error('source_snb_directory','A Bambook binary block has an invalid source offset.')
        self.text_offsets = offsets[self.binary_blocks:]+[self.tail_start]
        if self.text_offsets[0]!=self.text_start or any(
                not 0<end-start<=BLOCK_SIZE*2 for start,end in zip(self.text_offsets,self.text_offsets[1:])):
            raise Error('source_snb_directory','The Bambook text block boundaries are invalid.')

        fat = self.inflate(HEADER.size,fat_stored,fat_size,'member directory')
        self.members = self.directory(fat,tail[tail_prefix:],count)
        self.revision = revision
        self.by_name = {member.name:member for member in self.members}

    def read_range(self,start,size):
        self.stream.seek(start)
        raw = self.stream.read(size)
        if len(raw)!=size:
            raise Error('source_snb_record','The Bambook file ended inside a declared record.')
        return raw

    def inflate(self,start,stored,size,label):
        if max(size,stored)>MAX_STRUCTURE:
            raise Error('source_structure_size',f'The Bambook {label} exceeds the structured reading budget.')
        decoder = zlib.decompressobj()
        try:
            raw = decoder.decompress(self.read_range(start,stored),size+1)
        except zlib.error as exc:
            raise Error('source_snb_directory',f'The Bambook {label} has invalid compression.') from exc
        if len(raw)!=size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise Error('source_snb_directory',f'The Bambook {label} does not match its declared size.')
        return raw

    def directory(self,fat,pointers,count):
        split = count*TABLE_ENTRY.size
        names = fat[split:]
        members = []
        used = set()
        spans = {False:[],True:[]}
        for index,((flags,name_offset,size),(block,within)) in enumerate(zip(
                TABLE_ENTRY.iter_unpack(fat[:split]),POINTER.iter_unpack(pointers))):
            if flags not in (TEXT,BINARY):
                raise Error('source_snb_record','This Bambook ebook has an unsupported member storage mode.')
            end = names.find(b'\0',name_offset)
            if end<=name_offset or name_offset and names[name_offset-1]!=0:
                raise Error('source_snb_directory','A Bambook member name points outside its filename table.')
            try:
                path = safe_name(names[name_offset:end].decode('utf-8'))
            except UnicodeError as exc:
                raise Error('source_snb_directory','A Bambook member filename is not valid UTF-8.') from exc
            text = flags==TEXT
            offset = (block-(self.binary_blocks if text else 0))*BLOCK_SIZE+within
            if (path in used or within>=BLOCK_SIZE or offset<0 or offset+size>self.stream_sizes[text]):
                raise Error('source_snb_directory',f'Bambook member {path} has a duplicate name or invalid byte range.')
            members.append(Member(path,index,text,offset,size))
            used.add(path)
            if size:
                spans[text].append((offset,offset+size))
        for text,intervals in spans.items():
            end = 0
            for start,stop in sorted(intervals):
                if start!=end:
                    raise Error('source_snb_directory','Bambook member byte ranges overlap or leave an unclaimed gap.')
                end = stop
            if end!=self.stream_sizes[text]:
                raise Error('source_snb_directory','The Bambook directory does not account for its complete file streams.')
        return members

    def text_block(self,index):
        old,raw = self.cached_block
        if old==index:
            return raw
        begin,end = self.text_offsets[index:index+2]
        payload = self.read_range(begin,end-begin)
        size = min(BLOCK_SIZE,self.stream_sizes[True]-index*BLOCK_SIZE)
        if len(payload)==size and (size==BLOCK_SIZE or not payload.startswith(b'BZh')):
            raw = payload
        else:
            decoder = BZ2Decompressor()
            try:
                raw = decoder.decompress(payload,max_length=size+1)
            except (OSError,EOFError) as exc:
                raise Error('source_snb_record',f'Bambook text block {index+1} has invalid compression.') from exc
            if len(raw)!=size or not decoder.eof or decoder.unused_data:
                raise Error('source_snb_record',f'Bambook text block {index+1} does not match its declared size.')
        self.cached_block = (index,raw)
        return raw

    def chunks(self,member):
        start,end = member.offset,member.offset+member.size
        while start<end:
            if member.text:
                index,within = divmod(start,BLOCK_SIZE)
                raw = self.text_block(index)[within:within+end-start]
            else:
                raw = self.read_range(self.binary_start+start,min(end-start,256*1024))
            start += len(raw)
            yield raw

    def read(self,member):
        if member.size>MAX_STRUCTURE:
            raise Error('source_structure_size',f'The Bambook member exceeds its reading budget: {member.name}.')
        return b''.join(self.chunks(member))


def recover(path,destination,identity):
    with path.open('rb') as stream:
        package = Package(stream)
        member = package.by_name.get(identity.get('name'))
        if member is None or member.identity()!=identity:
            raise Error('source_location','The original Bambook member does not match the saved source location.')
        with destination.open('xb') as output:
            for chunk in package.chunks(member):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
