"""Read a compiled HTML Help publication in its authored contents order."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import platform
import shutil
import struct
import sys
import tempfile

from bs4 import BeautifulSoup

from .core import Error, digest, filehash
from .document_intake import MAX_STRUCTURE, DocumentBuilder, html_read
from .intake_helpers import convert
from .publication_package import bind_topic_links, local_target

READER_SHA256 = 'a604e6b43866ed96ebd485061bd1c7e0be018a69d56c5907da738bf83ec85672'
TOPIC_SUFFIXES = {'.htm','.html','.xhtml'}


@dataclass(frozen=True)
class Member:
    ordinal: int
    size: int
    name: str


def decoded(raw, encoding):
    if encoding:
        return raw.decode(encoding,errors='strict')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('windows-1252',errors='strict')


class Reader:
    def __init__(self,path,work,binary,encoding):
        self.path = path
        self.work = work
        self.binary = binary
        target = work/'index'
        self.run(work,'index',target)
        if target.stat().st_size>MAX_STRUCTURE:
            raise Error('source_structure_size','The compiled-help index exceeds the structural memory budget.')
        self.members = []
        self.names = {}
        for line in target.read_text(encoding='ascii').splitlines():
            ordinal, size, raw = line.split('\t')
            if int(ordinal)!=len(self.members):
                raise Error('source_chm_index','The help reader returned an incomplete member index.')
            name = decoded(bytes.fromhex(raw),encoding).removeprefix('/')
            member = Member(int(ordinal),int(size),name)
            self.members.append(member)
            self.names.setdefault(name.casefold(),[]).append(member)

    def run(self,work,mode,receipt,*args):
        convert(self.path,work,self.binary,[mode,'/input/'+self.path.name,'/work/'+receipt.name,*args],
                receipt,runtime=self.binary.parent)

    def find(self,name):
        matches = self.names.get(name.casefold(),())
        exact = [item for item in matches if item.name==name]
        if len(exact)==1:
            return exact[0]
        return matches[0] if len(matches)==1 else None

    @contextmanager
    def read(self, members):
        selected = {entry.ordinal:entry for entry in members if entry.size<=MAX_STRUCTURE}
        with tempfile.TemporaryDirectory(prefix='records-',dir=self.work) as temporary:
            work = Path(temporary)
            result = {}
            if selected:
                if sum(entry.size for entry in selected.values())>shutil.disk_usage(work).free:
                    raise Error('disk_space','The next compiled-help topics do not fit in local scratch space.')
                (work/'request').write_text(''.join(f'{index}\n' for index in sorted(selected)),encoding='ascii')
                receipt = work/'receipt'
                self.run(work,'read',receipt,'/work/request')
                for line in receipt.read_text(encoding='ascii').splitlines():
                    ordinal,status = line.split('\t')
                    ordinal = int(ordinal)
                    target = work/f'object-{ordinal}'
                    entry = selected.get(ordinal)
                    if (entry and status=='ok' and target.is_file() and not target.is_symlink()
                            and target.stat().st_size==entry.size):
                        result[ordinal] = target
            yield result

    def object(self, member):
        with self.read([member]) as records:
            if member.ordinal in records:
                return records[member.ordinal].read_bytes()
        return None


def system_records(data,encoding):
    if len(data)<4 or int.from_bytes(data[:4],'little') not in (2,3):
        raise ValueError('unsupported #SYSTEM record version')
    strings = {}
    cursor = 4
    while cursor<len(data):
        tag,length = struct.unpack_from('<HH',data,cursor)
        cursor += 4
        if cursor+length>len(data):
            raise ValueError('incomplete #SYSTEM record')
        if tag in (0,2,3,6):
            value = decoded(data[cursor:cursor+length].split(b'\0',1)[0],encoding).strip()
            if tag in strings and strings[tag]!=value:
                raise ValueError('competing #SYSTEM declarations')
            strings[tag] = value
        cursor += length
    return strings


def sitemap(raw,encoding):
    soup = BeautifulSoup(raw,'html.parser',from_encoding=encoding)
    for item in soup.find_all('object'):
        if str(item.get('type','')).casefold()!='text/sitemap':
            continue
        params = [(str(p.get('name','')).casefold(),p.get('value','')) for p in item.find_all('param',recursive=False)]
        label = next((value for name,value in params if name=='name'),'')
        refs = [value for name,value in params if name=='local']
        depth = sum(parent.name=='ul' for parent in item.parents)
        merged = any(name=='merge' for name,_ in params)
        yield {'label':label,'reference':refs[0] if len(refs)==1 else '',
               'depth':depth,'unresolved':len(refs)>1 or merged}


def batches(items):
    chunk,size = [],0
    for item in items:
        value = item['member'].size
        if chunk and size+value>MAX_STRUCTURE:
            yield chunk
            chunk,size = [],0
        chunk.append(item)
        size += value
    if chunk:
        yield chunk


def import_topics(reader,builder,items,encoding, *, auxiliary=False):
    for batch in batches(items):
        with reader.read(item['member'] for item in batch) as records:
            for item in batch:
                member = item['member']
                row = item['record']
                where = f"{member.name} (occurrence {row['occurrence']})"
                start = len(builder.doc.blocks)
                try:
                    if member.ordinal not in records:
                        raise Error('source_chm_topic','The embedded HTML topic could not be read in full.')
                    raw = records[member.ordinal].read_bytes()
                    row['sha256'] = digest(raw)
                    builder.chapter = row['label'] or PurePosixPath(member.name).stem
                    builder.chapter_id = 'chm:'+str(row['occurrence'])
                    html_read(raw,builder,where=where,metadata=False,fragment=item['anchor'],
                              encoding=encoding,collect_links=True)
                    if len(builder.doc.blocks)==start:
                        raise Error('source_chm_topic','The selected help topic has no readable content.')
                    row['status'] = 'companion' if auxiliary else 'represented'
                except Error as error:
                    row.update(status='failed',reason=str(error))
                    if not auxiliary:
                        builder.issue(str(error),where)
                blocks = builder.doc.blocks[start:]
                for block in blocks:
                    block.locator.update(path=member.name,occurrence=row['occurrence'],topic_entry=member.ordinal)
                    if auxiliary:
                        block.role = 'navigation'
                        block.locator['auxiliary'] = True
                if row['status']=='represented' and any(block.issue for block in blocks):
                    row['status'] = 'needs_review'
                row['blocks'] = [block.id for block in blocks]


def topic_links(reader, builder):
    listed = {row.get('topic_entry') for row in builder.doc.inventory}
    links, auxiliary = [],{}
    for block in builder.doc.blocks:
        if not (path := block.locator.get('path')):
            continue
        for span in block.locator.get('inline_semantics',()):
            if span['kind']!='hyperlink' or not (target:=local_target(span.get('href'),path)):
                continue
            name,anchor = target
            if PurePosixPath(name).suffix.casefold() not in TOPIC_SUFFIXES or not (member:=reader.find(name)):
                continue
            links.append((block,span,member.ordinal,anchor))
            if member.ordinal in listed or member.ordinal in auxiliary:
                continue
            row = {'occurrence':f'linked-{member.ordinal}','member':member.name,'topic_entry':member.ordinal,
                   'label':span['source'],'status':'selected','basis':'local hyperlink outside the contents'}
            auxiliary[member.ordinal] = {'member':member,'record':row,'anchor':None}
            builder.doc.inventory.append(row)
    return links,list(auxiliary.values())


def ordered_document(reader,builder,encoding):
    system = reader.find('#SYSTEM')
    raw = reader.object(system) if system else None
    if raw is None:
        raise Error('source_chm_contents','The compiled help system directory is missing or unreadable.')
    metadata = system_records(raw,encoding)
    builder.doc.title = metadata.get(3) or builder.doc.title
    toc_name = metadata.get(0) or (metadata[6]+'.hhc' if metadata.get(6) else '')
    tocs = [member for member in reader.members if PurePosixPath(member.name).suffix.casefold()=='.hhc']
    target = local_target(toc_name,'#SYSTEM')
    toc = reader.find(target[0]) if target else None
    if not toc and not metadata.get(0) and len(tocs)==1:
        toc = tocs[0]
    raw = reader.object(toc) if toc else None
    if raw is None:
        label = toc.name if toc else toc_name or 'the declared contents'
        raise Error('source_chm_contents',f'The compiled help contents {label} is missing or could not be read in full.',
                    'Provide a readable copy or an HTML/EPUB export with its original topic order.')
    builder.doc.metadata['chm_contents'] = {'member':toc.name,'entry':toc.ordinal,'sha256':digest(raw)}
    items = list(sitemap(raw,encoding))
    if not items:
        raise Error('source_chm_contents','The compiled help contents has no authored topic entries.')
    topics = []
    for ordinal, item in enumerate(items,1):
        row = {**item,'occurrence':ordinal,'status':'section'}
        builder.doc.inventory.append(row)
        if not item['reference'] and not item['unresolved']:
            continue
        target = local_target(item['reference'],toc.name)
        member = reader.find(target[0]) if target else None
        if item['unresolved'] or not member or PurePosixPath(member.name).suffix.casefold() not in TOPIC_SUFFIXES:
            reason = 'This contents entry names an unavailable or ambiguous help topic.'
            row.update(status='failed',reason=reason)
            topics.append({'record':row,'member':None,'anchor':None})
            continue
        row.update(status='selected',member=member.name,topic_entry=member.ordinal)
        topics.append({'record':row,'member':member,'anchor':target[1]})
    # Flush consecutive readable runs so missing occurrences stay in exact order.
    run = []
    for topic in topics:
        if topic['member']:
            run.append(topic)
            continue
        import_topics(reader,builder,run,encoding)
        run = []
        row = topic['record']
        block = builder.issue(row['reason'],f"{toc.name} entry {row['occurrence']}")
        row['blocks'] = [block.id]
    import_topics(reader,builder,run,encoding)
    if not topics:
        raise Error('source_chm_contents','The compiled help contents has no linked topics.')
    links, auxiliary = topic_links(reader,builder)
    import_topics(reader,builder,auxiliary,encoding,auxiliary=True)
    bind_topic_links(builder,links)
    represented = {row.get('topic_entry') for row in builder.doc.inventory}
    for member in reader.members:
        if member.ordinal not in represented and PurePosixPath(member.name).suffix.casefold() in TOPIC_SUFFIXES:
            builder.doc.inventory.append({'member':member.name,'topic_entry':member.ordinal,'status':'unlisted'})


def chm(path,source,sha,cache,title):
    binary = Path(__file__).with_name('intake_resident')/'chm-linux-x86_64'
    if sys.platform!='linux' or platform.machine().lower() not in ('x86_64','amd64') or not binary.is_file():
        raise Error('source_helper_unavailable','This platform build has no resident compiled-help reader.',
                    'Provide an ordered HTML or EPUB export.')
    if filehash(binary)!=READER_SHA256:
        raise Error('asset_integrity','The shipped compiled-help reader has changed.')
    builder = DocumentBuilder('chm',title,sha)
    builder.doc.metadata['decoder'] = {'engine':'CHMLib 0.40a','binary_sha256':READER_SHA256}
    with tempfile.TemporaryDirectory(prefix='hamr-chm-',dir=cache) as directory:
        try:
            reader = Reader(path,Path(directory),binary,source.encoding)
            ordered_document(reader,builder,source.encoding)
        except (UnicodeError,ValueError,LookupError,struct.error) as error:
            raise Error('source_chm_structure','The compiled help directory could not be decoded.',
                        'Try its source encoding with --encoding or supply a readable export.') from error
    return builder.doc
