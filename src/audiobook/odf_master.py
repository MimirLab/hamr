"""Read and preserve the ordered local sections of a Writer master document."""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import PurePosixPath
import shutil
import tempfile

from .archive_intake import containers
from .core import Error, key
from .document_intake import DocumentBuilder, visit_odf
from .odf_content import TEXT, XLINK_HREF
from .odf_sections import saved_content, section_target
from .publication_package import capture_folder, member_location
from .source_bundle import CHUNK, relative_file, stamp


MAX_LINK_DEPTH = 32
MAX_LINK_OCCURRENCES = 4096
INPUT_ERRORS = frozenset(('source_member_missing','unsafe_source_member','unreadable_archive',
                          'ambiguous_package_member','source_xml','source_structure_size',
                          'odf_section','odf_link','disk_space'))


class LocalDocuments:
    def __init__(self,root):
        self.root = root
        self.stamps = {}

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
    def __init__(self,archive,members):
        self.archive = archive
        self.by_name = defaultdict(list)
        for member in members:
            self.by_name[member.name].append(member)

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
    def __init__(self,documents,main,sha):
        self.documents = documents
        self.main = main
        self.sha = sha
        self.builder = DocumentBuilder('odt',PurePosixPath(main).stem,sha)
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
        with self.documents.open(name) as (stream,member):
            packaged = stream.read(4).startswith(b'PK')
            stream.seek(0)
            def section(element,source,location):
                return self.read_section(element,source,location,trail,packaged,member)

            visit_odf(stream,builder,name,region=region,link_section=section,use_title=not ancestors)
        if member:
            self.resources.setdefault(member.index,member_location(self.sha,member,'text' if not ancestors else 'include'))
        for block in doc.blocks[position:]:
            if 'odf_document' in block.locator:
                continue
            block.locator['odf_document'] = name
            if member:
                block.locator.update(member=name,archive_entry=member.index,
                                     location_id=key([self.sha,member.index,block.id])[:20])

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


def archive_claims(archive,members):
    roots = [item.name for item in members if PurePosixPath(item.name).suffix.casefold()=='.odm']
    if not roots:
        return set()
    documents = ArchivedDocuments(archive,members)
    claimed = selected_dependencies(documents,roots)
    return {item.index for item in members if item.name in claimed}


def capture_master(path,target_dir,source,*,root=None):
    path = path.resolve() if path.is_symlink() else path
    root = root if root is not None else path.parent
    name = path.relative_to(root).as_posix()
    documents = LocalDocuments(root)
    MasterBook(documents,name,'').read()
    target = target_dir/'source.zip'
    capture_folder(root,target,lambda:documents.stamps,include_root=False,
                   verify=lambda _: {name:stamp(relative_file(root,name).stat()) for name in documents.stamps})
    return target,replace(source,kind='zip-member',entry=sorted(documents.stamps).index(name)+1,member_name=None)


def read_master(path,source,sha):
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        entry = next((item for item in members if item.index==source.entry),None)
        if entry is None:
            raise Error('source_member_missing',f'Writer master entry {source.entry} is unavailable.')
        book = MasterBook(ArchivedDocuments(archive,members),entry.name,sha)
        doc = book.read()
        doc.metadata['archive_entry'] = {'index':entry.index,'name':entry.name}
        return doc
