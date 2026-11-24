"""Read FictionBook 3 publication boundaries and assets from OPC declarations."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from posixpath import dirname

from .archive_intake import Member, containers
from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, xml, xml_start
from .opc_source import Parts, TYPES, TYPES_NAME, manifest_xml, reference
from .publication_package import bind_member_block, member_bytes, member_location
from .xml_projection import element_text

BASE = 'http://www.fictionbook.org/FictionBook3/'
BODY = '{'+BASE+'body}fb3-body'
DESCRIPTION = '{'+BASE+'description}fb3-description'
BOOK_LINK = BASE+'relationships/Book'
BODY_LINK = BASE+'relationships/body'
IMAGE_LINK = BASE+'relationships/image'
BODY_TYPE = 'application/fb3-body+xml'
DESCRIPTION_TYPE = 'application/fb3-description+xml'
MANIFEST_LIMIT = 1024*1024


def declarations(node):
    overrides = [(item.get('PartName',''),item.get('ContentType','')) for item in node.findall(TYPES+'Override')]
    defaults = [(item.get('Extension','').lower(),item.get('ContentType','')) for item in node.findall(TYPES+'Default')]
    return overrides,defaults


def declares_fb3(node):
    return any(kind in (BODY_TYPE,DESCRIPTION_TYPE) for group in declarations(node) for _,kind in group)


def folder_marker(path):
    for marker in path.iterdir():
        if marker.name.lower()!=TYPES_NAME.lower() or marker.is_symlink() or not marker.is_file():
            continue
        with marker.open('rb') as stream:
            raw = stream.read(MANIFEST_LIMIT+1)
        if len(raw)<=MANIFEST_LIMIT and (node:=manifest_xml(raw)) is not None and declares_fb3(node):
            return True
    return False


def text_at(node,path):
    value = node.find(path) if node is not None else None
    return element_text(value) if value is not None else ''


def unique(choices):
    members = tuple(dict.fromkeys(choices))
    return members[0] if len(members)==1 else None


@dataclass(frozen=True)
class Edition:
    title: str
    description: Member | None
    body: Member | None
    metadata: object | None
    originals: dict
    image_targets: dict
    inventory: tuple
    problem: str | None


def identify(archive,manifest,members):
    kind = manifest_xml(member_bytes(archive,manifest,MANIFEST_LIMIT))
    if kind is None:
        return None
    prefix = dirname(manifest.name)
    scope = prefix+'/' if prefix else ''
    parts = Parts(scope,members)
    legacy = None
    if not declares_fb3(kind):
        legacy = parts.find('fb3-body.xml')
        if legacy is None:
            return None
        with archive.open_member(legacy) as stream:
            element = xml_start(stream)
            if element is None or element.tag!=BODY:
                return None
    read = lambda member: member_bytes(archive,member,MAX_STRUCTURE)
    read_rels = lambda member: member_bytes(archive,member,MANIFEST_LIMIT)
    overrides,defaults = declarations(kind)
    declared = {media for group in (overrides,defaults) for _,media in group}
    originals = {manifest:'manifest'}
    typed = defaultdict(list)
    overridden = set()
    for address,media in overrides:
        logical = reference('',address)
        if logical:
            overridden.add(logical)
        if (physical:=parts.one(logical)) is not None:
            typed[media].append(physical)
            originals[physical] = 'asset' if media.startswith('image/') else 'package'
    default_types = defaultdict(set)
    for suffix,media in defaults:
        default_types[suffix].add(media)
    for logical in parts.index:
        if logical in overridden:
            continue
        types = default_types.get(PurePosixPath(logical).suffix.removeprefix('.'),())
        if len(types)==1 and (member:=parts.one(logical)) is not None:
            media, = types
            if media in (BODY_TYPE,DESCRIPTION_TYPE):
                typed[media].append(member)
            elif media.startswith(('image/','audio/','video/','font/')) or media=='text/css':
                originals[member] = 'asset'

    inventory = []

    def related(owner):
        rels,links = parts.relationships(owner,read_rels)
        if rels is not None:
            originals[rels] = 'relationships'
        for link in links:
            if link.member is not None:
                role = 'asset' if link.kind==IMAGE_LINK or link.kind.endswith('/thumbnail') else 'package'
                originals.setdefault(link.member,role)
            inventory.append({'kind':'fb3_relationship','member':rels.name,'id':link.id,
                              'type':link.kind,'target':link.target,
                              'resolved_member':link.member.name if link.member is not None else None,
                              'status':'external' if link.external else 'bound' if link.member is not None else 'unavailable'})
        return links

    problem = None
    description = None
    body = None
    metadata = None
    images = {}
    try:
        if parts.find(TYPES_NAME)!=manifest:
            raise Error('source_fb3_package','The archive contains equivalent package content-type manifests.')
        roots = [link for link in related(None) if link.kind==BOOK_LINK]
        description = roots[0].member if len(roots)==1 else unique(typed[DESCRIPTION_TYPE]) if not roots else None
        if roots and (description is None or DESCRIPTION_TYPE in declared and description not in typed[DESCRIPTION_TYPE]):
            raise Error('source_fb3_description','FictionBook 3 does not identify one consistent book description.')
        if description is not None:
            metadata = xml(read(description))
            if metadata.tag!=DESCRIPTION:
                raise Error('source_fb3_description','The selected FictionBook 3 description has an unexpected XML root.')
            originals[description] = 'description'
        bodies = [link for link in related(description) if link.kind==BODY_LINK] if description is not None else []
        if bodies:
            body = bodies[0].member if len(bodies)==1 else None
            if BODY_TYPE in declared and body not in typed[BODY_TYPE]:
                body = None
        else:
            body = unique(typed[BODY_TYPE]) or legacy
        if body is None:
            raise Error('source_fb3_body','FictionBook 3 does not identify one local declared body part.')
        originals[body] = 'text'
        targets = defaultdict(list)
        for link in related(body):
            if link.id:
                targets[link.id].append(link)
        images = {ident:only.member for ident,links in targets.items()
                  if len(links)==1 and (only:=links[0]).kind==IMAGE_LINK}
    except Error as exc:
        problem = str(exc)
    title = text_at(metadata,'{*}title/{*}main') or (PurePosixPath(prefix).name if prefix else 'FictionBook 3')
    return Edition(title,description,body,metadata,originals,images,tuple(inventory),problem)


def archive_books(archive,members):
    editions,claimed = [],set()
    for manifest in members:
        if PurePosixPath(manifest.name).name.lower()!=TYPES_NAME.lower():
            continue
        if manifest.size is not None and manifest.size>MANIFEST_LIMIT:
            continue
        if (edition:=identify(archive,manifest,members)) is not None:
            editions.append((manifest,edition.title))
            claimed.update(item.index for item in edition.originals)
    return editions,claimed


def read_fb3(path,source,sha,title):
    from .fictionbook_intake import read_fictionbook

    kind = 'zip' if source.kind=='file' else source.kind
    with containers(path,kind) as archive:
        members = list(archive.members())
        manifests = [item for item in members if PurePosixPath(item.name).name.lower()==TYPES_NAME.lower()
                     and (source.entry is None or item.index==source.entry)]
        editions = [result for item in manifests if (result:=identify(archive,item,members)) is not None]
        if len(editions)!=1:
            raise Error('source_fb3_package','Select an archive with one declared FictionBook 3 publication.')
        edition, = editions
        if edition.problem:
            raise Error('source_fb3_package',edition.problem,'Open the complete FictionBook 3 edition.')
        root = xml(member_bytes(archive,edition.body,MAX_STRUCTURE))
        if root.tag!=BODY or not root.findall('{*}section'):
            raise Error('source_fb3_body','The declared FictionBook 3 body has no readable sections.')
        builder = DocumentBuilder('fb3',title,sha)
        read_fictionbook(root,builder,edition.body.name,metadata=edition.metadata,collect_resources=True)
        doc = builder.doc
        descriptor_id = edition.metadata.get('id') if edition.metadata is not None else None
        body_id = root.get('id')
        if descriptor_id and body_id and descriptor_id.lower()!=body_id.lower():
            doc.inventory.append({'kind':'fb3_identifiers','member':edition.description.name,'body_member':edition.body.name,
                                  'description_id':descriptor_id,'body_id':body_id,'status':'different'})
        records = {member.name:{**member_location(sha,member,role),'status':'represented' if role=='text' else 'preserved'}
                   for member,role in edition.originals.items()}
        def image_target(_part,ident):
            target = edition.image_targets.get(ident)
            return target.name if target is not None else None

        for position,block in enumerate(doc.blocks,1):
            bind_member_block(block,edition.body,position,sha,records,image_target)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(edition.inventory)
        doc.inventory.extend(records.values())
        return doc
