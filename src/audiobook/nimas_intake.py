"""Follow an OEB package's declared DTBook spine and retain its resources."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

from lxml import etree

from .archive_intake import containers
from .core import Error, key
from .document_intake import DocumentBuilder, MAX_STRUCTURE, XML_LANG, attr, name, xml
from .dtbook_intake import read_dtbook
from .importers import document_reference
from .publication_package import capture_folder, member_bytes, member_location
from .source_bundle import relative_file, safe_name, stamp

OEB = 'http://openebook.org/namespaces/oeb-package/1.0/'
OPF = 'http://www.idpf.org/2007/opf'
DC = '{http://purl.org/dc/elements/1.1/}'
DTBOOK = 'application/x-dtbook+xml'
DESCRIPTOR_LIMIT = 1024*1024


@dataclass(frozen=True)
class Resource:
    href: str
    target: str | None
    media: str


@dataclass(frozen=True)
class Package:
    title: str
    language: str | None
    authors: tuple[str,...]
    contributors: tuple[tuple[str,str],...]
    identifiers: tuple[str,...]
    manifest: dict[str,list[Resource]]
    spine: tuple[str,...]

    def resources(self):
        for group in self.manifest.values():
            yield from group


def target(part,href):
    try:
        url = urlsplit(href)
        if not url.path or url.query:
            return None
        path,_ = document_reference(part,href)
        return safe_name(path)
    except (Error,ValueError):
        return None


def package(data,part):
    try:
        root = xml(data)
    except Error:
        return None
    identity = etree.QName(root)
    if identity.localname!='package' or identity.namespace not in (OEB,OPF):
        return None
    ns = '{'+identity.namespace+'}'
    metadata = root.find(ns+'metadata')
    if metadata is None:
        return None
    fields = defaultdict(list)
    authors,contributors = [],[]
    for node in metadata.iter():
        if isinstance(node.tag,str) and node.tag.startswith(DC):
            value = ' '.join(''.join(node.itertext()).split())
            if value:
                field = name(node).lower()
                fields[field].append(value)
                if field in ('creator','contributor'):
                    role = (attr(node,'role') or '').lower()
                    if field=='creator' and role in ('','aut','author'):
                        authors.append(value)
                    else:
                        contributors.append((value,role or field))
    if not any(re.fullmatch(r'NIMAS(?:\s+1\.[01])?',value,re.I) for value in fields['format']):
        return None
    manifest = defaultdict(list)
    for item in root.findall(ns+'manifest/'+ns+'item'):
        if ident:=item.get('id'):
            href = item.get('href','')
            manifest[ident].append(Resource(href,target(part,href),item.get('media-type','')))
    spine = tuple(item.get('idref','') for item in root.findall(ns+'spine/'+ns+'itemref')
                  if item.get('linear','yes')!='no')
    return Package(next(iter(fields['title']),PurePosixPath(part).stem),
                   next(iter(fields['language']),root.get(XML_LANG)),tuple(authors),tuple(contributors),
                   tuple(fields['identifier']),dict(manifest),spine)


def folder_packages(root):
    books = []
    for path in sorted(root.iterdir()):
        if path.name.startswith('.') or path.suffix.casefold()!='.opf' or not path.is_file():
            continue
        if path.is_symlink() or path.is_junction() or path.stat().st_size>DESCRIPTOR_LIMIT:
            continue
        with path.open('rb') as stream:
            data = stream.read(DESCRIPTOR_LIMIT+1)
        if len(data)<=DESCRIPTOR_LIMIT and (parsed:=package(data,path.name)):
            books.append((path.name,parsed))
    return books


def archive_books(archive,members):
    by_name = defaultdict(list)
    for member in members:
        by_name[member.name].append(member)
    books,claimed = [],set()
    for descriptor in members:
        if (not descriptor.name.casefold().endswith('.opf') or
                descriptor.size is not None and descriptor.size>DESCRIPTOR_LIMIT):
            continue
        try:
            parsed = package(member_bytes(archive,descriptor,DESCRIPTOR_LIMIT),descriptor.name)
        except Error as exc:
            if exc.code!='source_structure_size':
                raise
            continue
        if parsed is None:
            continue
        books.append((parsed,descriptor))
        claimed.add(descriptor.index)
        for resource in parsed.resources():
            claimed.update(member.index for member in by_name.get(resource.target,()))
    return books,claimed


def capture_nimas(root,target_zip,ordinal,*,descriptor=None):
    names = [filename for filename,_ in folder_packages(root)]
    if descriptor is None:
        if not ordinal or not 0<ordinal<=len(names):
            raise Error('source_changed','The selected NIMAS package disappeared before capture.')
        descriptor = names[ordinal-1]

    def listed():
        path = relative_file(root,descriptor)
        before = stamp(path.stat())
        with path.open('rb') as stream:
            data = stream.read(DESCRIPTOR_LIMIT+1)
        if len(data)>DESCRIPTOR_LIMIT or stamp(path.stat())!=before or not (parsed:=package(data,descriptor)):
            raise Error('source_changed','The selected NIMAS package changed before capture.')
        files = {descriptor:before}
        for item in parsed.resources():
            if item.target is None:
                continue
            try:
                file = relative_file(root,item.target)
            except Error as exc:
                if exc.code!='source_member_missing':
                    raise
            else:
                files[item.target] = stamp(file.stat())
        return files

    files = listed()
    capture_folder(root,target_zip,lambda:files,verify=lambda _:listed(),include_root=False)
    return sorted(files).index(descriptor)+1


def read_nimas(path,source,sha,title):
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        descriptor = next((member for member in members if member.index==source.entry),None)
        if descriptor is None:
            raise Error('source_member_missing','The selected NIMAS descriptor is missing.')
        parsed = package(member_bytes(archive,descriptor,DESCRIPTOR_LIMIT),descriptor.name)
        if parsed is None:
            raise Error('source_nimas','The selected package does not declare a supported NIMAS edition.')
        by_name = defaultdict(list)
        for member in members:
            by_name[member.name].append(member)
        builder = DocumentBuilder('xml-book',parsed.title or title,sha)
        doc = builder.doc
        descriptor_ref = {**member_location(sha,descriptor,'descriptor'),'status':'preserved'}
        records = {descriptor.name:descriptor_ref}

        def scoped_issue(problem,index):
            block = builder.issue(problem,f'{descriptor.name}:spine/{index}')
            block.locator.update(member=descriptor.name,archive_entry=descriptor.index,
                                 location_id=key([sha,descriptor.index,block.id])[:20])

        for item in parsed.resources():
            choices = by_name.get(item.target,())
            if len(choices)==1:
                role = 'text' if item.media==DTBOOK else 'asset'
                resource = member_location(sha,choices[0],role)
                records[item.target] = {**resource,'media_type':item.media,'status':'preserved'}
            else:
                status = 'unresolved' if item.target is None or choices else 'missing'
                doc.inventory.append({'kind':'nimas_resource','member':item.target or item.href,'status':status,
                                      'media_type':item.media,'descriptor':descriptor.name})
        if not parsed.spine:
            scoped_issue('The NIMAS package has no authored reading order.',1)
        for occurrence,ident in enumerate(parsed.spine,1):
            candidates = parsed.manifest.get(ident,())
            if len(candidates)!=1:
                scoped_issue(f'NIMAS spine entry {ident!r} requires one manifest item.',occurrence)
                continue
            item, = candidates
            choices = by_name.get(item.target,())
            if item.media!=DTBOOK or len(choices)!=1:
                scoped_issue(f'NIMAS spine entry {ident!r} needs one readable DTBook member ({item.href!r}).',occurrence)
                continue
            body, = choices
            start = len(doc.blocks)
            try:
                read_dtbook(xml(member_bytes(archive,body,MAX_STRUCTURE)),builder,body.name,collect_resources=True)
            except Error as exc:
                scoped_issue(f'Cannot read {body.name}: {exc}',occurrence)
                continue
            records[body.name].update(status='represented',role='text',media_type=DTBOOK)
            for block in doc.blocks[start:]:
                block.locator.update(member=body.name,archive_entry=body.index,spine_occurrence=occurrence,
                                     location_id=key([sha,body.index,occurrence,block.id])[:20])
                for ref in block.locator.pop('resource_references',[]):
                    if ref['kind']!='image':
                        continue
                    asset = records.get(target(body.name,ref['href']))
                    if asset and asset['role']=='asset':
                        asset['status'] = 'referenced'
                        block.links.append({'kind':'source_resource','href':ref['href'],'label':ref.get('label'),
                                            'destinations':['source.html#resource-'+asset['location_id']]})
        doc.title = parsed.title or doc.title
        doc.language = parsed.language or doc.language
        if parsed.authors:
            doc.metadata['authors'] = list(parsed.authors)
        if parsed.contributors:
            doc.metadata['contributors'] = [{'name':author,'role':role} for author,role in parsed.contributors]
        if parsed.identifiers:
            doc.metadata['source_identifiers'] = list(parsed.identifiers)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(records.values())
        return doc
