"""Keep the original PML text and its named local images together."""
from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import unquote

from .archive_intake import containers
from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read
from .pml_intake import Reading, decode, images
from .publication_package import bind_member_block, capture_folder, member_bytes, member_location
from .source_bundle import relative_file, stamp


def is_pml(name):
    return PurePosixPath(name).suffix.casefold()=='.pml'


def asset_paths(part,name):
    folder = PurePosixPath(part).parent
    return [folder/prefix/name for prefix in ('',PurePosixPath(part).stem+'_img','images')]


def assets_for(part,requested,by_name):
    result = {}
    for name in sorted(requested):
        for path in asset_paths(part,name):
            candidates = by_name.get(path.as_posix().casefold(),())
            if len(candidates)>1:
                raise Error('source_member_ambiguous',f'Palm image {name!r} matches more than one source member.')
            if candidates:
                result[name] = candidates[0]
                break
    return result


def by_casefold(members):
    result = defaultdict(list)
    for member in members:
        result[member.name.casefold()].append(member)
    return result


def archive_books(archive,members):
    available = by_casefold(members)
    claimed,books = set(),[]
    for part in members:
        if not is_pml(part.name):
            continue
        books.append(('pml',part,part.name))
        claimed.add(part.index)
        try:
            requested = images(decode(member_bytes(archive,part,MAX_STRUCTURE),None))
        except Error as exc:
            if exc.code in ('text_encoding','source_structure_size'):
                continue
            raise
        bound = assets_for(part.name,requested,available)
        claimed.update(asset.index for asset in bound.values())
    return books,claimed


def pmlz_parts(members):
    parts = [member for member in members if is_pml(member.name)]
    if not parts or len({PurePosixPath(part.name).parent for part in parts})!=1:
        raise Error('source_pmlz','A PMLZ needs PML text parts in a single book folder.')
    if len({part.name.casefold() for part in parts})!=len(parts):
        raise Error('source_member_ambiguous','A PMLZ repeats a text part name.')
    return sorted(parts,key=lambda part:part.name)


def loose_assets(path,requested):
    for name in sorted(requested):
        for candidate in asset_paths(path.name,name):
            source = path.parent/candidate
            if source.is_file() or source.is_symlink():
                yield candidate.as_posix()
                break


def discover_loose_assets(path):
    try:
        return list(loose_assets(path,images(decode(bounded_read(path),None))))
    except Error as error:
        if error.code not in ('text_encoding','source_structure_size'):
            raise
        return []


def capture_pml(path,target_dir,source):
    if path.is_symlink():
        path = path.resolve()
    root = path.parent
    original = stamp(path.stat())
    requested = images(decode(bounded_read(path),source.encoding))
    if stamp(path.stat())!=original:
        raise Error('source_changed','The PML text changed while resolving its images.')
    paths = {name:stamp(relative_file(root,name).stat()) for name in loose_assets(path,requested)}
    if not paths:
        return None
    paths[path.name] = original
    target = target_dir/'source.zip'
    capture_folder(root,target,lambda:paths,include_root=False,
                   verify=lambda _: {name:stamp(relative_file(root,name).stat()) for name in paths})
    return target,replace(source,kind='zip',entry=sorted(paths).index(path.name)+1)


def read_pml(path,source,sha,title):
    builder = DocumentBuilder('pml',title,sha)
    if source.kind=='file':
        part = source.member_name or path.name
        data = decode(bounded_read(path),source.encoding)
        Reading(data,part,builder).read()
        return builder.doc
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        parts = (pmlz_parts(members) if source.entry is None else
                 [part for part in members if part.index==source.entry and is_pml(part.name)])
        if not parts:
            raise Error('source_member_missing','The source contains no selected PML book part.')
        available = by_casefold(members)
        resources = {}
        for occurrence,part in enumerate(parts,1):
            if len(available[part.name.casefold()])!=1:
                raise Error('source_member_ambiguous','The source repeats its selected PML book part.')
            data = decode(member_bytes(archive,part,MAX_STRUCTURE),source.encoding)
            first = len(builder.doc.blocks)
            Reading(data,part.name,builder).read()
            lookup = assets_for(part.name,images(data),available)
            addresses = {}
            for name,member in lookup.items():
                record = member_location(sha,member,'asset')
                resources[record['location_id']] = record
                addresses[name] = record
            for block in builder.doc.blocks[first:]:
                bind_member_block(block,part,occurrence,sha,addresses,lambda _,href:unquote(href))
        builder.doc.metadata['source_resources'] = list(resources.values())
    return builder.doc
