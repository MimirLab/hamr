"""Discover Pages publications and preserve their original package members."""

from collections import defaultdict
from gzip import GzipFile
from pathlib import Path, PurePosixPath
import plistlib
import tempfile
from zipfile import BadZipFile, ZipFile

from .archive_intake import ZipMembers, containers
from .core import Error
from .publication_package import member_bytes, member_location


XML_PARTS = ('index.xml','index.xml.gz')
IWA_PARTS = ('Index/Document.iwa','Index.zip')
DESCRIPTORS = (*XML_PARTS,*IWA_PARTS)
PAGES_ROOT = '{http://developer.apple.com/namespaces/sl}document'
PROPERTIES = 'Metadata/Properties.plist'
PAGES_PASSWORD_METHOD = 0x636c
MAX_PACKAGE = 128 * 1024 * 1024
CHUNK = 1024 * 1024


def split_marker(name):
    for part in DESCRIPTORS:
        if name==part or name.endswith('/'+part):
            return name[:-len(part)],part
    return None


def xml_document(stream,part):
    from .document_intake import xml_start

    try:
        if part.endswith('.gz'):
            with GzipFile(fileobj=stream) as decoded:
                root = xml_start(decoded)
        else:
            root = xml_start(stream)
    except (BadZipFile,EOFError,OSError,RuntimeError,NotImplementedError):
        return False
    return root is not None and root.tag==PAGES_ROOT


def folder_marker(path):
    if path.suffix.casefold()=='.pages':
        return True
    for part in XML_PARTS:
        candidate = path/part
        if not candidate.is_symlink() and candidate.is_file():
            with candidate.open('rb') as stream:
                if xml_document(stream,part):
                    return True
    properties = path/PROPERTIES
    if properties.is_symlink() or not properties.is_file() or not any((path/part).is_file() for part in IWA_PARTS):
        return False
    with properties.open('rb') as stream:
        return document_properties(stream.read(65537))


def protected(archive,member,part):
    return (part in XML_PARTS and isinstance(archive,ZipMembers)
            and archive.infolist()[member.index-1].compress_type==PAGES_PASSWORD_METHOD)


def document_properties(data):
    if len(data)>65536:
        return False
    try:
        metadata = plistlib.loads(data)
    except (ValueError,TypeError,EOFError,OverflowError,IndexError):
        return False
    return isinstance(metadata,dict) and metadata.get('isMultiPage') is True


def document_marker(archive,member,prefix,part,by_name):
    if part in XML_PARTS:
        if protected(archive,member,part):
            return prefix+'buildVersionHistory.plist' in by_name
        with archive.open_member(member) as stream:
            return xml_document(stream,part)
    properties = by_name.get(prefix+PROPERTIES,())
    if len(properties)!=1 or (properties[0].size is not None and properties[0].size>65536):
        return False
    try:
        return document_properties(member_bytes(archive,properties[0],65536))
    except (NotImplementedError,BadZipFile):
        return False


def archive_books(archive,members,*,explicit=False):
    by_name = defaultdict(list)
    roots = defaultdict(list)
    for item in members:
        by_name[item.name].append(item)
        if marker:=split_marker(item.name):
            prefix,part = marker
            roots[prefix].append((item,part))
    books,claimed = [],set()
    for prefix,markers in sorted(roots.items()):
        if any(item.index in claimed for item,_ in markers):
            continue
        label = PurePosixPath(prefix).name
        named = label.casefold().endswith('.pages')
        selected = next((item for item,part in markers if named or explicit
                         or document_marker(archive,item,prefix,part,by_name)),None)
        if selected is not None:
            books.append((selected,PurePosixPath(label).stem or 'Apple Pages'))
            claimed.update(item.index for item in members if item.name.startswith(prefix))
    return books,claimed


def members_for(archive,entry):
    members = [item for item in archive.members() if not item.name.startswith('__MACOSX/')]
    descriptor = next((item for item in members if item.index==entry),None)
    marker = split_marker(descriptor.name) if descriptor else None
    if not marker:
        raise Error('source_pages_package','The Pages publication has no original document descriptor.',
                    'Open the complete Pages file or package folder.')
    prefix,part = marker
    selected = [(item,item.name[len(prefix):]) for item in members if item.name.startswith(prefix)]
    markers = [item for item,name in selected if name in DESCRIPTORS]
    names = {name for _,name in selected}
    if len(markers)!=1 or len(selected)!=len(names):
        raise Error('source_pages_package','The Pages publication has competing document parts or duplicate names.')
    if protected(archive,descriptor,part):
        raise Error('protected_source','This Pages document needs a password.',
                    'Open it with the password and save a readable local copy.')
    return prefix,part,descriptor,selected


def copy_part(archive,member,destination,remaining):
    if member.size is not None and member.size>remaining:
        raise Error('source_structure_size','The expanded Pages package exceeds the local reading bound.')
    size = 0
    with archive.open_member(member) as stream:
        while chunk:=stream.read(min(CHUNK,remaining-size+1)):
            size += len(chunk)
            if size>remaining:
                raise Error('source_structure_size','The expanded Pages package exceeds the local reading bound.')
            destination.write(chunk)
    if member.size is not None and member.size!=size:
        raise Error('unreadable_archive',f'The Pages package ended partway through {member.name}.')
    return size


def projected_package(archive,selected,document,nested):
    size = 0
    inner_size = 0
    with ZipFile(document,'x') as target:
        for member,name in selected:
            with target.open(name,'w') as output:
                if name!='Index.zip':
                    size += copy_part(archive,member,output,MAX_PACKAGE-size-inner_size)
                    continue
                with nested.open('xb') as index:
                    size += copy_part(archive,member,index,MAX_PACKAGE-size)
                with ZipMembers(nested) as index:
                    parts = list(index.members())
                    names = {part.name for part in parts}
                    if len(names)!=len(parts) or 'Index/Document.iwa' not in names:
                        raise Error('source_pages_package','The Pages Index.zip has no unique document part.')
                    inner_size = sum(part.size for part in parts)
                    if size+inner_size>MAX_PACKAGE:
                        raise Error('source_structure_size','The expanded Pages index exceeds the local reading bound.')
                with nested.open('rb') as index:
                    while chunk:=index.read(CHUNK):
                        output.write(chunk)


def read_pages(path,source,sha,title,cache):
    from .document_intake import DocumentBuilder
    from .legacy_office_intake import read_legacy_odf

    try:
        with containers(path,source.kind) as archive:
            prefix,part,descriptor,selected = members_for(archive,source.entry)
            builder = DocumentBuilder('pages',PurePosixPath(prefix).stem if prefix else title,sha)
            with tempfile.TemporaryDirectory(prefix='hamr-pages-',dir=cache) as temporary:
                root = Path(temporary)
                document = root/'publication.pages'
                projected_package(archive,selected,document,root/'Index.zip')
                doc = read_legacy_odf(document,builder,cache,kind='pages',input_limit=MAX_PACKAGE)
    except (BadZipFile,NotImplementedError,RuntimeError) as exc:
        raise Error('unreadable_pages','The Pages package contains an unreadable or unsupported part.') from exc
    for block in doc.blocks:
        block.locator.update(member=descriptor.name,archive_entry=descriptor.index,member_scope='document_index')
    doc.metadata['source_resources'] = [member_location(sha,item,'package_member') for item,_ in selected]
    doc.metadata['pages_generation'] = 'xml' if part in XML_PARTS else 'iwa'
    return doc
