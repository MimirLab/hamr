"""Send an exact PalmDOC text stream to the common plain-text reader."""
from struct import Struct

from .core import Error
from .document_intake import MAX_STRUCTURE, bounded_read
from .palm_database import Database, plain_record_book, text_records


PALMDOC_HEADER = Struct('>HHIHHI')


def read_palmdoc(path,source,sha,title):
    database = Database(bounded_read(path),b'TEXtREAd',sha)
    header = database.read(0)
    if len(header)<PALMDOC_HEADER.size:
        raise Error('source_palm_header','The PalmDOC text header is incomplete.')
    codec,_,length,count,record_limit,position = PALMDOC_HEADER.unpack_from(header)
    if codec not in (1,2,258):
        raise Error('source_palm_compression',f'PalmDOC compression {codec} needs a readable text export.')
    if not length or not record_limit or not 1<=count<database.count:
        raise Error('source_palm_header','The PalmDOC text length or record count is invalid.')
    if length>MAX_STRUCTURE:
        raise Error('source_structure_size','The decoded PalmDOC text exceeds the structured reading budget.')
    chunks = text_records(database,range(1,count+1),codec,record_limit)
    if sum(len(data) for _,data in chunks)!=length:
        raise Error('source_palm_record','The PalmDOC text does not match its declared decoded length.')
    document = plain_record_book(database,chunks,source,path.name,title,'palmdoc')
    document.inventory.append({'kind':'palm_database','signature':'TEXtREAd','compression':codec,
                               'stored_position':position,'text_records':count,'text_bytes':length})
    return document
