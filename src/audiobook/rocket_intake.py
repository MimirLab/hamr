"""Read unencrypted Rocket eBooks into the shared HTML publication model."""

from dataclasses import dataclass
from pathlib import PurePosixPath
from struct import Struct
from urllib.parse import unquote_to_bytes
import codecs
import zlib

from bs4 import BeautifulSoup, SoupStrainer

from .binary_source import byte_asset
from .core import Error, key
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, html_read
from .publication_package import bind_resources, bind_topic_links, local_target
from .source_bundle import safe_name

MAGIC = b'\xb0\x0c\xb0\x0c'
UINT = Struct('<I')
PAIR = Struct('<II')
ENTRY = Struct('<32sIII')
COMPRESSION_BLOCK = 4096
MAX_RECORDS = 10000
HTML_SUFFIXES = {'.htm','.html','.xhtml'}


@dataclass(frozen=True)
class Record:
    index: int
    name: str
    offset: int
    length: int
    flags: int

    @property
    def end(self):
        return self.offset+self.length


def record_name(data):
    raw = unquote_to_bytes(data.split(b'\0',1)[0])
    try:
        name = raw.decode('utf-8')
    except UnicodeDecodeError:
        name = raw.decode('cp1252',errors='replace')
    return safe_name(name)


def records(data):
    if len(data)<32 or data[:4]!=MAGIC or data[4:6]!=b'\x02\x00':
        raise Error('source_rocket_header','The original does not identify a Rocket eBook version 2.')
    directory,size = PAIR.unpack_from(data,24)
    if size!=len(data) or not 32<=directory<=size-UINT.size:
        raise Error('source_rocket_header','The Rocket eBook has an invalid file length or directory offset.')
    count, = UINT.unpack_from(data,directory)
    start = directory+UINT.size
    end = start+count*ENTRY.size
    if not 1<=count<=MAX_RECORDS or end>size:
        raise Error('source_rocket_header','The Rocket eBook record directory is incomplete or exceeds its entry limit.')
    result = []
    names = set()
    for index,(raw,length,offset,flags) in enumerate(ENTRY.iter_unpack(data[start:end])):
        if flags&1:
            raise Error('protected_source','This Rocket eBook contains protected records.',
                        'Open an unprotected EPUB, HTML or text export of the book.')
        name = record_name(raw)
        if name in names or flags not in (0,2,8):
            raise Error('source_rocket_record',f'The Rocket eBook has a duplicate or unsupported record: {name}.')
        record = Record(index,name,offset,length,flags)
        if offset<32 or record.end>size or offset<end and record.end>directory:
            raise Error('source_rocket_record',f'The Rocket eBook record has an invalid byte range: {name}.')
        names.add(name)
        result.append(record)
    occupied = 32
    for record in sorted((item for item in result if item.length),key=lambda item:item.offset):
        if record.offset<occupied:
            raise Error('source_rocket_record',f'The Rocket eBook records overlap at {record.name}.')
        occupied = record.end
    return result


def decode_chunks(data,name):
    def invalid():
        return Error('source_rocket_record',f'The compressed Rocket eBook record is incomplete or invalid: {name}.')

    if len(data)<PAIR.size:
        raise invalid()
    count,total = PAIR.unpack_from(data)
    if total>MAX_STRUCTURE:
        raise Error('source_structure_size',f'The decoded Rocket eBook record exceeds its reading budget: {name}.')
    begin = PAIR.size
    offset = begin+UINT.size*count
    if count!=(total+COMPRESSION_BLOCK-1)//COMPRESSION_BLOCK or offset>len(data):
        raise invalid()
    chunks = []
    for length, in UINT.iter_unpack(data[begin:offset]):
        expected = min(COMPRESSION_BLOCK,total)
        end = offset+length
        if not length or end>len(data):
            raise invalid()
        decoder = zlib.decompressobj()
        try:
            chunk = decoder.decompress(data[offset:end],expected+1)
        except zlib.error as exc:
            raise invalid() from exc
        if len(chunk)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise invalid()
        chunks.append(chunk)
        total -= len(chunk)
        offset = end
    if offset!=len(data):
        raise invalid()
    return b''.join(chunks)


def info_fields(raw,encoding):
    if len(raw)>1024*1024:
        raise Error('source_structure_size','The Rocket eBook metadata record exceeds its reading budget.')
    try:
        lines = raw.decode(encoding or 'cp1252').splitlines()
    except UnicodeError as exc:
        raise Error('source_rocket_info','The Rocket eBook metadata cannot be decoded with this encoding.') from exc
    values = {}
    for line in lines:
        key,separator,value = line.partition('=')
        key = key.strip().upper()
        if separator and key in ('BODY','MENUMARK','TITLE','AUTHOR','TITLE_LANGUAGE'):
            stripped = value.strip()
            if key in values and values[key]!=stripped:
                raise Error('source_rocket_info',f'The Rocket eBook has conflicting {key} fields.')
            values[key] = stripped
    return values


def topic_references(raw,part,encoding):
    markup = BeautifulSoup(raw,'html.parser',parse_only=SoupStrainer(['a','link']),from_encoding=encoding)
    for tag in markup.find_all(['a','link'],href=True):
        if target:=local_target(tag['href'],part):
            yield target[0],'next' in {value.casefold() for value in tag.get('rel',())},tag.name=='a'


def reading_order(topics,root,menu,raw,encoding):
    if len(topics)<=1:
        return [root],'declared body'
    expected = set(topics)-{root}
    if menu in raw:
        authored = list(dict.fromkeys(target for target,_,visible in topic_references(raw[menu],menu,encoding)
                                      if visible and target in expected))
        if set(authored)==expected:
            return [root,*authored],'declared body and menu'
    order = [root]
    while expected and order[-1] in raw:
        refs = [(target,explicit) for target,explicit,visible in topic_references(raw[order[-1]],order[-1],encoding)
                if target in expected and (visible or explicit)]
        candidates = {target for target,explicit in refs if explicit} or {target for target,_ in refs}
        if len(candidates)!=1:
            break
        target = candidates.pop()
        order.append(target)
        expected.remove(target)
    if not expected:
        return order,'declared body and complete local continuation links'
    return [root,*(topic for topic in topics if topic!=root)],'declared body and saved directory order'


def read_rocket(path,source,sha,title):
    data = bounded_read(path)
    members = {item.name:item for item in records(data)}
    encoding = source.encoding
    if encoding:
        try:
            codecs.lookup(encoding)
        except LookupError as exc:
            raise Error('source_encoding',f'Unknown Rocket eBook encoding: {encoding}.') from exc
    descriptors = [item for item in members.values() if item.flags==2]
    if len(descriptors)!=1:
        raise Error('source_rocket_info','The Rocket eBook needs one readable information record.')
    descriptor, = descriptors
    fields = info_fields(data[descriptor.offset:descriptor.end],encoding)
    pages = [name for name in members if PurePosixPath(name).suffix.casefold() in HTML_SUFFIXES
             and members[name].flags!=2]
    body = local_target(fields.get('BODY'),descriptor.name)
    root = body[0] if body else pages[0] if len(pages)==1 and not fields.get('BODY') else None
    if root not in pages:
        raise Error('source_rocket_info','The declared Rocket eBook body is missing or unreadable.',
                    'Open a complete original or a readable HTML or EPUB export.')
    menu = local_target(fields.get('MENUMARK'),descriptor.name)
    menu = menu[0] if menu and menu[0]!=root and menu[0] in pages else None

    raw,problems = {},{}
    remaining = MAX_STRUCTURE
    resources = {}
    folded_names = {}
    for name in members:
        folded = name.casefold()
        folded_names[folded] = name if folded not in folded_names else None
    view = memoryview(data)
    for name,member in members.items():
        suffix = PurePosixPath(name).suffix.casefold()
        role = ('descriptor' if member.flags==2 else 'text' if name in pages else
                'asset' if suffix in ('.png','.wav') else 'index')
        resources[name] = {'name':name,'path':name,'role':role,'topic_entry':member.index,
                           'resource_label':name,'status':'preserved','byte_range':[member.offset,member.end],
                           'location_id':key([sha,member.index,name])[:20],
                           'binary_asset':byte_asset(view,member.offset,member.end,
                                                     codec='rocket-zlib' if member.flags==8 else None)}
        if role=='text':
            payload = view[member.offset:member.end]
            try:
                raw[name] = decode_chunks(payload,name) if member.flags==8 else payload.tobytes()
            except Error as exc:
                problems[name] = str(exc)
                continue
            remaining -= len(raw[name])
            if remaining<0:
                raise Error('source_structure_size','The decoded Rocket eBook exceeds its combined HTML reading budget.')

    order,basis = reading_order([name for name in pages if name!=menu],root,menu,raw,encoding)
    builder = DocumentBuilder('rocket',fields.get('TITLE') or title,sha)
    doc = builder.doc
    doc.metadata['rocket_contents'] = {'body':root,'basis':basis,'order':order}
    if fields.get('AUTHOR'):
        doc.metadata['authors'] = [fields['AUTHOR']]
    links = []
    for occurrence,name in enumerate([*order,*([menu] if menu else [])],1):
        record = resources[name]
        def image(reference):
            if target:=local_target(reference['href'],name):
                path = target[0]
                return resources.get(path if path in resources else folded_names.get(path.casefold()))
            return None

        start = len(doc.blocks)
        if name in raw:
            html_read(raw[name],builder,where=name,metadata=name==root,encoding=encoding,
                      collect_links=True,collect_resources=True)
        if start==len(doc.blocks):
            builder.issue(problems.get(name,'This Rocket eBook topic has no readable text.'),name)
        status = 'companion' if name==menu else 'needs_review' if any(
            b.issue for b in doc.blocks[start:]) else 'represented'
        for block in doc.blocks[start:]:
            block.locator.update(path=name,topic_entry=members[name].index,occurrence=occurrence,
                                 binary_asset=record['binary_asset'],byte_range=record['byte_range'])
            if name==menu and not block.issue:
                block.role = 'navigation'
            bind_resources(block,image)
            for span in block.locator.get('inline_semantics',()):
                if span['kind']=='hyperlink' and (target:=local_target(span.get('href'),name)):
                    if target[0] in raw:
                        links.append((block,span,members[target[0]].index,target[1]))
                        label = ' '.join(span['source'].casefold().split()).rstrip('.')
                        next_topic = occurrence<len(order) and target[0]==order[occurrence]
                        previous_topic = 1<occurrence<=len(order) and target[0]==order[occurrence-2]
                        if block.role=='body' and block.source.strip()==span['source'].strip() and (
                                next_topic and (label in ('next','next chapter') or label.startswith('continue to '))
                                or previous_topic and label in ('previous','previous chapter')):
                            block.role = 'navigation'
        record['status'] = status
    bind_topic_links(builder,links)
    doc.title = fields.get('TITLE') or doc.title
    doc.language = fields.get('TITLE_LANGUAGE') or doc.language
    doc.metadata['source_resources'] = list(resources.values())
    doc.inventory.extend(resources.values())
    return doc
