"""Read the record directory and text compression shared by Palm ebooks."""
from bisect import bisect_right
import codecs
from struct import Struct
import zlib

from .binary_source import byte_asset
from .core import Error, key
from .document_intake import MAX_STRUCTURE


BOOK_TYPES = {b'TEXtREAd':'palmdoc',b'PNRdPPrs':'ereader',b'zTXTGPlm':'ztxt'}
DIRECTORY_RECORD = Struct('>I4x')
PALM_HEADER_SIZE = 78
MAX_PALM_RECORD = 65535


class Database:
    def __init__(self,data,signature,sha):
        if len(data)<PALM_HEADER_SIZE or data[60:68]!=signature:
            raise Error('source_palm_header','The source has no matching Palm ebook signature.')
        if int.from_bytes(data[32:34],'big')&1:
            raise Error('source_palm_header','The Palm source is an application resource database.')
        if data[72:76]!=b'\0\0\0\0':
            raise Error('source_palm_header','Palm databases with chained record directories need a readable export.')
        self.data = memoryview(data)
        self.sha = sha
        self.title = data[:32].split(b'\0',1)[0].decode('cp1252',errors='replace').strip()
        self.count = int.from_bytes(data[76:78],'big')
        table_end = PALM_HEADER_SIZE+self.count*DIRECTORY_RECORD.size
        if self.count<2 or table_end>len(data):
            raise Error('source_palm_header','The Palm ebook record directory is incomplete.')
        self.offsets = [offset for offset, in DIRECTORY_RECORD.iter_unpack(self.data[PALM_HEADER_SIZE:table_end])]
        self.offsets.append(len(data))
        if self.offsets[0]<table_end or any(a>b for a,b in zip(self.offsets,self.offsets[1:])):
            raise Error('source_palm_header','The Palm record directory contains invalid offsets.')

    def bounds(self,index):
        if not 0<=index<self.count:
            raise Error('source_palm_record',f'The Palm book has no record {index}.')
        return self.offsets[index],self.offsets[index+1]

    def read(self,index):
        start,end = self.bounds(index)
        if end-start>MAX_PALM_RECORD:
            raise Error('source_structure_size',f'Palm record {index} exceeds the single-record reading budget.')
        return self.data[start:end].tobytes()

    def asset(self,index,role,*,prefix=0,name=None):
        start,end = self.bounds(index)
        start += prefix
        return {'role':role,'palm_record':index,'name':name,'byte_range':[start,end],
                'resource_label':name or f'Palm record {index}',
                'location_id':key([self.sha,index,start,end])[:20],
                'binary_asset':byte_asset(self.data,start,end)}


def decompress_palm(data,limit):
    """Expand one PalmDOC record with references confined to that record."""
    cursor = 0
    out = bytearray()
    while cursor<len(data):
        token = data[cursor]
        cursor += 1
        if 1<=token<=8:
            stop = cursor+token
            if stop>len(data):
                raise ValueError('A literal run is incomplete.')
            out.extend(data[cursor:stop])
            cursor = stop
        elif token<128:
            out.append(token)
        elif token>=192:
            out.extend((32,token^128))
        else:
            if cursor==len(data):
                raise ValueError('A back-reference is incomplete.')
            pair = (token << 8)|data[cursor]
            cursor += 1
            distance = (pair>>3)&2047
            length = (pair&7)+3
            if not 0<distance<=len(out):
                raise ValueError('A back-reference starts outside this record.')
            for _ in range(length):
                out.append(out[-distance])
        if len(out)>limit:
            raise ValueError('The decoded record exceeds its length limit.')
    return bytes(out)


def text_record(database,index,codec,limit):
    data = database.read(index)
    try:
        if codec==1:
            value = data
        elif codec in (2,258):
            value = decompress_palm(data,limit)
        else:
            decoder = zlib.decompressobj()
            value = decoder.decompress(data,limit+1)
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError('The zlib record is incomplete or contains extra data.')
        if len(value)>limit:
            raise ValueError('The decoded record exceeds its length limit.')
    except (ValueError,zlib.error) as error:
        raise Error('source_palm_record',f'Palm text record {index} is invalid. {error}') from error
    return value


def text_records(database,indices,codec,record_limit):
    chunks = []
    size = 0
    for index in indices:
        data = text_record(database,index,codec,min(record_limit,MAX_STRUCTURE-size))
        size += len(data)
        chunks.append((index,data))
    return chunks


class Locations:
    """Map decoded source ranges back to the records that supplied their text."""
    def __init__(self,chunks,encoding):
        decoder = codecs.getincrementaldecoder(encoding)()
        self.mapping = []
        self.ends = []
        size, characters = 0, 0
        byte_ends = []
        for order,(index,data) in enumerate(chunks):
            pending = len(decoder.getstate()[0])
            value = decoder.decode(data,final=order==len(chunks)-1)
            first = bisect_right(byte_ends,size-pending) if pending else order
            size += len(data)
            byte_ends.append(size)
            if value:
                carries = tuple(number for number,_ in chunks[first:order])
                characters += len(value)
                self.ends.append(characters)
                self.mapping.append((index,carries))

    def overlaps(self,start,end):
        selected = []
        index = bisect_right(self.ends,start)
        while index<len(self.ends) and (self.ends[index-1] if index else 0)<end:
            record,carry = self.mapping[index]
            if (self.ends[index-1] if index else 0)>=start:
                selected.extend(carry)
            selected.append(record)
            index += 1
        return list(dict.fromkeys(selected))


def plain_record_book(database,chunks,source,filename,title,route):
    from .text_source import plain

    document = plain(b''.join(data for _,data in chunks),source.encoding,fallback='cp1252')
    document.title = database.title or title
    document.format = route
    document.source_sha256 = database.sha
    encoding = document.inventory[0]['encoding']
    locations = Locations(chunks,encoding)
    for block in document.blocks:
        locator = block.locator
        locator['source_file'] = source.member_name or filename
        locator['palm_records'] = locations.overlaps(locator['start'],locator['end'])
    document.metadata['source_resources'] = [database.asset(index,'palm_text_record') for index,_ in chunks]
    return document
