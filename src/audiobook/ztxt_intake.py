"""Read zTXT record books without treating device annotations as narration."""
from struct import Struct
import zlib

from .core import Error
from .document_intake import MAX_STRUCTURE, bounded_read
from .palm_database import Database, plain_record_book


HEADER = Struct('>HHI5HBBI')
INDEX_ENTRY = Struct('>I20s')
SIGNATURE = b'zTXTGPlm'
RANDOM_ACCESS = 1
NONUNIFORM = 2
FULL_FLUSH = bytes.fromhex('0000ffff')


def decode_body(database,count,length,record_size,flags,expected_crc):
    chunks = []
    remaining = length
    crc = 0
    random_access = bool(flags&RANDOM_ACCESS)
    uniform = random_access and not flags&NONUNIFORM
    decoder = zlib.decompressobj()
    for index in range(1,count+1):
        data = database.read(index)
        crc = zlib.crc32(data,crc)
        if random_access and index>1:
            # Full-flush records following the first record start in raw DEFLATE.
            decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        limit = min(record_size,remaining) if uniform else remaining
        try:
            chunk = decoder.decompress(data,limit+1)
        except zlib.error as error:
            raise Error('source_palm_record',f'zTXT text record {index} has invalid compressed data.') from error
        if len(chunk)>limit or decoder.unused_data or decoder.unconsumed_tail:
            raise Error('source_palm_record',f'zTXT text record {index} exceeds its declared data range.')
        if random_access and (decoder.eof or not data.endswith(FULL_FLUSH)):
            raise Error('source_palm_record',f'zTXT text record {index} lacks its full-flush boundary.')
        if uniform and (len(chunk)!=limit or not limit or index==count and remaining!=limit):
            raise Error('source_palm_record',f'zTXT text record {index} has the wrong expanded length.')
        if not random_access and decoder.eof and index<count:
            raise Error('source_palm_record','The zTXT continuous stream ended before the last text record.')
        remaining -= len(chunk)
        chunks.append((index,chunk))
    if remaining or not (random_access or decoder.eof):
        raise Error('source_palm_record','The zTXT text does not match its declared length or stream ending.')
    if expected_crc and crc!=expected_crc:
        raise Error('source_palm_record','The compressed zTXT text does not match its CRC.')
    return chunks


def read_index(database,index,count,first_aux,length,encoding,kind):
    if not count:
        return []
    if not first_aux<=index<database.count:
        raise Error('source_palm_header',f'The zTXT {kind} index is outside its metadata records.')
    data = database.read(index)
    if len(data)!=count*INDEX_ENTRY.size:
        raise Error('source_palm_record',f'The zTXT {kind} count does not match its index record.')
    entries = []
    for offset,title in INDEX_ENTRY.iter_unpack(data):
        if offset>length:
            raise Error('source_palm_record',f'A zTXT {kind} points past the end of the text.')
        entries.append({'text_byte_offset':offset,
                        'title':title.split(b'\0',1)[0].decode(encoding,errors='replace')})
    return entries


def read_ztxt(path,source,sha,title):
    database = Database(bounded_read(path),SIGNATURE,sha)
    header = database.read(0)
    if len(header)<32:
        raise Error('source_palm_header','The zTXT record header is incomplete.')
    version,count,length,record_size,nmarks,marks,nnotes,notes,flags,_,crc = HEADER.unpack_from(header)
    major,minor = divmod(version,256)
    if major!=1:
        raise Error('source_ztxt_version',f'zTXT version {major}.{minor} needs a readable text export.')
    if minor<40:
        # The original continuous format reserved these bytes as padding.
        flags = crc = 0
    if flags&~(RANDOM_ACCESS|NONUNIFORM):
        raise Error('source_ztxt_version',f'zTXT version {major}.{minor} or its record options need a readable text export.')
    if not length or not record_size or not 1<=count<database.count:
        raise Error('source_palm_header','The zTXT text length or record count is invalid.')
    if length>MAX_STRUCTURE:
        raise Error('source_structure_size','The decoded zTXT text exceeds the structured reading budget.')
    if nnotes and (notes+nnotes>=database.count or nmarks and marks>=notes):
        raise Error('source_palm_header','The zTXT annotation records overlap other content or extend past the database.')

    chunks = decode_body(database,count,length,record_size,flags,crc)
    document = plain_record_book(database,chunks,source,path.name,title,'ztxt')
    encoding = document.inventory[0]['encoding']
    bookmarks = read_index(database,marks,nmarks,count+1,length,encoding,'bookmark')
    annotations = read_index(database,notes,nnotes,count+1,length,encoding,'annotation')
    resources = document.metadata['source_resources']
    roles = {}
    if bookmarks:
        roles[marks] = ('palm_bookmark_index',None)
    if annotations:
        roles[notes] = ('palm_annotation_index',None)
        for index,annotation in enumerate(annotations,notes+1):
            roles[index] = ('palm_annotation',annotation)
    for index in range(count+1,database.count):
        role,entry = roles.get(index,('palm_auxiliary_record',None))
        name = entry.get('title') if entry else None
        asset = database.asset(index,role,name=name or None)
        if entry is not None:
            entry['location_id'] = asset['location_id']
        resources.append(asset)
    document.inventory.append({
        'kind':'palm_database','signature':SIGNATURE.decode('ascii'),'version':f'{major}.{minor}',
        'compression':'random_access' if flags&RANDOM_ACCESS else 'continuous',
        'text_records':count,'text_bytes':length,'compressed_crc32':crc or None,
        'bookmarks':len(bookmarks),'annotations':len(annotations),
    })
    if bookmarks or annotations:
        document.metadata['ztxt_reader_marks'] = {'bookmarks':bookmarks,'annotations':annotations}
    return document
