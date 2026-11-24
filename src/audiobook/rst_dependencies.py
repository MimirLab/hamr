"""Resolve declared reStructuredText includes against the frozen source."""

from collections import defaultdict
from dataclasses import replace
import os
from pathlib import PurePosixPath
import posixpath
import re

from .archive_intake import containers
from .core import Error, key
from .document_intake import MAX_STRUCTURE
from .publication_package import capture_folder, member_bytes, member_location
from .rst_intake import decode_rst, rst_document
from .source_bundle import relative_file, safe_name, stamp

# The parser determines which directives actually read files. The hint skips
# the dependency pass for documents without even a possible directive.
INCLUDE = re.compile(r'\binclude\s*::',re.I)


def include_path(current,reference):
    relative = reference.replace('\\','/')
    if relative.startswith('/') or ':' in relative:
        raise Error('rst_include',f'Include {reference!r} must name a relative file within this source.')
    name = posixpath.normpath(posixpath.join(posixpath.dirname(current),relative))
    try:
        return safe_name(name)
    except Error as exc:
        raise Error('rst_include',f'Include {reference!r} from {current} leaves the source.') from exc


def read_local(path,stamps,name):
    with path.open('rb') as stream:
        version = stamp(os.fstat(stream.fileno()))
        if stamps.get(name,version)!=version or stamp(path.stat())!=version:
            raise Error('source_changed',f'Source changed while reading {name}.')
        if version[2]>MAX_STRUCTURE:
            raise Error('source_structure_size',f'This structured reader cannot buffer {name} within its memory budget.')
        data = stream.read(MAX_STRUCTURE+1)
        if stamp(os.fstat(stream.fileno()))!=version or stamp(path.stat())!=version or len(data)!=version[2]:
            raise Error('source_changed',f'Source changed while reading {name}.')
    stamps[name] = version
    return data


def capture_dependencies(path,target_dir,source):
    """Return a bundle only when the parser invokes a local include."""
    path = path.resolve() if path.is_symlink() else path
    root,main = path.parent,path.name
    observed = {}
    text = decode_rst(read_local(path,observed,main),source.encoding)
    if not INCLUDE.search(text):
        return None
    attempted = False
    changed = None

    def read(current,reference):
        nonlocal attempted,changed
        attempted = True
        candidate = include_path(current,reference)
        try:
            return candidate,read_local(relative_file(root,candidate),observed,candidate)
        except Error as exc:
            if exc.code=='source_changed':
                changed = exc
            raise
        except OSError as exc:
            raise Error('rst_include',f'Cannot read included source {candidate!r}: {exc.strerror}.') from exc

    rst_document(text,main,'',path.stem,include=read,encoding=source.encoding)
    if changed:
        raise changed
    if not attempted:
        return None
    target = target_dir/'source.zip'
    capture_folder(root,target,lambda:observed,include_root=False,
                   verify=lambda _: {name:stamp(relative_file(root,name).stat()) for name in observed})
    return target,replace(source,kind='zip-member',entry=sorted(observed).index(main)+1)


def read_rst_archive(path,source,sha):
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        main = next((item for item in members if item.index==source.entry),None)
        if main is None:
            raise Error('source_member_missing',f'Archive member {source.entry} is unavailable.')
        text = decode_rst(member_bytes(archive,main,MAX_STRUCTURE),source.encoding)
        path_name = PurePosixPath(main.name)
        title = path_name.stem
        logical = main.name if INCLUDE.search(text) else path_name.name
        by_name = defaultdict(list)
        for item in members:
            by_name[item.name].append(item)
        used = {logical:main}
        attempted = False

        def read(current,reference):
            nonlocal attempted
            attempted = True
            name = include_path(current,reference)
            choices = by_name.get(name,())
            if len(choices)!=1:
                detail = 'is repeated' if choices else 'is missing'
                raise Error('rst_include',f'Included source {name!r} {detail} in the archive.')
            member, = choices
            data = member_bytes(archive,member,MAX_STRUCTURE)
            used[name] = member
            return name,data

        doc = rst_document(text,logical,sha,title,include=read,encoding=source.encoding)
        if logical!=path_name.name and not attempted:
            doc = rst_document(text,path_name.name,sha,title,encoding=source.encoding)
    for block in doc.blocks:
        origin = used.get(block.locator.get('source_file'),main)
        block.locator.update(member=origin.name,archive_entry=origin.index,
                             location_id=key([sha,origin.index,block.id])[:20])
    doc.metadata['archive_entry'] = {'index':main.index,'name':main.name}
    if len(used)>1:
        resources = [member_location(sha,item,'text' if name==logical else 'include')|
                     {'status':'represented' if name==logical else 'included'} for name,item in used.items()]
        doc.metadata['source_resources'] = resources
        doc.inventory.extend(resources)
    return doc
