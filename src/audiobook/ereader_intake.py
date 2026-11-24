"""Prepare unprotected eReader record books with the resident PML reader."""
from bisect import bisect_right
from urllib.parse import unquote

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read
from .palm_database import Database, Locations, MAX_PALM_RECORD, text_record, text_records
from .pml_intake import Reading, decode, encoding_for, image_name
from .publication_package import bind_resources


class Edition:
    def __init__(self,database):
        self.database = database
        self.header = database.read(0)
        self.codec = self.field(0)
        if self.codec in (260,272):
            raise Error('protected_source','This eReader source is encrypted.',
                        'Supply an unencrypted PML or EPUB export.')
        if len(self.header)!=132:
            raise Error('source_palm_header','This eReader header layout needs a readable PML export.')
        if self.codec not in (2,10):
            raise Error('source_palm_compression',f'eReader compression {self.codec} needs a readable PML export.')
        self.non_text = self.field(12)
        if not 2<=self.non_text<=database.count:
            raise Error('source_palm_header','The eReader header points outside the text record directory.')
        self.images = self.section(40,20)
        self.footnotes = self.section(48,28)
        self.sidebars = self.section(50,30)

    def field(self,offset):
        return int.from_bytes(self.header[offset:offset+2],'big')

    def section(self,offset_field,count_field):
        start,count = self.field(offset_field),self.field(count_field)
        if count and not self.non_text<=start<start+count<=self.database.count:
            raise Error('source_palm_header','An eReader image or note section points outside the record directory.')
        return range(start,start+count)

    def assets(self):
        assets = {}
        for index in self.images:
            raw = self.database.read(index)
            if len(raw)<70 or raw[:4]!=b'PNG ' or raw[62:70]!=b'\x89PNG\r\n\x1a\n':
                raise Error('source_palm_asset',f'eReader record {index} does not contain its declared PNG image.')
            name = raw[4:36].split(b'\0',1)[0].decode('cp1252',errors='replace')
            if not image_name(name) or name.casefold() in assets:
                raise Error('source_palm_asset',f'eReader record {index} has an invalid or duplicate image name.')
            assets[name.casefold()] = self.database.asset(index,'asset',prefix=62,name=name)
        return assets

    def note_items(self,kind,section,encoding,budget):
        if not section:
            return
        raw = self.database.read(section.start)
        identifiers = note_ids(raw)
        if len(identifiers)!=len(section)-1:
            raise Error('source_palm_note',f'The eReader {kind} index and its record count disagree.')
        for index,identifier in zip(section[1:],identifiers):
            label = decode(identifier,encoding)
            if not label or any(char in label for char in '<>"\r\n'):
                raise Error('source_palm_note',f'eReader {kind} record {index} has an invalid identifier.')
            payload = text_record(self.database,index,self.codec,min(MAX_PALM_RECORD,budget))
            budget -= len(payload)
            yield index,label,decode(payload,encoding),len(payload)

    def metadata(self):
        if not self.field(24):
            return {}
        index = self.field(44)
        if index<self.non_text:
            raise Error('source_palm_header','The eReader metadata points into the text records.')
        raw = self.database.read(index).split(b'\0')
        values = (part.decode('cp1252',errors='replace').strip() for part in raw)
        return {field:value for field,value in zip(('title','author','copyright','publisher','isbn'),values) if value}


def note_ids(data):
    if not data.startswith(b'\0\1'):
        return [item for item in data.split(b'\0') if item]
    labels = []
    position = 0
    while position<len(data):
        if data[position:position+2]!=b'\0\1' or position+2>=len(data):
            raise Error('source_palm_note','The length-prefixed eReader note index is incomplete.')
        length = data[position+2]
        start = position+3
        end = start+length
        if not length or end>=len(data) or data[end]!=0:
            raise Error('source_palm_note','An eReader note index identifier has an invalid length.')
        labels.append(data[start:end])
        position = end+1
    return labels


def read_ereader(path,source,sha,title):
    database = Database(bounded_read(path),b'PNRdPPrs',sha)
    edition = Edition(database)
    chunks = text_records(database,range(1,edition.non_text),edition.codec,MAX_PALM_RECORD)
    raw = b''.join(chunk for _,chunk in chunks)
    encoding = encoding_for(raw,source.encoding)
    body = decode(raw,encoding)
    locations = Locations(chunks,encoding)
    parts = [body]
    boundaries = []
    notes = []
    budget = MAX_STRUCTURE-len(raw)
    length = len(body)
    for kind,section in (('footnote',edition.footnotes),('sidebar',edition.sidebars)):
        for index,identifier,text,size in edition.note_items(kind,section,encoding,budget):
            budget -= size
            markup = f'\n<{kind} id="{identifier}">\n{text}\n</{kind}>\n'
            notes.append((index,text))
            boundaries.append(length)
            parts.append(markup)
            length += len(markup)
    published = edition.metadata()
    builder = DocumentBuilder('ereader',published.pop('title',database.title or title),sha)
    doc = builder.doc
    doc.metadata.update(published)
    assets = edition.assets()
    Reading(''.join(parts),source.member_name or path.name,builder).read()

    for block in doc.blocks:
        loc = block.locator
        if limits:=loc.get('character_range'):
            start,end = limits
            if start<len(body):
                loc['palm_records'] = locations.overlaps(start,min(end,len(body)))
            elif (note_index:=bisect_right(boundaries,start)-1)>=0:
                index,text = notes[note_index]
                loc['palm_records'] = [index]
                if block.role=='note':
                    loc['exact'] = text
        bind_resources(block,lambda ref:assets.get(unquote(ref['href']).casefold()))
    doc.inventory.append({'kind':'palm_database','signature':'PNRdPPrs','compression':edition.codec,
                          'text_records':len(chunks),'image_records':len(assets),'note_records':len(notes)})
    doc.metadata['source_resources'] = ([database.asset(index,'palm_text_record') for index,_ in chunks] +
                                        [database.asset(index,'palm_note_record') for index,_ in notes] +
                                        list(assets.values()))
    return doc
