"""The same intake path for plans, saved jobs, repairs and portable sources."""
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import shutil
import tempfile

from .core import Block, Document, Error, filehash, key, loadjson, syncdir, writejson
from .source_bundle import CHUNK, Source, describe, preview, stamp


def payload_document(payload):
    return Document(payload['title'],payload['format'],payload['source_sha256'],
                    [Block(**b) for b in payload['blocks']],payload['language'],payload['inventory'],
                    payload['warnings'],payload.get('metadata',{}))


@contextmanager
def materialized_member(path,kind,index,work):
    from .archive_intake import containers

    with containers(path,kind) as archive, tempfile.TemporaryDirectory(prefix='intake-member-',dir=work) as temp:
        entry = next((member for member in archive.members() if member.index==index),None)
        if entry is None:
            raise Error('source_member_missing', f'Archive member {index} is unavailable.')
        folder = Path(temp)
        # No member pathname is ever used as a destination.
        suffix = Path(entry.name).suffix
        output = folder/('member'+suffix if len(suffix)<16 and suffix.isascii() else 'member')
        remaining = shutil.disk_usage(folder).free
        if entry.size is not None and entry.size>remaining:
            raise Error('disk_space', f'Archive member {entry.name} cannot fit at {folder.parent}.')
        with archive.open_member(entry) as stream, output.open('xb') as file:
            while chunk:=stream.read(CHUNK):
                remaining -= len(chunk)
                if remaining<0:
                    raise Error('disk_space', f'Archive member {entry.name} exceeds available working space.')
                file.write(chunk)
        if entry.size is not None and output.stat().st_size!=entry.size:
            raise Error('unreadable_archive', f'The archive ended partway through {entry.name}.')
        yield output,entry


def parse(path,source,sha,cache,title):
    from .importers import ADAPTERS, MAX_INPUT, markdown, plain

    if source.kind.endswith('-member'):
        work = cache if cache else None
        with materialized_member(path,source.kind,source.entry,work) as (member,entry):
            route = describe(member,format=source.route_override,offline=source.offline)
            if route.kind in ('zip-member','tar-member','libarchive-member','directory','manifest'):
                raise Error('source_member_nested','Select an unpacked book for this nested archive.')
            doc = parse(member,route,filehash(member),cache,Path(entry.name).stem)
            doc.source_sha256 = sha
            for item in doc.blocks:
                item.locator.update(member=entry.name,archive_entry=entry.index,
                                    location_id=key([sha,entry.index,item.id])[:20])
            doc.metadata['archive_entry'] = {'index':entry.index,'name':entry.name}
            return doc
    if source.route=='pdf':
        return ADAPTERS['.pdf'](path,ocr_cache=cache/'pdf-observations' if cache else None)
    if source.route=='epub':
        return ADAPTERS['.epub'](path)
    if source.route=='djvu':
        from .djvu_intake import djvu

        return djvu(path,source,sha,cache,title)
    if source.route=='scan':
        from .scan_intake import scan

        return scan(path,source,sha,cache/'scan-observations' if cache else None,title)
    if source.route in ('markdown','text'):
        if path.stat().st_size>MAX_INPUT:
            raise Error('input_limit','This text reader exceeds its current decoded size bound.',
                        'Split the document at a chapter boundary.')
        return (markdown if source.route=='markdown' else plain)(path.read_bytes())
    from .document_intake import read_document

    return read_document(path,source,sha,cache,title)


def inspect_saved(path,source,cache=None,*,title=None):
    from .importers import IMPORT_REVISION

    path = Path(path)
    source = Source(**source) if isinstance(source,dict) else source
    if cache:
        cache.mkdir(exist_ok=True,parents=True)
    before = stamp(path.stat())
    sha = filehash(path)
    target = None
    # Native byte readers are deterministic. Visual and external readers keep
    # their own effective-engine receipts and re-evaluate unresolved regions.
    if cache and source.kind=='file' and source.route in ('epub','text','markdown','pdf'):
        import importlib.metadata
        dependencies = {name:importlib.metadata.version(name) for name in (
            'beautifulsoup4','lxml','markdown-it-py','mdit-py-plugins','pypdfium2','Pillow','tinycss2','webencodings')}
        directory = Path(__file__).parent
        implementation = {p.name:filehash(p) for p in directory.glob('*.py')
                          if p.name in ('importers.py','pronunciation.py','markdown_notes.py','text_source.py',
                                        'text_evidence.py','intake.py')
                          or p.name.startswith(('epub_','pdf_'))}
        identity = key([sha,source.route,IMPORT_REVISION,dependencies,implementation])
        target = cache/(identity+'.json')
        if target.is_symlink():
            raise Error('unsafe_path','Extraction cache entry cannot be a symlink.')
        try:
            envelope = loadjson(target)
            payload = envelope['document']
            if envelope['document_sha256']==key(payload) and payload['source_sha256']==sha:
                return payload_document(payload)
        except (OSError,ValueError,KeyError,TypeError):
            pass
    doc = parse(path,source,sha,cache,title)
    if stamp(path.stat())!=before or doc.source_sha256!=sha:
        raise Error('source_changed', 'The source changed during import.',
                    'Repeat from the original stable file or a saved Hamr source bundle.')
    if not doc.blocks:
        raise Error('empty_document','Document contains no narratable material.')
    visual = [observation for page in doc.inventory if isinstance(page,dict)
              for observation in page.get('visual_observations',[])]
    if target and all(observation['status']=='verified_blank' for observation in visual):
        payload = doc.json()
        writejson(target,{'document_sha256':key(payload),'document':payload,'import_revision':IMPORT_REVISION})
    source_kind = source.kind
    if source_kind!='file' or source.route not in ('text','markdown','pdf','epub'):
        doc.metadata.setdefault('intake',{'kind':source_kind,'route':source.route})
    return doc


def inspect_source(path,cache=None,**options):
    path = Path(path).absolute()
    if cache is None:
        source = describe(path,**options)
        if source.kind not in ('manifest','directory'):
            from .source_bundle import _local_source
            return inspect_saved(_local_source(path,options),source,title=path.stem)
        with tempfile.TemporaryDirectory(prefix='hamr-source-') as temp:
            return inspect_source(path,Path(temp),**options)
    cache.mkdir(exist_ok=True,parents=True)
    with preview(path,cache,**options) as (snapshot,source):
        return inspect_saved(snapshot,source,cache,title=path.stem)


def extract_location(book,location,output):
    from .archive_intake import containers
    from .scan_intake import bundle_index
    from .source_bundle import copy_snapshot

    book = Path(book)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise Error('output_collision', f'The destination already exists: {output}.')
    companion = loadjson(book/'companion.json')
    source_name = companion['original_source']
    if Path(source_name).name!=source_name:
        raise Error('unsafe_source_member','Invalid original-source filename in the companion.')
    original = book/source_name
    if original.is_symlink() or filehash(original)!=companion['source_sha256']:
        raise Error('snapshot_changed','The published original has changed.')
    loc = companion.get('source_locations',{}).get(location)
    if not loc:
        raise Error('source_location', f'The finished book has no source location {location}.')
    kind = companion.get('source_kind','file')
    if kind=='file':
        copy_snapshot(original,output)
        return {'output':str(output),'location':loc}
    try:
        with tempfile.NamedTemporaryFile(prefix='.hamr-source-',dir=output.parent,delete=False) as stream:
            staged = Path(stream.name)
            with containers(original,kind) as archive:
                if kind=='bundle':
                    item = bundle_index(archive)['members'].get(loc['member'])
                    if not item:
                        raise Error('source_member_missing','The portable source has no matching member.')
                    read = archive.open('blobs/'+item['sha256'])
                    expected = item['sha256']
                else:
                    item = next((item for item in archive.members() if item.index==loc.get('archive_entry')),None)
                    if not item or item.name!=loc['member']:
                        raise Error('source_member_missing','The archive has no matching original member.')
                    read = archive.open_member(item)
                    expected = None
                with read as member:
                    shutil.copyfileobj(member,stream,CHUNK)
                stream.flush()
                os.fsync(stream.fileno())
            if expected and filehash(staged)!=expected:
                raise Error('snapshot_changed','The source member does not match its recorded hash.')
        os.link(staged,output)
        syncdir(output.parent)
    finally:
        if 'staged' in locals():
            staged.unlink(missing_ok=True)
    return {'output':str(output),'location':loc}


def capabilities():
    """Describe registered readers and this host's optional conversion paths."""
    import platform
    import sys
    from .pdf_ocr import resident_binary
    from .source_bundle import NATIVE
    from .intake_helpers import CALIBRE_BYTES,CALIBRE_UNPACKED

    arch = platform.machine().lower()
    x64 = arch in ('x86_64','amd64')
    linux = sys.platform.startswith('linux') and x64
    assets = Path(__file__).with_name('intake_resident')
    external = {
        'office-legacy':{'ready':bool(shutil.which('libreoffice') and shutil.which('bwrap')),
                         'source':'verified local Writer conversion'},
        'legacy-ebook':{'managed_available':linux and bool(shutil.which('bwrap')),
                        'download_bytes':CALIBRE_BYTES,'unpacked_bytes':CALIBRE_UNPACKED},
        'djvu':{'ready':linux and (assets/'djvu-linux-x86_64').is_file() and bool(shutil.which('bwrap'))},
        'mobi':{'ready':(linux and (assets/'mobitool-linux-x86_64').is_file()
                         or os.name=='nt' and x64 and (assets/'mobitool-windows-x86_64.exe').is_file()
                         or bool(shutil.which('mobitool')))},
    }
    return {'formats':sorted({'scan',*NATIVE.values()}),
            'registered_suffixes':dict(sorted(NATIVE.items())),
            'source_kinds':['file','directory','page-manifest','archive'],
            'archives':{'zip':'built in','tar':'built in','7z':'local libarchive','rar':'local libarchive'},
            'resident_english_ocr':resident_binary() is not None,'specialist':external,
            'portable_originals':True,'entry_flag':'--entry','offline_flag':'--offline',
            'manifest_schema':'hamr.page-sequence.v1'}
