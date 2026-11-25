"""Read and preserve the ordered local sections of a Writer master document."""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import PurePosixPath
import shutil
import tempfile

from .archive_intake import Member, containers
from .core import Error, key
from .document_intake import DocumentBuilder, odf_parts, visit_odf_parts, xml
from .odf_content import TEXT, XLINK_HREF
from .odf_package import MASTER_TYPES, WRITER_MEDIA, PackageParts, archive_books, folder_marker, marker_prefix
from .odf_sections import LINKED_SUFFIXES, saved_content, section_target
from .publication_package import capture_folder, folder_files, member_location
from .source_bundle import CHUNK, relative_file, relative_path, stamp


MAX_LINK_DEPTH = 32
MAX_LINK_OCCURRENCES = 4096
INPUT_ERRORS = frozenset(('source_member_missing','unsafe_source_member','unreadable_archive',
                          'ambiguous_package_member','source_xml','source_structure_size',
                          'odf_section','odf_link','disk_space'))


class LocalDocuments:
    recoverable = False

    def __init__(self,root):
        self.root = root
        self.stamps = {}
        self.expanded = {}

    def path(self,name):
        return relative_path(self.root,name) if name else self.root

    def is_package(self,name):
        path = self.path(name)
        return path.is_dir() and folder_marker(path,WRITER_MEDIA | MASTER_TYPES)

    def package(self,name):
        if name not in self.expanded:
            prefix = name+'/' if name else ''
            observed = {prefix+part:version for part,version in folder_files(self.path(name)).items()}
            for part,version in observed.items():
                if self.stamps.get(part,version)!=version:
                    raise Error('source_changed',f'Writer source changed while reading {part}.')
                self.stamps[part] = version
            members = [Member(index,part,version[2]) for index,(part,version) in enumerate(observed.items(),1)]
            self.expanded[name] = (PackageParts(self,members,prefix),observed)
        return self.expanded[name][0]

    def verify(self,listed):
        for name,(_,observed) in self.expanded.items():
            current = folder_files(self.path(name))
            prefix = name+'/' if name else ''
            if {prefix+part:version for part,version in current.items()}!=observed:
                raise Error('source_changed',f'Writer package parts changed during capture: {name or self.root.name}.')
        return {name:stamp(relative_file(self.root,name).stat()) for name in listed}

    @contextmanager
    def open_member(self,member):
        with self.open(member.name) as (stream,_):
            yield stream

    @contextmanager
    def open(self,name):
        path = relative_file(self.root,name)
        before = stamp(path.stat())
        if self.stamps.get(name,before)!=before:
            raise Error('source_changed',f'Writer source changed while reading {name}.')
        with path.open('rb') as stream:
            if stamp(os.fstat(stream.fileno()))!=before:
                raise Error('source_changed',f'Writer source changed before reading {name}.')
            self.stamps[name] = before
            try:
                yield stream,None
            finally:
                if stamp(os.fstat(stream.fileno()))!=before or stamp(path.stat())!=before:
                    raise Error('source_changed',f'Writer source changed while reading {name}.')


class ArchivedDocuments:
    recoverable = True

    def __init__(self,archive,members,packages):
        self.archive = archive
        self.members = members
        self.expanded = {marker_prefix(item.name).rstrip('/'):item for route,item,_ in packages
                         if route in ('odt','odf-master')}
        self.by_name = defaultdict(list)
        for member in members:
            self.by_name[member.name].append(member)

    def is_package(self,name):
        return name in self.expanded

    def package(self,name):
        return PackageParts(self.archive,self.members,marker_prefix(self.expanded[name].name))

    def member(self,name):
        options = self.by_name.get(name,())
        if len(options)>1:
            raise Error('ambiguous_package_member',f'The archive has more than one Writer member named {name!r}.')
        if not options:
            raise Error('source_member_missing',f'The Writer document {name!r} is missing from the archive.')
        return options[0]

    @contextmanager
    def open(self,name):
        member = self.member(name)
        with self.archive.open_member(member) as source:
            if source.seekable():
                yield source,member
                return
            remaining = shutil.disk_usage(tempfile.gettempdir()).free
            if member.size is not None and member.size>remaining:
                raise Error('disk_space',f'Cannot open the Writer member {name!r} in temporary storage.')
            with tempfile.TemporaryFile() as staged:
                while chunk:=source.read(CHUNK):
                    remaining -= len(chunk)
                    if remaining<0:
                        raise Error('disk_space',f'The Writer member {name!r} exceeds temporary storage.')
                    staged.write(chunk)
                if member.size is not None and staged.tell()!=member.size:
                    raise Error('unreadable_archive',f'The Writer member {name!r} ended unexpectedly.')
                staged.seek(0)
                yield staged,member


class MasterBook:
    def __init__(self,documents,main,sha,title=None):
        self.documents = documents
        self.main = main
        self.sha = sha
        self.builder = DocumentBuilder('odt',title or PurePosixPath(main).stem,sha)
        self.claimed = set()
        self.occurrences = 0
        self.resources = {}

    def read(self):
        self.read_member(self.main,None,())
        doc = self.builder.doc
        if self.resources:
            doc.metadata['source_resources'] = list(self.resources.values())
        return doc

    def read_member(self,name,region,ancestors):
        if (name,region) in ancestors:
            raise Error('odf_link',f'The Writer link returns to {name!r} and forms a cycle.')
        if len(ancestors)>=MAX_LINK_DEPTH or self.occurrences>=MAX_LINK_OCCURRENCES:
            raise Error('odf_link','The Writer document exceeds the linked-section depth or occurrence budget.')
        self.occurrences += 1
        builder = self.builder
        doc = builder.doc
        position = len(doc.blocks)
        trail = (*ancestors,(name,region))
        with self.parts(name) as (document,read,member,resources):
            def section(element,source,location):
                return self.read_section(element,source,location,trail,read is not None,member)

            visit_odf_parts(document,read,builder,name,region=region,link_section=section,use_title=not ancestors)
        for item in resources:
            role = ('include' if ancestors else 'text') if item==member else 'package_member'
            self.resources.setdefault(item.index,member_location(self.sha,item,role))
        for block in doc.blocks[position:]:
            if 'odf_document' in block.locator:
                continue
            block.locator['odf_document'] = name
            if member:
                block.locator.update(member=member.name,archive_entry=member.index,
                                     location_id=key([self.sha,member.index,block.id])[:20])

    @contextmanager
    def parts(self,name):
        if self.documents.is_package(name):
            package = self.documents.package(name)
            package.marker()
            content = package.member('content.xml')
            resources = [items[0] for items in package.members.values() if len(items)==1]
            if not self.documents.recoverable:
                content,resources = None,()
            yield xml(package.read('content.xml')),package.read,content,resources
        else:
            with self.documents.open(name) as (stream,member), odf_parts(stream) as (document,read):
                yield document,read,member,(member,) if member else ()

    def read_section(self,element,source,location,trail,packaged,member):
        builder = self.builder
        doc = builder.doc
        name,_ = trail[-1]
        saved = saved_content(element)
        receipt = {'kind':'odf_linked_section','odf_document':name,'element':location,
                   'section':element.get(TEXT+'name'),'href':source.get(XLINK_HREF,'')}
        if member:
            receipt.update(member=member.name,archive_entry=member.index)
        doc.inventory.append(receipt)
        start,inventory = len(doc.blocks),len(doc.inventory)
        chapter,chapter_id = builder.chapter,builder.chapter_id
        try:
            target,region = section_target(name,source,packaged=packaged)
            if (target!=name and PurePosixPath(target).suffix.casefold() not in LINKED_SUFFIXES and
                    not self.documents.is_package(target)):
                raise Error('odf_link','The Writer link needs a local OpenDocument text file or package.')
            receipt.update(target=target,region=region)
            if target!=name:
                self.claimed.add(target)
            if not saved:
                self.read_member(target,region,trail)
        except (Error,OSError) as error:
            if isinstance(error,Error) and error.code not in INPUT_ERRORS:
                raise
            if not saved:
                del doc.blocks[start:]
                del doc.inventory[inventory:]
                builder.chapter,builder.chapter_id = chapter,chapter_id
                detail = error.message if isinstance(error,Error) else f'The chapter could not be opened. {error.strerror}'
                receipt.update(status='unresolved',detail=detail)
                builder.issue(f'Writer could not include this section. {detail}',location)
                return True
        receipt['status'] = 'saved' if saved else 'included'
        return not saved


def selected_dependencies(documents,names):
    """Find chapter ownership without collapsing a cyclic set of masters."""
    roots = {}
    for name in names:
        book = MasterBook(documents,name,'')
        try:
            book.read()
        except Error as error:
            if error.code not in INPUT_ERRORS:
                raise
        else:
            roots[name] = book.claimed
    return {target for name,owned in roots.items() for target in owned if name not in roots.get(target,())}


def folder_claims(root,names):
    return selected_dependencies(LocalDocuments(root),names)


def archive_claims(archive,members,packages,claimed):
    expanded = [marker_prefix(item.name).rstrip('/') for route,item,_ in packages if route=='odf-master']
    roots = expanded+[item.name for item in members
                      if item.index not in claimed and PurePosixPath(item.name).suffix.casefold()=='.odm']
    if not roots:
        return set()
    documents = ArchivedDocuments(archive,members,packages)
    linked = selected_dependencies(documents,roots)
    return ({item.index for item in members if item.name in linked} |
            {documents.expanded[name].index for name in linked if name in documents.expanded})


def capture_master(path,target_dir,source,*,root=None):
    path = path.resolve() if path.is_symlink() else path
    root = root if root is not None else path.parent
    name = '' if path==root else path.relative_to(root).as_posix()
    documents = LocalDocuments(root)
    MasterBook(documents,name,'').read()
    target = target_dir/'source.zip'
    capture_folder(root,target,lambda:documents.stamps,include_root=False,verify=documents.verify)
    expanded = source.kind=='directory'
    original = documents.package(name).marker().name if expanded else name
    ordinal = sorted(documents.stamps).index(original)+1
    return target,replace(source,kind='zip' if expanded else 'zip-member',entry=ordinal,member_name=None)


def read_master(path,source,sha,title):
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        entry = next((item for item in members if item.index==source.entry),None)
        if entry is None:
            raise Error('source_member_missing',f'Writer master entry {source.entry} is unavailable.')
        packages,_ = archive_books(archive,members)
        if source.kind.endswith('-member'):
            name = entry.name
        else:
            if not any(item.index==entry.index and route=='odf-master' for route,item,_ in packages):
                raise Error('source_member_missing','The source has no selected expanded Writer master package.')
            name = marker_prefix(entry.name).rstrip('/')
        book = MasterBook(ArchivedDocuments(archive,members,packages),name,sha,title if not name else None)
        doc = book.read()
        doc.metadata['archive_entry'] = {'index':entry.index,'name':entry.name}
        return doc
