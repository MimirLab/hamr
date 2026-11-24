"""Read a descriptor-selected text payload and preserve its original resources."""

import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .archive_intake import containers
from .core import Error, key
from .source_bundle import MAX_MANIFEST, copy_stream, relative_file, safe_name, stamp

MARKDOWN = 'net.daringfireball.markdown'
KNOWN_TYPES = {
    MARKDOWN: ('markdown',None), 'public.markdown': ('markdown',None),
    'public.plain-text': ('text',None), 'public.utf8-plain-text': ('text','utf-8-sig'),
    'public.utf16-plain-text': ('text','utf-16'), 'public.html': ('html',None),
    'public.rtf': ('rtf',None),
}
TEXT_SUFFIXES = {'.md':'markdown','.markdown':'markdown','.txt':'text','.text':'text',
                 '.html':'html','.htm':'html','.xhtml':'html','.rtf':'rtf'}


def package_members(descriptor,members):
    base = posixpath.dirname(descriptor.name)
    if not base:
        return members
    return [member for member in members if member.name.startswith(base+'/')]


def document_members(descriptor,members):
    return [member for member in members if posixpath.dirname(member.name)==posixpath.dirname(descriptor.name)
            and PurePosixPath(member.name).name.startswith('text.')]


def read_member(archive,member,limit):
    if member.size is not None and member.size>limit:
        raise Error('source_structure_size',f'TextBundle member exceeds its reading budget: {member.name}.')
    with archive.open_member(member) as stream:
        data = stream.read(limit+1)
    if len(data)>limit:
        raise Error('source_structure_size',f'TextBundle member exceeds its reading budget: {member.name}.')
    return data


def archive_descriptors(archive,members,*,explicit=False):
    picked = {}
    descriptors = [member for member in members if PurePosixPath(member.name).name=='info.json']
    if not descriptors:
        return []
    body_folders = {posixpath.dirname(member.name) for member in members
                    if PurePosixPath(member.name).name.startswith('text.')}
    for descriptor in sorted(descriptors,key=lambda item:(item.name.count('/'),item.index)):
        base = posixpath.dirname(descriptor.name)
        if any(parent.as_posix() in picked for parent in PurePosixPath(descriptor.name).parents):
            continue
        named_bundle = PurePosixPath(base).suffix.casefold()=='.textbundle'
        if named_bundle or (explicit and len(descriptors)==1):
            picked[base or '.'] = descriptor
            continue
        if base not in body_folders:
            continue
        if explicit:
            picked[base or '.'] = descriptor
            continue
        if descriptor.size is None or descriptor.size>MAX_MANIFEST:
            continue
        try:
            metadata = json.loads(read_member(archive,descriptor,MAX_MANIFEST))
        except (UnicodeError,ValueError):
            continue
        if isinstance(metadata,dict) and type(metadata.get('version')) is int and metadata['version'] in (1,2):
            picked[base or '.'] = descriptor
    return list(picked.values())


def archive_scopes(archive,members,*,explicit=False):
    descriptors = archive_descriptors(archive,members,explicit=explicit)
    roots = {PurePosixPath(item.name).parent for item in descriptors}
    claimed = ({member.index for member in members
                if any(parent in roots for parent in PurePosixPath(member.name).parents)}
               if roots else set())
    return descriptors,claimed


def folder_files(root):
    result = {}

    def fail(error):
        raise error

    for path,dirs,files in os.walk(root,onerror=fail,followlinks=False):
        here = Path(path)
        for name in dirs:
            child = here/name
            if child.is_symlink() or child.is_junction():
                raise Error('unsafe_source_member','A TextBundle directory entry follows a link.')
        for name in files:
            relative = (here/name).relative_to(root).as_posix()
            child = relative_file(root,relative)
            result[relative] = stamp(child.stat())
    return result


def capture_textbundle(root,target):
    """Freeze folder members as one deterministic, portable TextPack."""
    listed = folder_files(root)
    if 'info.json' not in listed:
        raise Error('source_textbundle','The TextBundle folder has no info.json descriptor.')
    budget = sum(value[2]+1024 for value in listed.values())
    if shutil.disk_usage(target.parent).free<budget:
        raise Error('disk_space',f'Need at least {budget:,} bytes to save the original TextBundle.')
    with ZipFile(target,'x',compression=ZIP_STORED,allowZip64=True) as archive:
        for name,before in sorted(listed.items()):
            source = relative_file(root,name)
            if stamp(source.stat())!=before:
                raise Error('source_changed',f'TextBundle member changed during capture: {name}.')
            record = ZipInfo(safe_name(root.name+'/'+name))
            record.create_system = 3
            record.external_attr = (stat.S_IFREG|0o400)<<16
            with archive.open(record,'w',force_zip64=True) as destination:
                copy_stream(source,destination)
        if folder_files(root)!=listed:
            raise Error('source_changed','TextBundle members changed during capture.')


def declaration(raw,body):
    try:
        info = json.loads(raw)
    except (ValueError,UnicodeError) as exc:
        raise Error('source_textbundle','Cannot read the TextBundle info.json descriptor.') from exc
    if not isinstance(info,dict) or type(info.get('version')) is not int or info['version'] not in (1,2):
        raise Error('source_textbundle','The descriptor must declare TextBundle version 1 or 2.')
    version = info['version']
    declared = info.get('type',MARKDOWN) if version==2 else MARKDOWN
    if not isinstance(declared,str):
        raise Error('source_textbundle','The TextBundle text type must be a string.')
    if declared in KNOWN_TYPES:
        route,encoding = KNOWN_TYPES[declared]
    elif declared=='public.text' and (route:=TEXT_SUFFIXES.get(PurePosixPath(body.name).suffix.casefold())):
        encoding = None
    else:
        raise Error('source_textbundle_type',f'The TextBundle declares an unsupported text type: {declared}.',
                    'Provide a package with supported text or markup.')
    return version,declared,route,encoding


def text_document(raw,route,encoding,title,sha):
    from .document_intake import DocumentBuilder, html_read
    from .importers import markdown, plain
    from .rtf_intake import read_rtf

    if route=='markdown':
        if encoding:
            try:
                raw = raw.decode(encoding).encode('utf-8')
            except (UnicodeError,LookupError) as exc:
                raise Error('text_encoding',f'Cannot decode TextBundle Markdown using {encoding}.') from exc
        document = markdown(raw,collect_resources=True)
        if document.title=='Document' and title:
            document.title = title
        return document
    if route=='text':
        document = plain(raw,encoding)
        if title:
            document.title = title
        return document
    builder = DocumentBuilder(route,title,sha)
    if route=='rtf':
        read_rtf(raw,builder)
    else:
        html_read(raw,builder,encoding=encoding,collect_resources=True)
    return builder.doc


def locator(sha,member,role):
    return {'member':member.name,'archive_entry':member.index,'size':member.size,'role':role,
            'location_id':key([sha,member.index,member.name])[:20]}


def asset_target(href,base):
    try:
        url = urlsplit(href)
        if url.scheme or url.netloc:
            return None,'external'
        path = unquote(url.path,errors='strict')
        if not path.startswith('assets/') or url.query:
            return None,'outside-assets'
        return safe_name(posixpath.join(base,path)),'expected'
    except (Error,ValueError,UnicodeError):
        return None,'outside-assets'


def bind_resources(document,members,descriptor,body,sha):
    base = posixpath.dirname(descriptor.name)
    prefix = posixpath.join(base,'assets')+'/'
    resources = {}
    for member in members:
        if member.index==body.index:
            continue
        kind = 'descriptor' if member.index==descriptor.index else 'asset' if member.name.startswith(prefix) else 'other'
        resources[member.name] = {**locator(sha,member,kind),'status':'preserved'}
    body_location = locator(sha,body,'text')
    for block in document.blocks:
        block.locator.update(member=body.name,archive_entry=body.index,
                             location_id=key([sha,body.index,block.id])[:20])
        for ref in block.locator.pop('resource_references',[]):
            path,status = asset_target(ref['href'],base)
            resource = resources.get(path)
            if resource and resource['role']=='asset':
                resource['status'] = 'referenced'
                block.links.append({'kind':'source_resource','href':ref['href'],
                                    'label':ref.get('label') or PurePosixPath(path).name,
                                    'destinations':['source.html#resource-'+resource['location_id']]})
            elif path:
                document.inventory.append({'member':path,'status':'missing','reason':'Referenced TextBundle asset is absent.'})
                document.warnings.append(f'TextBundle asset is missing: {path}.')
            elif status!='external' and ref['kind']=='image':
                document.inventory.append({'member':ref['href'],'status':'unresolved',
                                           'reason':'Asset reference is outside the package assets directory.'})
    document.inventory.extend([body_location|{'status':'represented'},*resources.values()])
    document.metadata['source_resources'] = list(resources.values())


def read_textbundle(path,source,sha,title):
    from .document_intake import MAX_STRUCTURE
    from .importers import MAX_INPUT

    with containers(path,source.kind) as archive:
        indexed = [member for member in archive.members() if not member.name.startswith('__MACOSX/')]
        descriptor = next((member for member in indexed if member.index==source.entry),None)
        if descriptor is None or PurePosixPath(descriptor.name).name!='info.json':
            raise Error('source_textbundle','The selected TextBundle descriptor is unavailable.')
        members = package_members(descriptor,indexed)
        if len({member.name for member in members})!=len(members):
            raise Error('ambiguous_package_member','The selected TextBundle repeats a member path.')
        bodies = document_members(descriptor,members)
        if len(bodies)!=1:
            raise Error('source_textbundle','The bundle must contain exactly one text.* file beside info.json.')
        body, = bodies
        version,declared,route,encoding = declaration(read_member(archive,descriptor,MAX_MANIFEST),body)
        limit = MAX_INPUT if route in ('markdown','text') else MAX_STRUCTURE
        package_name = PurePosixPath(descriptor.name).parent.stem or title
        document = text_document(read_member(archive,body,limit),route,source.encoding or encoding,package_name,sha)
    document.source_sha256 = sha
    document.metadata['textbundle'] = {'version':version,'type':declared,'text_member':body.name,
                                       'descriptor':descriptor.name}
    bind_resources(document,members,descriptor,body,sha)
    return document
