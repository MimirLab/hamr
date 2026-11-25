"""Identify and read OpenDocument package parts without using their thumbnails."""

from collections import defaultdict
from pathlib import PurePosixPath

from .core import Error, key
from .document_intake import DocumentBuilder, MAX_STRUCTURE, visit_odf_parts, xml
from .odf_content import MANIFEST, MASTER_MEDIA, TEXT_MEDIA
from .publication_package import member_bytes, member_location


LEGACY_TEXT_MEDIA = frozenset((b'application/vnd.sun.xml.writer',b'application/vnd.sun.xml.writer.template'))
WRITER_MEDIA = frozenset(value.encode() for value in TEXT_MEDIA) | LEGACY_TEXT_MEDIA
MASTER_TYPES = frozenset((MASTER_MEDIA.encode(),))
PRESENTATION_MEDIA = frozenset((b'application/vnd.oasis.opendocument.presentation',
                                b'application/vnd.oasis.opendocument.presentation-template'))
MEDIA_ROUTES = {value:route for media,route in ((WRITER_MEDIA,'odt'),(MASTER_TYPES,'odf-master'),
                                                (PRESENTATION_MEDIA,'odp')) for value in media}
LEGACY_MANIFEST = '{http://openoffice.org/2001/manifest}'
MEDIA_LIMIT = 256
MANIFEST_PART = 'META-INF/manifest.xml'


def manifest_media(data):
    try:
        root = xml(data)
    except Error:
        return b''
    namespace = next((ns for ns in (MANIFEST,LEGACY_MANIFEST) if root.tag==ns+'manifest'),None)
    if namespace is None:
        return b''
    declared = [item.get(namespace+'media-type','') for item in root.iterchildren(namespace+'file-entry')
                if item.get(namespace+'full-path')=='/']
    if len(declared)==1:
        return declared[0].encode()
    return b''


def folder_media(root):
    marker = root/'mimetype'
    if marker.is_symlink():
        return b''
    if marker.is_file():
        with marker.open('rb') as stream:
            return stream.read(MEDIA_LIMIT+1).strip()
    manifest = root/MANIFEST_PART
    if manifest.parent.is_symlink() or manifest.is_symlink() or not manifest.is_file():
        return b''
    with manifest.open('rb') as stream:
        data = stream.read(MAX_STRUCTURE+1)
    return manifest_media(data) if len(data)<=MAX_STRUCTURE else b''


def folder_marker(root,media):
    return folder_media(root) in media


def marker_prefix(name):
    return name.removesuffix(MANIFEST_PART) if name.endswith(MANIFEST_PART) else name.removesuffix('mimetype')


def archive_books(archive,members):
    """Return recognized package markers and claim the members they enclose."""
    roots,claimed = [],set()
    names = {member.name for member in members}
    markers = []
    for item in members:
        if PurePosixPath(item.name).name=='mimetype' and (item.size is None or item.size<=MEDIA_LIMIT):
            markers.append((marker_prefix(item.name),item,MEDIA_LIMIT))
        elif item.name==MANIFEST_PART or item.name.endswith('/'+MANIFEST_PART):
            prefix = marker_prefix(item.name)
            if (prefix+'mimetype' not in names and prefix+'content.xml' in names and
                    (item.size is None or item.size<=MAX_STRUCTURE)):
                markers.append((prefix,item,MAX_STRUCTURE))
    for prefix,marker,limit in sorted(markers,key=lambda row:(row[0].count('/'),row[0])):
        if marker.index in claimed:
            continue
        data = member_bytes(archive,marker,limit)
        media = data.strip() if limit==MEDIA_LIMIT else manifest_media(data)
        if not (route:=MEDIA_ROUTES.get(media)):
            continue
        claimed.update(part.index for part in members if part.name.startswith(prefix))
        label = PurePosixPath(prefix).name
        if route=='odp':
            label = f'OpenDocument presentation ({label})' if label else 'OpenDocument presentation'
        roots.append((route,marker,label or ('Writer master document' if route=='odf-master' else 'OpenDocument text')))
    return roots,claimed


class PackageParts:
    def __init__(self,archive,members,prefix):
        self.archive = archive
        self.members = defaultdict(list)
        for member in members:
            if member.name.startswith(prefix):
                self.members[member.name[len(prefix):]].append(member)
        self.protected = set()
        if info:=self.read(MANIFEST_PART,required=False):
            # OpenOffice 1.x uses the same package declarations under its old namespace.
            root = xml(info)
            namespace = LEGACY_MANIFEST if root.tag.startswith(LEGACY_MANIFEST) else MANIFEST
            self.protected = {entry.get(namespace+'full-path') for entry in root.iter(namespace+'file-entry')
                              if entry.find(namespace+'encryption-data') is not None}

    def marker(self):
        return self.member('mimetype',required=False) or self.member(MANIFEST_PART)

    def member(self,name,required=True):
        matches = self.members.get(name,())
        if len(matches)==1:
            return matches[0]
        if matches:
            raise Error('ambiguous_package_member',f'The Writer package repeats its {name} part.')
        if required:
            raise Error('source_member_missing',f'The Writer package is missing {name}.')
        return None

    def read(self,name,required=True):
        if name in self.protected:
            raise Error('protected_source',f'The Writer package part {name} is encrypted.',
                        'Save a readable local copy in Writer.')
        if member:=self.member(name,required):
            return member_bytes(self.archive,member,MAX_STRUCTURE)
        return None


def read_writer_package(path,source,sha,title):
    from .archive_intake import containers

    with containers(path,source.kind) as archive:
        members = list(archive.members())
        roots,_ = archive_books(archive,members)
        selected = [(marker,label) for route,marker,label in roots if route=='odt' and source.entry in (None,marker.index)]
        if len(selected)!=1:
            raise Error('source_member_missing','The source has no unique Writer package.')
        marker,label = selected[0]
        package = PackageParts(archive,members,marker_prefix(marker.name))
        package.marker()
        content = package.member('content.xml')
        raw = xml(package.read('content.xml'))
        builder = DocumentBuilder('odt',label if marker_prefix(marker.name) else title,sha)
        visit_odf_parts(raw,package.read,builder,'content.xml')
        doc = builder.doc
        doc.metadata['source_resources'] = [member_location(sha,items[0],'package_member')
                                            for items in package.members.values() if len(items)==1]
        for block in doc.blocks:
            block.locator.update(member=content.name,archive_entry=content.index,
                                 location_id=key([sha,content.index,block.id])[:20])
        return doc
