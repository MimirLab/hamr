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
    '.xps':'xps', '.oxps':'xps',
    '.textpack':'textbundle',
    '.djvu':'djvu', '.djv':'djvu', '.chm':'chm', '.html':'html', '.htm':'html', '.xhtml':'html', '.docx':'docx',
    '.odt':'odt', '.ott':'odt', '.oth':'odt', '.fodt':'odt', '.odm':'odf-master', '.sxw':'odt', '.stw':'odt',
    '.sxg':'odf-master',
    '.abw':'abiword', '.awt':'abiword', '.zabw':'abiword', '.bzabw':'abiword',
    '.abw.gz':'abiword', '.abw.bz2':'abiword',
    '.dbk':'xml-book', '.docbook':'xml-book', '.fb2':'fb2', '.fb3':'fb3', '.rtf':'rtf', '.dtbook':'xml-book', '.xml':'xml-book',
    '.jats':'xml-book', '.tei':'xml-book', '.nimas':'xml-book', '.bits':'xml-book', '.nxml':'xml-book',
    '.zai':'xml-book',
    '.csv':'csv', '.xlsx':'xlsx', '.pptx':'pptx', '.odp':'odp', '.otp':'odp', '.fodp':'odp',
    '.opml':'outline', '.svg':'outline', '.tsv':'csv', '.ipynb':'notebook', '.mhtml':'mhtml', '.mht':'mhtml',
    '.eml':'mail', '.tcr':'tcr', '.guide':'amigaguide', '.rb':'rocket', '.snb':'snb', '.mobi':'mobi', '.azw':'mobi', '.azw3':'mobi', '.azw4':'mobi', '.lrf':'legacy-ebook', '.lit':'legacy-ebook', '.pml':'pml', '.pmlz':'pml', '.doc':'office-legacy', '.wps':'office-legacy',
    '.wpd':'wordperfect', '.wp':'wordperfect', '.wp4':'wordperfect', '.wp5':'wordperfect', '.wp6':'wordperfect',
    '.hqx':'classic-mac',
    '.hwp':'office-legacy', '.srt':'subtitles', '.vtt':'subtitles',
    '.rst':'rst', '.rest':'rst', '.org':'org', '.adoc':'asciidoc', '.asciidoc':'asciidoc', '.tex':'latex',
    '.ws':'wordstar', '.ws7':'wordstar', '.pages':'pages',
}
PALM_SUFFIXES = frozenset(('.pdb','.prc'))
PALM_ROUTES = frozenset(('palmdoc','ereader','ztxt'))
PAGE_SCHEMAS = frozenset(('hamr.page-sequence.v1', 'hamr.page-sequence.design.v1'))
OPF_FOLDERS = frozenset(('nimas','daisy3'))
DESCRIPTOR_FOLDERS = OPF_FOLDERS | {'daisy202'}
PACKAGE_FOLDERS = frozenset(('textbundle','mdbook','gitbook','sphinx','scrivener','fb3','xps','pptx',
                             'odp','odt','odf-master','pages')) | DESCRIPTOR_FOLDERS
DEPENDENCY_SOURCES = frozenset(('rst','org','asciidoc','latex','xml-book','pml','classic-mac','odf-master'))
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


def source_suffix(name):
    filename = Path(name).name.lower()
    for compound in ('.abw.gz','.abw.bz2'):
        if filename.endswith(compound):
            return compound
    return Path(filename).suffix


def image_magic(prefix):
    return (prefix.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'II*\0', b'MM\0*',
                               b'II+\0', b'MM\0+', b'GIF87a', b'GIF89a',
                               b'\0\0\0\x0cjP  \r\n\x87\n', b'\xffO\xffQ'))
            or prefix.startswith(b'RIFF') and prefix[8:12]==b'WEBP')


def probe(prefix, name):
    suffix = source_suffix(name)
    if prefix.startswith(b'\x1d\x7d\x00\x00') or suffix in ('.ws','.ws7'):
        from .wordstar_intake import matches_header

        return 'wordstar' if matches_header(prefix) else None
    if prefix.startswith(b'AT&TFORM') and prefix[12:16] in (b'DJVU',b'DJVM'):
        return 'djvu'
    if prefix.startswith(b'ITSF') and len(prefix)>=8 and int.from_bytes(prefix[4:8],'little') in (2,3):
        return 'chm'
    if suffix=='.chm':
        return None
    if prefix.startswith(b'\xb0\x0c\xb0\x0c'):
        return 'rocket'
    if prefix.startswith(b'SNBP000B'):
        return 'snb'
    if suffix in ('.rb','.snb'):
        return None
    if len(prefix)>=14 and prefix.startswith(b'\xffWPC') and prefix[9:11] in (
            b'\x0a\x00',b'\x0a\x02',b'\x2c\x02',b'\x2c\x03',b'\x2c\x04'):
        return 'wordperfect'
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
    signature = prefix[60:68]
    if signature==b'BOOKMOBI':
        return 'mobi'
    from .palm_database import BOOK_TYPES

    if signature in BOOK_TYPES:
        return BOOK_TYPES[signature]
    if suffix in PALM_SUFFIXES:
        return None
    if prefix.lstrip().startswith(b'{\\rtf'):
        return 'rtf'
    from .classic_mac_intake import matches_header

    if matches_header(prefix):
        return 'classic-mac'
    if suffix=='.hqx':
        return None
    if suffix=='.asc':
        from .asciidoc_intake import matches_asc

        if matches_asc(prefix):
            return 'asciidoc'
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
    from .fictionbook_package import folder_marker as fb3_folder
    from .odf_package import MEDIA_ROUTES, folder_media
    from .pages_intake import folder_marker as pages_folder
    from .presentation_intake import folder_marker as pptx_folder
    from .xps_intake import folder_marker as xps_folder

    if xps_folder(path):
        return 'xps'
    if fb3_folder(path):
        return 'fb3'
    if pptx_folder(path):
        return 'pptx'
    if route:=MEDIA_ROUTES.get(folder_media(path)):
        return route
    if pages_folder(path):
        return 'pages'
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
    from .classic_mac_intake import loose_fork, matches_header
    from .document_intake import xml_route
    from .ncc_intake import descriptor_name, folder_books, folder_descriptors
    from .opf_intake import folder_packages
    from .pml_package import discover_loose_assets, is_pml
    from .xml_package import SUFFIXES as XML_SUFFIXES, loose_assets, loose_document

    declared = set()
    xml_claimed = set()
    pages, other = [], []
    for folder, directories, files in os.walk(root, followlinks=False):
        here = Path(folder)
        if any(Path(filename).suffix.casefold()=='.opf' for filename in files):
            for descriptor,book in folder_packages(here):
                other.append(((here/descriptor).relative_to(root).as_posix(),book.route))
                declared.update((here/item.target).relative_to(root).as_posix()
                                for item in book.resources() if item.target)
        if any(descriptor_name(filename) for filename in files):
            descriptors = [filename for filename in folder_descriptors(here)
                           if (here/filename).relative_to(root).as_posix() not in declared]
            for book,descriptor in folder_books(here,descriptors):
                other.append(((here/descriptor.name).relative_to(root).as_posix(),'daisy202'))
                declared.update((here/item.name).relative_to(root).as_posix() for item in book.resources)
        for filename in files:
            path = here/filename
            relative = path.relative_to(root).as_posix()
            if (is_pml(filename) and not filename.startswith('.') and not path.is_symlink() and path.is_file()):
                declared.update((here/asset).relative_to(root).as_posix() for asset in discover_loose_assets(path))
            if (filename.startswith('.') or relative in declared or path.suffix.lower() not in XML_SUFFIXES
                    or path.is_symlink() or not path.is_file()):
                continue
            try:
                document = loose_document(path)
            except Error as exc:
                if exc.code not in ('source_xml','source_structure_size'):
                    raise
                continue
            if document is not None:
                linked = {asset for _,asset in loose_assets(path,document,root) if asset and asset!=relative}
                declared.update(linked)
                xml_claimed.update(linked)
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
            if relative in declared:
                continue
            suffix = source_suffix(path.name)
            if suffix in IMAGE_SUFFIXES:
                pages.append(relative)
            elif suffix in PALM_SUFFIXES and not path.is_symlink() and path.is_file():
                with path.open('rb') as stream:
                    if route:=probe(stream.read(512),path.name):
                        other.append((relative,route))
            elif suffix=='.xml' and not path.is_symlink() and path.is_file():
                with path.open('rb') as stream:
                    if route:=xml_route(stream):
                        other.append((relative,route))
            elif suffix not in SIDECAR_SUFFIXES and not path.is_symlink() and path.is_file():
                route = NATIVE.get(suffix)
                if route in (None,'classic-mac','text'):
                    with path.open('rb') as stream:
                        prefix = stream.read(512)
                    classic = matches_header(prefix)
                    if classic or loose_fork(path):
                        route = 'classic-mac'
                    elif route=='classic-mac':
                        route = None
                    elif suffix=='.asc':
                        route = probe(prefix,path.name)
                if route:
                    other.append((relative,route))
    pages = [name for name in pages if name not in xml_claimed]
    other = [(name,route) for name,route in other if name not in xml_claimed]
    masters = [name for name,route in other if route=='odf-master']
    if masters:
        from .odf_master import folder_claims

        claimed = folder_claims(root,masters)
        other = [(name,route) for name,route in other if name not in claimed]
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
    from .document_intake import MAX_STRUCTURE
    from .odf_package import MANIFEST_PART, MASTER_TYPES, PRESENTATION_MEDIA, WRITER_MEDIA, manifest_media
    from .pml_package import pmlz_parts

    try:
        with ZipMembers(path) as archive:
            if path.suffix.casefold()=='.pmlz' and override in (None,'pml') and entry is None:
                pmlz_parts(list(archive.members()))
                return Source('pml','zip')
            package_route = override if override in PACKAGE_FOLDERS-{'textbundle'} else None
            if path.suffix.casefold()=='.fb3' and override is None:
                package_route = 'fb3'
            if path.suffix.casefold() in ('.xps','.oxps') and override is None:
                package_route = 'xps'
            if path.suffix.casefold()=='.pptx' and override is None:
                package_route = 'pptx'
            if path.suffix.casefold() in ('.odp','.otp') and override is None:
                package_route = 'odp'
            if path.suffix.casefold()=='.pages' and override is None:
                package_route = 'pages'
            records = zip_members(archive)
            names = {info.filename for _,info in records}
            if 'manifest.json' in names and any(name.startswith('blobs/') for name in names):
                return Source('scan','bundle')
            marker, mimetype = next(((index,info) for index,info in records
                                     if info.filename=='mimetype' and info.file_size<=256),(None,None))
            media = archive.read(mimetype).strip() if mimetype else b''
            if 'mimetype' not in names and 'content.xml' in names and MANIFEST_PART in names:
                for index,info in records:
                    if info.filename==MANIFEST_PART and info.file_size<=MAX_STRUCTURE:
                        marker,media = index,manifest_media(archive.read(info))
                        break
            if override is None and media in PRESENTATION_MEDIA:
                package_route = 'odp'
            for predicate,route in (
                ('META-INF/container.xml' in names or media==b'application/epub+zip','epub'),
                ('word/document.xml' in names,'docx'),
                ('xl/workbook.xml' in names,'xlsx'),
                (media in WRITER_MEDIA,'odt'),
                (media in MASTER_TYPES,'odf-master'),
            ):
                if predicate:
                    if route=='epub' and 'META-INF/container.xml' in names:
                        from .fixed_pages import image_only_fixed
                        if image_only_fixed(archive):
                            return Source('scan','fixed-epub')
                    if override!='scan' and package_route not in ('fb3','xps','pptx','odp','pages'):
                        if route in ('odt','odf-master') and path.suffix.casefold()=='.zip':
                            return Source(route,'zip',marker)
                        return Source(route)
            return archive_publications(archive,'zip',entry,scan_only=override=='scan',
                                        textpack=path.suffix.casefold()=='.textpack' or override=='textbundle',
                                        package_route=package_route, latex_only=override=='latex')
    except BadZipFile as exc:
        raise Error('unreadable_archive', 'The ZIP archive could not be read.') from exc


def wrapped_source(path,kind,entry,override=None):
    from .archive_intake import containers

    with containers(path,kind) as archive:
        return archive_publications(archive,kind,entry,scan_only=path.suffix.lower() in ('.cb7','.cbr','.cbt'),
                                    package_route=override if override in PACKAGE_FOLDERS else None,
                                    latex_only=override=='latex')


def archive_publications(archive,kind,entry,*,scan_only=False,textpack=False,package_route=None,latex_only=False):
    from .classic_mac_intake import archive_books as mac_books
    from .document_intake import xml_route
    from .publication_package import archive_packages
    from .scan_intake import bounded

    all_members = list(archive.members())
    members = [item for item in all_members if not item.name.startswith('__MACOSX/')]
    packages,claimed = archive_packages(archive,members,textpack=textpack,explicit_pages=package_route=='pages')
    if not scan_only and not textpack and package_route in (None,'classic-mac'):
        forked = mac_books(archive,[item for item in all_members if item.index not in claimed])
        packages.extend(forked)
        claimed.update(part.index for _,part,_ in forked)
    if textpack and scan_only:
        raise Error('source_format','The selected publication is a TextPack.')
    if textpack and not packages:
        raise Error('source_textbundle','The TextPack has no info.json descriptor next to its text payload.')
    if not (scan_only or textpack or latex_only) and package_route in (None,'odf-master'):
        from .odf_master import archive_claims

        linked = archive_claims(archive,members,packages,claimed)
        claimed |= linked
        packages = [item for item in packages if item[1].index not in linked]
    manifests, images, documents = [], [], []
    for item in members:
        if textpack or package_route not in (None,'odf-master') or item.index in claimed:
            continue
        name = PurePosixPath(item.name)
        basename, suffix = name.name.casefold(),source_suffix(name.name)
        if package_route=='odf-master' and NATIVE.get(suffix)!='odf-master':
            continue
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
        if suffix=='.xml' and not name.name.startswith('.'):
            with archive.open_member(item) as stream:
                detected = xml_route(stream)
        elif (not name.name.startswith('.') and
              (kind=='zip' and not detected or suffix in ('.asc','.cwk','.hqx','.lrf','.lit',*PALM_SUFFIXES))):
            with archive.open_member(item) as stream:
                prefix = stream.read(512)
            detected = probe(prefix,item.name)
        if detected=='scan':
            images.append(item)
        elif detected and not scan_only:
            documents.append((item,detected))
    if latex_only:
        manifests,images,packages=[],[],[]
        documents=[item for item in documents if item[1]=='latex']
    if entry is None and any(route=='latex' for _,route in documents):
        from .latex_intake import project_entry

        def latex_root(item):
            with archive.open_member(item) as stream:
                return project_entry(stream.read(64*1024))
        roots = [item.index for item,route in documents if route=='latex' and latex_root(item)]
        if roots:
            documents=[(item,route) for item,route in documents if route!='latex' or item.index in roots]
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
            ident = str(descriptor.index) if route=='xml-book' else 'bundle:'+str(descriptor.index)
            candidates[ident] = (label,Source(route,kind,descriptor.index))
    if package_route=='pages' and not candidates:
        raise Error('source_pages_package','The Pages package has no original document part.',
                    'Open the complete Pages file or package folder.')
    selected = choose([(ident,name) for ident,(name,_) in candidates.items()],entry)
    return candidates[selected][1]


def describe(path, *, entry=None, format=None, offline=False, encoding=None):
    source = _describe(path,entry,format,offline)
    if encoding and source.route not in ('text','chm','textbundle','rst','org','asciidoc','latex','pml','rocket','wordstar',*PALM_ROUTES):
        raise Error('source_encoding','Use --encoding for plain text, AsciiDoc, reStructuredText, Org, LaTeX, TextBundle, Palm ebooks, Palm Markup Language, Rocket eBooks, WordStar or compiled HTML Help.')
    if source.kind=='file' and source.route in ('scan','djvu','abiword','odt','odp','org','asciidoc','latex','pml','wordstar',*PALM_ROUTES) and source.member_name is None:
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
    routes = {*NATIVE.values(),*PACKAGE_FOLDERS,*PALM_ROUTES,'scan'}
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
        if manifest.exists() and format not in DESCRIPTOR_FOLDERS:
            relative_file(path,manifest.name)
            read_manifest(manifest)
            return Source('scan','directory',route_override=format,offline=offline)
        pages,books = directory_members(path)
        if format=='latex':
            pages,books=[],[(name,route) for name,route in books if route=='latex']
        if entry is None and any(route=='latex' for _,route in books):
            from .latex_intake import project_entry

            roots = []
            for name,route in books:
                if route=='latex':
                    with relative_file(path,name).open('rb') as stream:
                        if project_entry(stream.read(64*1024)):
                            roots.append(name)
            if roots:
                books=[(name,route) for name,route in books if route!='latex' or name in roots]
        if format in DESCRIPTOR_FOLDERS:
            pages,books = [],[(name,route) for name,route in books if route==format]
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
            if result.kind=='directory' and result.route in DESCRIPTOR_FOLDERS and selected.is_file():
                parent = selected.parent.relative_to(path)
                return replace(result,member_name=parent.as_posix() if parent.parts else None)
            return replace(result,member_name=name) if result.kind=='directory' or result.route in ('xml-book','latex','odf-master') else result
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
    if path.suffix.casefold()=='.opf' and (format is None or format in OPF_FOLDERS):
        from .opf_intake import folder_packages

        for ordinal,(filename,book) in enumerate(folder_packages(path.parent),1):
            if filename==path.name and format in (None,book.route):
                return Source(book.route,'directory',entry=ordinal,offline=offline)
        raise Error('source_opf','This OPF does not identify a supported NIMAS or DAISY edition.',
                    'For an EPUB, open the complete publication. For NIMAS or DAISY, supply the source folder or ZIP.')
    if format in (None,'daisy202'):
        from .ncc_intake import FolderMembers, describe_book, descriptor_name, folder_descriptors

        if descriptor_name(path.name):
            files = FolderMembers(path.parent)
            names = folder_descriptors(path.parent)
            if path.name in names:
                descriptor, = files.find(path.name)
                if describe_book(descriptor,files.find,files.read):
                    return Source('daisy202','directory',entry=names.index(path.name)+1,offline=offline)
            if format=='daisy202':
                raise Error('source_daisy','Select a readable DAISY NCC or SMIL source.')
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
    if suffix in ('.cbz','.zip','.textpack','.xps','.oxps','.pptx','.odp','.otp','.pmlz','.pages') or prefix.startswith(b'PK\x03\x04'):
        selected = zip_source(path,entry,format or ('scan' if suffix=='.cbz' else None))
        if format and format!=selected.route and selected.kind!='file':
            raise Error('source_format', 'The selected container does not match the requested format.')
        return Source(format or selected.route,selected.kind,selected.entry,format,offline)
    route = format or probe(prefix,path.name)
    if format is None and route in (None,'classic-mac','text'):
        from .classic_mac_intake import APPLESINGLE, BINHEX, loose_fork

        if not prefix.startswith((APPLESINGLE,BINHEX)) and loose_fork(path):
            route = 'classic-mac'
    if suffix=='.xml' and format is None:
        from .document_intake import xml_route

        with path.open('rb') as stream:
            route = xml_route(stream) or route
    if route=='legacy-ebook' and not prefix.startswith((b'L\0R\0F\0',b'ITOLITLS')):
        raise Error('unsupported_format','The file does not identify a supported legacy ebook.')
    if not route:
        from .wordstar_intake import WORDSTAR_SUFFIXES

        if suffix in WORDSTAR_SUFFIXES or prefix.startswith(b'\x1d\x7d\x00\x00'):
            raise Error('source_wordstar_version','The file does not identify a supported WordStar 7 document.',
                        'Open the original WordStar 7 document or use a readable text export.')
        raise Error('unsupported_format', f'Hamr has no reader for {path.name}.',
                    'Choose EPUB, PDF, an image sequence, or another format listed by hamr agent capabilities.')
    return Source(route,route_override=format,offline=offline)


def _local_source(path, options, source=None):
    if path.is_dir() and source and source.kind=='file' and source.route in ('latex','odf-master','xml-book') and source.member_name:
        return relative_file(path,source.member_name)
    if source and source.kind=='directory' and source.route in PACKAGE_FOLDERS:
        if (source.route=='scrivener' or source.route in DESCRIPTOR_FOLDERS) and path.is_file():
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
    prepared = capture_dependencies(local,target_dir,source,root=path if path.is_dir() else None)
    if prepared:
        target,source = prepared
    elif source.kind=='directory' and source.route in DESCRIPTOR_FOLDERS:
        target = target_dir/'source.zip'
        if source.route=='daisy202':
            from .ncc_intake import capture_daisy202

            ordinal = capture_daisy202(local,target,source.entry,descriptor=path.name if path.is_file() else None)
        else:
            from .opf_intake import capture_opf

            ordinal = capture_opf(local,target,source.entry,descriptor=path.name if path.is_file() else None)
        source = replace(source,kind='zip',entry=ordinal,member_name=None)
    elif source.kind=='directory' and source.route in PACKAGE_FOLDERS:
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
        elif source.route in ('fb3','xps','pptx','odp','odt','pages'):
            from .publication_package import capture_folder, folder_files

            target = target_dir/'source.zip'
            capture_folder(local,target,lambda:folder_files(local),include_root=False)
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
        suffix = source_suffix(local.name)
        # Preserve legacy snapshot names while giving misnamed inputs a safe suffix.
        suffix = suffix if suffix in NATIVE or re.fullmatch(r'\.[a-z0-9]{1,16}',suffix) else '.bin'
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
    elif source.kind=='file' and source.route in DEPENDENCY_SOURCES:
        local = _local_source(path,options,source)
        with tempfile.TemporaryDirectory(prefix='source-preview-',dir=cache) as directory:
            yield capture_dependencies(local,Path(directory),source,root=path if path.is_dir() else None) or (local,source)
    else:
        yield _local_source(path,options,source),source


def capture_dependencies(path,target_dir,source,*,root=None):
    if source.kind=='file' and source.route in ('rst','org','asciidoc','latex'):
        from .linked_text_intake import capture_dependencies as linked

        return linked(path,target_dir,source)
    if source.kind=='file' and source.route=='pml':
        from .pml_package import capture_pml

        return capture_pml(path,target_dir,source)
    if source.kind=='file' and source.route=='xml-book':
        from .xml_package import capture_xml

        return capture_xml(path,target_dir,source,scope=root)
    if source.kind=='file' and source.route=='classic-mac':
        from .classic_mac_intake import capture_classic_mac

        return capture_classic_mac(path,target_dir,source)
    if source.kind in ('file','directory') and source.route=='odf-master':
        from .odf_master import capture_master

        return capture_master(path,target_dir,source,root=root)


def original_name(snapshot,source):
    if source and source.get('kind')=='bundle':
        return 'original.hamr.zip'
    suffix = source_suffix(snapshot) if source and source.get('route')=='abiword' else Path(snapshot).suffix
    return 'original'+suffix
