"""Discover and freeze source bytes independently of their narration."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from zipfile import BadZipFile, ZIP_STORED, ZipFile, ZipInfo

from .core import Error, filehash, key, syncdir

IMAGE_SUFFIXES = frozenset(('.jpg', '.jpeg', '.jfif', '.png', '.tif', '.tiff', '.bmp',
                            '.pnm', '.pbm', '.pgm', '.ppm', '.jp2', '.j2k', '.webp', '.gif'))
SIDECAR_SUFFIXES = frozenset(('.xml', '.hocr', '.alto', '.page', '.tsv'))
SIDECAR_ENDINGS = ('.alto.xml','.page.xml','.abbyy.xml','.hocr','.tsv')
NATIVE = {
    '.txt':'text', '.text':'text', '.asc':'text', '.nfo':'text', '.md':'markdown', '.markdown':'markdown', '.epub':'epub', '.pdf':'pdf',
    '.textpack':'textbundle',
    '.djvu':'djvu', '.djv':'djvu', '.chm':'chm', '.html':'html', '.htm':'html', '.xhtml':'html', '.docx':'docx', '.odt':'odt',
    '.abw':'xml-book', '.dbk':'xml-book', '.fb2':'fb2', '.fb3':'fb3', '.rtf':'rtf', '.dtbook':'xml-book', '.xml':'xml-book',
    '.jats':'xml-book', '.tei':'xml-book', '.nimas':'xml-book',
    '.csv':'csv', '.xlsx':'xlsx', '.pptx':'pptx', '.opml':'outline', '.svg':'outline', '.tsv':'csv', '.ipynb':'notebook', '.mhtml':'mhtml', '.mht':'mhtml',
    '.eml':'mail', '.tcr':'tcr', '.guide':'amigaguide', '.mobi':'mobi', '.prc':'mobi', '.azw':'mobi', '.azw3':'mobi', '.azw4':'mobi', '.lrf':'legacy-ebook', '.lit':'legacy-ebook', '.doc':'office-legacy', '.wps':'office-legacy', '.wpd':'office-legacy',
    '.sxw':'office-legacy', '.hwp':'office-legacy', '.srt':'subtitles', '.vtt':'subtitles',
    '.rst':'rst', '.rest':'rst',
}
PAGE_SCHEMAS = frozenset(('hamr.page-sequence.v1', 'hamr.page-sequence.design.v1'))
PACKAGE_FOLDERS = frozenset(('textbundle','mdbook','gitbook','sphinx','scrivener'))
BUNDLE_SCHEMA = 'hamr.source-bundle.v1'
CHUNK = 1024*1024
MAX_MANIFEST = 16*CHUNK


@dataclass(frozen=True)
class Source:
    route: str
    kind: str = 'file'
    entry: int | None = None
    route_override: str | None = None
    offline: bool = False
    encoding: str | None = None
    member_name: str | None = None

    def json(self):
        return asdict(self)


def natural(name):
    folded = name.casefold()
    return tuple((1, int(part)) if part.isascii() and part.isdigit() else (0, part)
                 for part in re.split(r'([0-9]+)', folded)), folded, name


def image_magic(prefix):
    return (prefix.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'II*\0', b'MM\0*',
                               b'II+\0', b'MM\0+', b'GIF87a', b'GIF89a',
                               b'\0\0\0\x0cjP  \r\n\x87\n', b'\xffO\xffQ'))
            or prefix.startswith(b'RIFF') and prefix[8:12]==b'WEBP')


def probe(prefix, name):
    suffix = Path(name).suffix.lower()
    if prefix.startswith(b'AT&TFORM') and prefix[12:16] in (b'DJVU',b'DJVM'):
        return 'djvu'
    if prefix.startswith(b'ITSF') and len(prefix)>=8 and int.from_bytes(prefix[4:8],'little') in (2,3):
        return 'chm'
    if suffix=='.chm':
        return None
    if prefix.lstrip().startswith(b'%PDF-'):
        return 'pdf'
    if prefix.startswith(b'!!8-Bit!!'):
        return 'tcr'
    if prefix.lower().startswith(b'@database '):
        return 'amigaguide'
    if prefix.startswith((b'L\0R\0F\0',b'ITOLITLS')):
        return 'legacy-ebook'
    if suffix in ('.lrf','.lit'):
        return None
    if suffix in IMAGE_SUFFIXES or image_magic(prefix):
        return 'scan'
    if prefix[60:68] in (b'BOOKMOBI',b'TEXtREAd'):
        return 'mobi'
    if prefix.lstrip().startswith(b'{\\rtf'):
        return 'rtf'
    return NATIVE.get(suffix)


def read_manifest(path):
    if path.stat().st_size>MAX_MANIFEST:
        raise Error('source_manifest_size', 'The page order manifest exceeds 16 MiB.')
    return manifest_data(path.read_bytes(),path.name)


def manifest_data(raw,filename):
    if PurePosixPath(filename).suffix.lower() in ('.xml','.mets'):
        from .archival_manifest import mets
        value = {'schema':'hamr.page-sequence.v1',**mets(raw)}
    else:
        try:
            value = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise Error('source_manifest', f'Cannot read the page order in {filename}.') from exc
        if isinstance(value,dict) and value.get('type')=='Manifest':
            from .archival_manifest import iiif
            value = {'schema':'hamr.page-sequence.v1',**iiif(value)}
    if not isinstance(value,dict):
        raise Error('source_manifest', f'Cannot read the page order in {filename}.')
    schema = value.get('schema')
    if (not isinstance(schema,str) or schema not in PAGE_SCHEMAS or not isinstance(value.get('pages'),list)
            or not value['pages']):
        raise Error('source_manifest', 'Use a hamr.page-sequence.v1 manifest with a nonempty pages list.')
    return value


def safe_name(name):
    if not isinstance(name,str) or not name or '\\' in name or '\0' in name or ':' in name:
        raise Error('unsafe_source_member', f'Invalid relative source path {name!r}.')
    if any(part in ('','.','..') for part in name.split('/')):
        raise Error('unsafe_source_member', f'Invalid relative source path {name!r}.')
    return name


def relative_path(root, name):
    path = root
    for part in safe_name(name).split('/'):
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise Error('unsafe_source_member', f'Source member {name} follows a link.')
    return path


def relative_file(root, name):
    path = relative_path(root,name)
    if not path.is_file():
        raise Error('source_member_missing', f'Source member is missing: {name}.')
    return path


def publication_folder(path):
    if path.suffix.casefold()=='.textbundle':
        return 'textbundle'
    if path.suffix.casefold()=='.scriv':
        return 'scrivener'
    if (path/'Files').is_dir():
        from .scrivener_intake import folder_binders

        if folder_binders(path):
            return 'scrivener'
    from .gitbook_intake import CONFIG as GITBOOK_CONFIG, SITE as GITBOOK_SITE, folder_marker
    from .mdbook_intake import folder_book

    if any((path/name).is_file() for name in (GITBOOK_CONFIG,GITBOOK_SITE)):
        return 'gitbook'
    if folder_book(path) is not None:
        return 'mdbook'
    if (path/'conf.py').is_file():
        from .sphinx_intake import folder_book as sphinx_book

        if sphinx_book(path) is not None:
            return 'sphinx'
    return 'gitbook' if folder_marker(path) else None


def directory_members(root):
    pages, other = [], []
    for folder, directories, files in os.walk(root, followlinks=False):
        here = Path(folder)
        directories[:] = [name for name in sorted(directories) if not name.startswith('.')
                          and name != '__MACOSX' and not (here/name).is_symlink()
                          and not (here/name).is_junction()]
        packages = {name:route for name in directories if (route:=publication_folder(here/name))}
        other.extend(((here/name).relative_to(root).as_posix(),route) for name,route in packages.items())
        directories[:] = [name for name in directories if name not in packages]
        for name in files:
            if name.startswith('.'):
                continue
            path = here/name
            relative = path.relative_to(root).as_posix()
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                pages.append(relative)
            elif suffix not in SIDECAR_SUFFIXES and suffix in NATIVE and not path.is_symlink():
                other.append((relative,NATIVE[suffix]))
    return sorted(pages,key=natural), sorted(other,key=lambda row:natural(row[0]))


def choose(candidates, entry):
    if not candidates:
        raise Error('unsupported_format', 'No readable book or page image was found in this source.')
    if len(candidates)==1 and entry is None:
        return candidates[0][0]
    for ident, name in candidates:
        if entry==ident:
            return ident
    detail = '\n'.join(f'  {ident}: {name}' for ident,name in candidates)
    error = Error('source_selection', 'Choose the publication in this source.\n'+detail,
                  'Repeat the command with --entry ID.')
    error.candidates = [{'id':ident,'name':name} for ident,name in candidates]
    raise error


def zip_members(archive):
    records = []
    for index, info in enumerate(archive.infolist(),1):
        name = info.filename
        if info.orig_filename!=name:
            raise Error('unsafe_source_member', 'An archive member name contains a disallowed byte.')
        if info.is_dir():
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            raise Error('unsafe_source_member', f'The archive contains a link: {name!r}.')
        safe_name(name)
        if info.flag_bits & 1:
            raise Error('protected_source', f'An archive member is encrypted: {name}.',
                        'Provide an unencrypted local export.')
        records.append((index,info))
    return records


def zip_source(path, entry, override):
    from .archive_intake import ZipMembers

    try:
        with ZipMembers(path) as archive:
            records = zip_members(archive)
            names = {info.filename for _,info in records}
            if 'manifest.json' in names and any(name.startswith('blobs/') for name in names):
                return Source('scan','bundle')
            mimetype = next((info for _,info in records if info.filename=='mimetype' and info.file_size<=256),None)
            media = archive.read(mimetype).strip() if mimetype else b''
            for predicate,route in (
                ('META-INF/container.xml' in names or media==b'application/epub+zip','epub'),
                ('word/document.xml' in names,'docx'),
                ('xl/workbook.xml' in names,'xlsx'),
                ('ppt/presentation.xml' in names,'pptx'),
                (media==b'application/vnd.oasis.opendocument.text','odt'),
                ('[Content_Types].xml' in names and any(n.endswith('fb3-body.xml') for n in names),'fb3'),
            ):
                if predicate:
                    if route=='epub' and 'META-INF/container.xml' in names:
                        from .fixed_pages import image_only_fixed
                        if image_only_fixed(archive):
                            return Source('scan','fixed-epub')
                    if override!='scan':
                        return Source(route)
            return archive_publications(archive,'zip',entry,scan_only=override=='scan',
                                        textpack=path.suffix.casefold()=='.textpack' or override=='textbundle',
                                        package_route=override if override in PACKAGE_FOLDERS-{'textbundle'} else None)
    except BadZipFile as exc:
        raise Error('unreadable_archive', 'The ZIP archive could not be read.') from exc


def wrapped_source(path,kind,entry,override=None):
    from .archive_intake import containers

    with containers(path,kind) as archive:
        return archive_publications(archive,kind,entry,scan_only=path.suffix.lower() in ('.cb7','.cbr','.cbt'),
                                    package_route=override if override in PACKAGE_FOLDERS else None)


def archive_publications(archive,kind,entry,*,scan_only=False,textpack=False,package_route=None):
    from .publication_package import archive_packages
    from .scan_intake import bounded

    members = [item for item in archive.members() if not item.name.startswith('__MACOSX/')]
    packages,claimed = archive_packages(archive,members,textpack=textpack)
    if textpack and scan_only:
        raise Error('source_format','The selected publication is a TextPack.')
    if textpack and not packages:
        raise Error('source_textbundle','The TextPack has no info.json descriptor next to its text payload.')
    manifests, images, documents = [], [], []
    for item in members:
        if textpack or package_route or item.index in claimed:
            continue
        name = PurePosixPath(item.name)
        basename, suffix = name.name.casefold(),name.suffix.lower()
        is_manifest = basename in ('pages.hamr.json','mets.xml') or suffix=='.mets'
        if basename=='manifest.json' and item.size is not None and item.size<=MAX_MANIFEST:
            with archive.open_member(item) as stream:
                try:
                    payload = json.loads(bounded(stream,MAX_MANIFEST))
                    is_manifest = isinstance(payload,dict) and (payload.get('type')=='Manifest'
                                  or payload.get('schema') in PAGE_SCHEMAS)
                except (UnicodeError,ValueError,TypeError):
                    pass
        if is_manifest:
            manifests.append(item)
            continue
        if suffix in SIDECAR_SUFFIXES and suffix!='.xml':
            continue
        detected = 'scan' if suffix in IMAGE_SUFFIXES else NATIVE.get(suffix)
        if (kind=='zip' and not detected or suffix in ('.lrf','.lit','.xml')) and not name.name.startswith('.'):
            with archive.open_member(item) as stream:
                prefix = stream.read(512)
            detected = probe(prefix,item.name)
            if suffix=='.xml' and not re.search(rb'<(?:[A-Za-z][\w.-]*:)?(?:FictionBook|dtbook|TEI|teiCorpus|article|book|docbook)(?:[\s>])',prefix):
                detected = None
        if detected=='scan':
            images.append(item)
        elif detected and not scan_only:
            documents.append((item,detected))
    candidates = {}
    if manifests:
        for item in manifests:
            ident = 'pages' if len(manifests)==1 else 'manifest:'+str(item.index)
            candidates[ident] = (f'Image sequence ({item.name})', Source('scan',kind,item.index))
    elif images:
        candidates['pages'] = (f'Image sequence ({len(images)} files)',Source('scan',kind))
    for item,route in documents:
        if (images or manifests) and PurePosixPath(item.name).stem.casefold() in ('readme','metadata','license'):
            continue
        candidates[str(item.index)] = (item.name,Source(route,kind+'-member',item.index))
    for route,descriptor,name in packages if not scan_only else ():
        if not package_route or route==package_route:
            label = name if name!='.' else 'TextBundle'
            candidates['bundle:'+str(descriptor.index)] = (label,Source(route,kind,descriptor.index))
    selected = choose([(ident,name) for ident,(name,_) in candidates.items()],entry)
    return candidates[selected][1]


def describe(path, *, entry=None, format=None, offline=False, encoding=None):
    source = _describe(path,entry,format,offline)
    if encoding and source.route not in ('text','chm','textbundle'):
        raise Error('source_encoding','Use --encoding for plain text, TextBundle or compiled HTML Help.')
    if source.kind=='file' and source.route in ('scan','djvu') and source.member_name is None:
        source = replace(source,member_name=path.name)
    return replace(source,encoding=encoding) if encoding else source


def folder_choices(path,route):
    if route=='gitbook':
        from .gitbook_intake import folder_choices as space_choices

        return space_choices(path)
    from .scrivener_intake import folder_binders

    names = folder_binders(path)
    if not names:
        raise Error('source_scrivener_binder','This Scrivener project folder has no root .scrivx binder.',
                    'Open the complete project folder or a backup ZIP.')
    return [(name,name) for name in names]


def _describe(path,entry,format,offline):
    if not path.exists():
        raise Error('source_missing', f'Document not found: {path}', 'Check the document path.')
    routes = {*NATIVE.values(),*PACKAGE_FOLDERS,'scan'}
    if format and format not in routes:
        raise Error('source_format', f'Unknown intake format: {format}.', 'Use hamr agent capabilities to list formats.')
    if path.is_dir():
        if path.is_symlink() or path.is_junction():
            raise Error('unsafe_source_member', 'A publication folder must be a real directory.')
        if route:=publication_folder(path):
            if format not in (None,route):
                raise Error('source_format',f'This directory uses the {route} publication format.')
            ordinal = None
            if route in ('gitbook','scrivener'):
                labels = folder_choices(path,route)
                selected = choose(labels,entry)
                ordinal = next(number for number,(ident,_) in enumerate(labels,1) if ident==selected)
            return Source(route,'directory',entry=ordinal,offline=offline)
        manifest = path/'pages.hamr.json'
        if manifest.exists():
            relative_file(path,manifest.name)
            read_manifest(manifest)
            return Source('scan','directory',route_override=format,offline=offline)
        pages,books = directory_members(path)
        inputs = ([('pages',f'Image sequence ({len(pages)} files)')] if pages else [])
        selectors = {}
        for name,route in books:
            if route in ('gitbook','scrivener'):
                spaces = folder_choices(relative_path(path,name),route)
                if len(spaces)!=1:
                    for ident,title in spaces:
                        selection = name+'/'+ident
                        inputs.append((selection,f'{name} · {title}'))
                        selectors[selection] = (name,ident)
                    continue
            inputs.append((name,name))
        selection = choose(inputs,entry)
        if selection!='pages':
            name,child = selectors.get(selection,(selection,None))
            selected = relative_path(path,name)
            result = describe(selected,entry=child,format=format,offline=offline)
            return replace(result,member_name=name) if result.kind=='directory' else result
        return Source('scan','directory',route_override=format,offline=offline)
    if not path.is_file():
        raise Error('source_type', 'Choose a regular file or folder.')
    if path.suffix.casefold()=='.scrivx':
        if format not in (None,'scrivener'):
            raise Error('source_format','A Scrivener binder requires the Scrivener reader.')
        if path.parent.is_symlink() or path.parent.is_junction():
            raise Error('unsafe_source_member','A publication folder must be a real directory.')
        from .scrivener_intake import folder_binders

        relative_file(path.parent,path.name)
        names = folder_binders(path.parent)
        if path.name not in names:
            raise Error('source_scrivener_binder','Choose the visible root .scrivx binder for this project.')
        return Source('scrivener','directory',entry=names.index(path.name)+1,offline=offline)
    with path.open('rb') as stream:
        prefix = stream.read(512)
    suffix = path.suffix.lower()
    is_mets = suffix in ('.xml','.mets') and re.search(rb'<(?:[A-Za-z][\w-]*:)?mets(?:\s|>)',prefix)
    if suffix=='.mets' or is_mets or suffix=='.json' and format in (None,'scan'):
        read_manifest(path)
        return Source('scan','manifest',route_override=format,offline=offline)
    if (suffix in ('.tar','.cbt','.tgz','.tbz2','.txz') or path.name.lower().endswith(('.tar.gz','.tar.bz2','.tar.xz'))
            or prefix[257:262]==b'ustar'):
        selected = wrapped_source(path,'tar',entry,format)
        return Source(format or selected.route,selected.kind,selected.entry,format,offline)
    if (suffix in ('.7z','.rar','.cb7','.cbr') or prefix.startswith((b'7z\xbc\xaf\x27\x1c',b'Rar!'))):
        selected = wrapped_source(path,'libarchive',entry,format)
        return Source(format or selected.route,selected.kind,selected.entry,format,offline)
    if suffix in ('.cbz','.zip','.textpack') or prefix.startswith(b'PK\x03\x04'):
        selected = zip_source(path,entry,format or ('scan' if suffix=='.cbz' else None))
        if format and format!=selected.route and selected.kind!='file':
            raise Error('source_format', 'The selected container does not match the requested format.')
        return Source(format or selected.route,selected.kind,selected.entry,format,offline)
    route = format or probe(prefix,path.name)
    if route=='legacy-ebook' and not prefix.startswith((b'L\0R\0F\0',b'ITOLITLS')):
        raise Error('unsupported_format','The file does not identify a supported legacy ebook.')
    if not route:
        raise Error('unsupported_format', f'Hamr has no reader for {path.name}.',
                    'Choose EPUB, PDF, an image sequence, or another format listed by hamr agent capabilities.')
    return Source(route,route_override=format,offline=offline)


def _local_source(path, options, source=None):
    if source and source.kind=='directory' and source.route in PACKAGE_FOLDERS:
        if source.route=='scrivener' and path.is_file():
            return path.parent
        return relative_path(path,source.member_name) if source.member_name else path
    if path.is_dir() and options.get('entry') not in (None,'pages') and not (path/'pages.hamr.json').exists():
        return relative_file(path,options['entry'])
    if path.is_dir() and not (path/'pages.hamr.json').exists():
        pages, books = directory_members(path)
        if not pages and len(books)==1:
            return relative_file(path,books[0][0])
    return path


def stamp(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns


def copy_stream(source, destination):
    before = stamp(source.stat())
    with source.open('rb') as stream:
        if stamp(os.fstat(stream.fileno()))!=before:
            raise Error('source_changed', f'Source changed while capturing {source.name}.')
        checksum = hashlib.sha256()
        while chunk := stream.read(CHUNK):
            destination.write(chunk)
            checksum.update(chunk)
        if stamp(os.fstat(stream.fileno()))!=before or stamp(source.stat())!=before:
            raise Error('source_changed', f'Source changed while capturing {source.name}.')
    return checksum.hexdigest()


def copy_snapshot(source,destination):
    size = source.stat().st_size
    with destination.open('xb') as output:
        cloned = False
        if os.name=='posix':
            import fcntl
            with source.open('rb') as stream:
                before = stamp(os.fstat(stream.fileno()))
                try:
                    fcntl.ioctl(output.fileno(),0x40049409,stream.fileno())  # Linux FICLONE
                except OSError:
                    pass
                else:
                    cloned = True
                    if stamp(source.stat())!=before:
                        raise Error('source_changed', f'Source changed while capturing {source.name}.')
        if not cloned:
            if shutil.disk_usage(destination.parent).free < size:
                raise Error('disk_space', f'Not enough room to save the original at {destination.parent}.')
            copy_stream(source,output)
        output.flush()
        os.fsync(output.fileno())
    syncdir(destination.parent)


def page_manifest(path,kind):
    if kind=='manifest':
        root, manifest = path.parent, path
    else:
        root, manifest = path, path/'pages.hamr.json'
    if manifest.is_file():
        value = read_manifest(manifest)
        pages = value['pages']
        title = value.get('title',root.name)
        if not isinstance(title,str):
            raise Error('source_manifest', 'The page manifest title must be text.')
        return root,manifest,pages,title
    names,_ = directory_members(root)
    return root,None,[{'path':name} for name in names],root.name


def make_bundle(path,kind,target):
    root,order,pages,title = page_manifest(path,kind)
    normalized, locations, seen = [], {}, set()
    order_hash = filehash(order) if order else None
    for index,item in enumerate(pages,1):
        if not isinstance(item,dict) or not isinstance(item.get('path'),str):
            raise Error('source_manifest', f'Page entry {index} must name a relative image path.')
        name = safe_name(item['path'])
        occurrence = item.get('occurrence',f'p{index:06d}')
        if not isinstance(occurrence,str) or not occurrence or occurrence in seen:
            raise Error('source_manifest', f'Invalid or repeated occurrence in page entry {index}.')
        seen.add(occurrence)
        row = {'occurrence':occurrence,'path':name}
        for field in ('frame','crop','label','role'):
            if field in item:
                row[field] = item[field]
        locations[name] = relative_file(root,name)
        sidecar = item.get('transcription')
        if sidecar is None and not order:
            base = PurePosixPath(name)
            candidates = [str(base.with_suffix(ext)) for ext in SIDECAR_ENDINGS
                          if (root/str(base.with_suffix(ext))).is_file()]
            if len(candidates)==1:
                sidecar = {'path':candidates[0]}
            elif len(candidates)>1:
                row['transcription_alternatives'] = candidates
                locations.update((n,relative_file(root,n)) for n in candidates)
        if sidecar is not None:
            if not isinstance(sidecar,dict):
                raise Error('source_manifest', f'Page entry {index} needs an OCR transcription path.')
            side_name = safe_name(sidecar.get('path'))
            row['transcription'] = {'path':side_name,'format':sidecar.get('format')}
            locations[side_name] = relative_file(root,side_name)
        normalized.append(row)
    if order:
        locations[order.name] = order
    proofs = {name:(filehash(local),local.stat().st_size) for name,local in sorted(locations.items())}
    unique = {sha:locations[name] for name,(sha,_) in proofs.items()}
    provenance = {'schema':BUNDLE_SCHEMA,'title':title,'pages':normalized,
                  'members':{name:{'sha256':sha,'size':size} for name,(sha,size) in proofs.items()}}
    lengths = dict(proofs.values())
    needed = sum(lengths.values())+len(proofs)*1024
    if shutil.disk_usage(target.parent).free<needed:
        raise Error('disk_space', f'Need at least {needed:,} bytes for the original image bundle.')
    def zipinfo(name):
        info = ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFREG|0o400)<<16
        return info
    with ZipFile(target,'x',compression=ZIP_STORED,allowZip64=True) as archive:
        archive.writestr(zipinfo('manifest.json'),json.dumps(provenance,ensure_ascii=False,sort_keys=True).encode())
        for sha,source in sorted(unique.items()):
            with archive.open(zipinfo(f'blobs/{sha}'),'w',force_zip64=True) as destination:
                if copy_stream(source,destination)!=sha:
                    raise Error('source_changed', f'Source changed while capturing {source.name}.')
        if order and filehash(order)!=order_hash:
            raise Error('source_changed', 'The page order manifest changed during capture.')
        if not order and directory_members(root)[0]!=[item['path'] for item in pages]:
            raise Error('source_changed', 'The page list changed during capture.')
    return provenance


def capture(path,target_dir,**options):
    source = describe(path,**options)
    local = _local_source(path,options,source)
    if source.kind=='directory' and source.route in PACKAGE_FOLDERS:
        if source.route=='textbundle':
            from .textbundle import capture_textbundle

            target = target_dir/'source.textpack'
            capture_textbundle(local,target)
        elif source.route=='mdbook':
            from .mdbook_intake import capture_mdbook

            target = target_dir/'source.zip'
            capture_mdbook(local,target,container=path)
        elif source.route=='gitbook':
            from .gitbook_intake import capture_gitbook

            target = target_dir/'source.zip'
            capture_gitbook(local,target,entry=source.entry)
        elif source.route=='sphinx':
            from .sphinx_intake import capture_sphinx

            target = target_dir/'source.zip'
            capture_sphinx(local,target)
        else:
            from .scrivener_intake import capture_scrivener, folder_binders

            names = folder_binders(local)
            if not source.entry or source.entry>len(names):
                raise Error('source_changed','The selected Scrivener binder disappeared before capture.')
            binder = path.name if path.is_file() else names[source.entry-1]
            target = target_dir/'source.zip'
            capture_scrivener(local,target,binder)
        selected = zip_source(target,None,source.route)
        source = replace(selected,offline=source.offline,encoding=source.encoding)
    elif source.kind in ('directory','manifest'):
        target = target_dir/'source.hamr.zip'
        make_bundle(path,source.kind,target)
        source = Source('scan','bundle',offline=source.offline)
    else:
        suffix = local.suffix.lower()
        # Preserve legacy snapshot names while giving misnamed inputs a safe suffix.
        suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,16}',suffix) else '.bin'
        target = target_dir/('source'+suffix)
        copy_snapshot(local,target)
    with target.open('r+b') as stream:
        os.fsync(stream.fileno())
    target.chmod(0o400)
    syncdir(target_dir)
    return target,source


@contextmanager
def preview(path,cache,**options):
    source = describe(path,**options)
    if source.kind in ('directory','manifest'):
        with tempfile.TemporaryDirectory(prefix='source-preview-',dir=cache) as directory:
            yield capture(path,Path(directory),**options)
    else:
        yield _local_source(path,options,source),source


def original_name(snapshot,source):
    return 'original.hamr.zip' if source and source.get('kind')=='bundle' else 'original'+Path(snapshot).suffix
