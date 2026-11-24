"""Resolve the files and authored playback sequence of a DAISY 2.02 book."""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup

from .archive_intake import Member, containers
from .core import Error
from .daisy_intake import HTML_TYPE, read_synchronized, smil_body
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read, name
from .opf_intake import DTBOOK, target
from .publication_package import bind_member_block, capture_folder, member_bytes, member_location
from .source_bundle import relative_file, stamp

SMIL = ('.smil','.smi')
MASTER_NAMES = ('master.smil','MASTER.SMIL')
HTML = ('.html','.htm','.xhtml','.ncc')
TEXT_MEDIA = {**dict.fromkeys(HTML,HTML_TYPE),'.xml':DTBOOK,'.dtbook':DTBOOK}
PAGE_CLASSES = {'page-front','page-normal','page-special'}


def ncc_name(path):
    leaf = PurePosixPath(path).name.casefold()
    return leaf in ('ncc.html','ncc.htm','ncc.xhtml') or leaf.endswith('.ncc')


def descriptor_name(path):
    return ncc_name(path) or PurePosixPath(path).suffix.casefold() in SMIL


@dataclass(frozen=True)
class Reference:
    source: Member
    href: str
    line: int | None

    def path(self):
        return target(self.source.name,self.href)


@dataclass
class Book:
    title: str
    fields: dict = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    tracks: list = field(default_factory=list)
    navigation: list[Reference] = field(default_factory=list)
    note_targets: dict = field(default_factory=lambda: defaultdict(set))
    warnings: list[str] = field(default_factory=list)
    body_references: bool = False
    has_audio: bool = False
    opaque_track: bool = False


def smil_link(href):
    try:
        return PurePosixPath(unquote(urlsplit(href).path)).suffix.casefold() in SMIL
    except ValueError:
        return False


def document_html(raw):
    return BeautifulSoup(raw,'html.parser')


def read_daisy_html(raw,builder,part,note_targets):
    html = document_html(raw)
    annotations = {}
    for label,node in enumerate(html.find_all(True),1):
        marker = str(label)
        node['data-hamr-node'] = marker
        location = {'source_file':part}
        if node.sourceline:
            location['lines'] = [node.sourceline,node.sourceline]
        anchor = node.get('id') or (node.name=='a' and node.get('name'))
        if anchor:
            node['id'] = anchor
            location['anchor'] = anchor
        classes = set(node.get('class',()))
        if PAGE_CLASSES.intersection(classes):
            node['role'] = 'doc-pagebreak'
            node['aria-hidden'] = 'true'
        elif 'notebody' in classes:
            node['role'] = 'doc-footnote'
        elif node.name=='a' and 'noteref' in classes:
            node['role'] = 'doc-noteref'
            targets = note_targets.get((part,anchor),())
            if len(targets)==1:
                (filename,fragment), = targets
                if filename==part:
                    node['href'] = '#'+quote(fragment)
        elif node.name=='div' and 'line' in classes and node.find_parent('div',class_='linegroup'):
            node.name = 'p'
        annotations[marker] = location
    html_read(str(html),builder,where=part,collect_resources=True,source_annotations=annotations)


def note_bindings(track,body):
    """Map the two text cues of a DAISY 2.02 note sequence to their anchors."""
    for seq in body.iter():
        if name(seq)!='seq':
            continue
        elements = [node for node in seq if name(node)]
        if len(elements)!=2 or any(name(node)!='par' for node in elements):
            continue
        marker,definition = elements
        if marker.get('system-required') or definition.get('system-required')!='footnote-on':
            continue
        anchors = []
        for par in (marker,definition):
            texts = [node for node in par if name(node)=='text' and node.get('src')]
            if len(texts)!=1:
                break
            href = texts[0].get('src')
            filename = target(track.name,href)
            if not filename or not (fragment:=unquote(urlsplit(href).fragment)):
                break
            anchors.append((filename,fragment))
        if len(anchors)==2 and anchors[0]!=anchors[1]:
            yield tuple(anchors)


def describe_book(descriptor,find,read):
    raw = read(descriptor)
    book = Book(PurePosixPath(descriptor.name).stem)
    is_ncc = ncc_name(descriptor.name)
    index_refs = []
    if is_ncc:
        soup = document_html(raw)
        fields = defaultdict(list)
        for meta in soup.find_all('meta',attrs={'name':True,'content':True}):
            fields[meta['name'].strip().casefold()].append(meta['content'].strip())
        book.fields = dict(fields)
        label = soup.title.get_text(' ',strip=True) if soup.title else book.title
        book.title = next(iter(fields['dc:title']),label) or book.title
        index_refs = [Reference(descriptor,node['href'],node.sourceline)
                      for node in (soup.body or soup).find_all('a',href=True) if smil_link(node['href'])]
        signature = fields['dc:format']+fields['ncc:format']
        if not index_refs and not any('daisy' in value.casefold() for value in signature):
            return None

    def remember(member,role,media=None):
        book.resources.setdefault(member,(role,media))

    def component(ref,role,media=None):
        path = ref.path()
        choices = find(path) if path else ()
        if len(choices)==1:
            member, = choices
            remember(member,role,media)
            return member
        return None

    remember(descriptor,'descriptor',HTML_TYPE if is_ncc else None)
    # NCC links are navigation points. Several can address one component.
    first_entry = {}
    for ref in index_refs:
        first_entry.setdefault(ref.path() or ref.href,ref)
    references = list(first_entry.values())
    book.navigation = index_refs
    if is_ncc:
        master_files = [item for variant in MASTER_NAMES
                        for item in find(target(descriptor.name,variant))]
        for master in master_files:
            remember(master,'synchronization')
        if len(master_files)==1:
            master, = master_files
            try:
                _,body = smil_body(read(master))
                master_refs = [Reference(master,node.get('src',''),node.sourceline)
                               for node in body if name(node)=='ref'] if body is not None else []
            except Error:
                master_refs = []
            if master_refs:
                declared = {ref.path() for ref in master_refs}
                extra = [ref for ref in references if ref.path() not in declared]
                if not extra:
                    references = master_refs
                elif declared <= {ref.path() for ref in references}:
                    book.warnings.append('The optional Master SMIL lists only part of this book. Using the complete NCC order.')
                else:
                    references = master_refs + extra
                    book.tracks.extend((None,ref,'The NCC and Master SMIL disagree on the placement of this track.')
                                       for ref in extra)
            elif references:
                book.warnings.append('The optional Master SMIL has no readable sequence. Using the NCC order.')
            else:
                book.tracks.append((None,Reference(master,'',None),'The DAISY Master SMIL has no readable sequence.'))
        elif master_files:
            if references:
                book.warnings.append('The DAISY publication has two Master SMIL files. Using the NCC order.')
            else:
                book.tracks.append((None,Reference(descriptor,'master.smil',None),
                                    'The DAISY publication has more than one Master SMIL.'))
    else:
        try:
            _,body = smil_body(raw)
        except Error:
            return None
        if body is None:
            return None
        references = [Reference(descriptor,node.get('src',''),node.sourceline) for node in body if name(node)=='ref']
        if not references:
            if not any(name(node) in ('text','audio') for node in body.iter()):
                return None
            references = [Reference(descriptor,PurePosixPath(descriptor.name).name,None)]

    inspected_html,scanned_tracks = set(),set()
    for ref in references:
        track = component(ref,'synchronization') if smil_link(ref.href) else None
        problem = None if track else f'DAISY playback reference {ref.href!r} needs one local SMIL member.'
        book.tracks.append((track,ref,problem))
        if track is None or track in scanned_tracks:
            continue
        scanned_tracks.add(track)
        try:
            _,body = smil_body(read(track))
        except Error:
            book.opaque_track = True
            continue  # The shared binder will report this exact track.
        if body is None:
            book.opaque_track = True
            continue
        for marker,definition in note_bindings(track,body):
            book.note_targets[marker].add(definition)
        for node in body.iter():
            kind,href = name(node),node.get('src')
            if not href or kind not in ('text','audio','img','video'):
                continue
            book.has_audio |= kind=='audio'
            declared = Reference(track,href,node.sourceline)
            filename = declared.path()
            media = TEXT_MEDIA.get(PurePosixPath(filename).suffix.casefold()) if filename and kind=='text' else None
            asset = component(declared,'text' if kind=='text' else 'asset',media)
            if media and filename!=descriptor.name and not ncc_name(filename):
                book.body_references = True
            if media==HTML_TYPE and asset and asset not in inspected_html and not ncc_name(asset.name):
                inspected_html.add(asset)
                try:
                    parsed = document_html(read(asset))
                except Error:
                    continue
                for child in parsed.find_all(['img','link','source']):
                    if child.name=='link' and 'stylesheet' not in child.get('rel',[]):
                        continue
                    if link:=child.get('src') or child.get('href'):
                        component(Reference(asset,link,child.sourceline),'asset')
    return book


class FolderMembers:
    def __init__(self,root):
        self.root = root
        self.stamps = {}
        self.members = {}

    def find(self,name):
        if name not in self.members:
            try:
                path = relative_file(self.root,name)
            except Error as exc:
                if exc.code!='source_member_missing':
                    raise
                return ()
            leaf = PurePosixPath(name).name
            # On case-insensitive filesystems these two lookup spellings may
            # otherwise manufacture two archive members for one physical file.
            if leaf in MASTER_NAMES and all(item.name!=leaf for item in path.parent.iterdir()):
                return ()
            version = stamp(path.stat())
            self.stamps[name] = version
            self.members[name] = Member(len(self.members)+1,name,version[2])
        return (self.members[name],)

    def read(self,member):
        path = relative_file(self.root,member.name)
        before = self.stamps[member.name]
        if before[2]>MAX_STRUCTURE:
            raise Error('source_structure_size',f'DAISY structured member exceeds the reading budget: {member.name}.')
        with path.open('rb') as stream:
            data = stream.read(MAX_STRUCTURE+1)
        if stamp(path.stat())!=before or len(data)!=before[2]:
            raise Error('source_changed',f'DAISY member changed while reading {member.name}.')
        return data


def candidate_books(members,find,read):
    claimed = set()
    books = []
    candidates = sorted((m for m in members if descriptor_name(m.name)),key=lambda m:
                        (not ncc_name(m.name),PurePosixPath(m.name).name.casefold()!='master.smil',m.index))
    for member in candidates:
        if member.index in claimed or member.size is not None and member.size>MAX_STRUCTURE:
            continue
        if book:=describe_book(member,find,read):
            books.append((book,member))
            claimed.update(item.index for item in book.resources)
    return books,claimed


def folder_descriptors(root):
    return sorted(path.name for path in root.iterdir() if not path.name.startswith('.') and descriptor_name(path.name)
                  and path.is_file() and not path.is_symlink() and not path.is_junction())


def folder_books(root,descriptors):
    files = FolderMembers(root)
    candidates = [member for name in descriptors for member in files.find(name)]
    books,_ = candidate_books(candidates,files.find,files.read)
    return books


def archive_books(archive,members):
    by_name = defaultdict(list)
    for member in members:
        by_name[member.name].append(member)
    return candidate_books(members,lambda name:by_name.get(name,()),lambda m:member_bytes(archive,m,MAX_STRUCTURE))


def capture_daisy202(root,target_zip,ordinal,*,descriptor=None):
    names = folder_descriptors(root)
    if descriptor is None and ordinal and 0<ordinal<=len(names):
        descriptor = names[ordinal-1]
    if descriptor not in names:
        raise Error('source_changed','The selected DAISY descriptor disappeared before capture.')

    def listed():
        files = FolderMembers(root)
        members = files.find(descriptor)
        if not members:
            raise Error('source_changed','The selected DAISY descriptor disappeared before capture.')
        member, = members
        book = describe_book(member,files.find,files.read)
        if book is None:
            raise Error('source_changed','The selected DAISY descriptor no longer identifies a publication.')
        return {item.name:files.stamps[item.name] for item in book.resources}

    files = listed()
    capture_folder(root,target_zip,lambda:files,verify=lambda _:listed(),include_root=False)
    return sorted(files).index(descriptor)+1


def read_daisy202(path,source,sha):
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        by_name = defaultdict(list)
        for member in members:
            by_name[member.name].append(member)
        descriptor = next((member for member in members if member.index==source.entry),None)
        if descriptor is None:
            raise Error('source_member_missing','The DAISY package descriptor is missing.')
        book = describe_book(descriptor,lambda name:by_name.get(name,()),lambda m:member_bytes(archive,m,MAX_STRUCTURE))
        if book is None:
            raise Error('source_daisy','This source does not contain a DAISY navigation or synchronization sequence.')
        modes = {mode.casefold() for mode in book.fields.get('ncc:multimediatype',())}
        if 'audioparttext' in modes:
            raise Error('daisy_partial_text','This DAISY edition provides only partial text.',
                        'Open a full-text edition for synthesis, or play the original DAISY audio.')
        inferred_audio_only = (book.has_audio and not book.body_references and not book.opaque_track
                               and all(track is not None for track,_,_ in book.tracks))
        if modes.intersection({'audioonly','audioncc'}) or inferred_audio_only:
            raise Error('daisy_audio_only','This DAISY edition has no full text for audiobook synthesis.',
                        'Open a full-text edition, or play the original DAISY audio.')
        builder = DocumentBuilder('html',book.title,sha)
        doc = builder.doc
        doc.warnings.extend(book.warnings)
        doc.language = next(iter(book.fields.get('dc:language',())),doc.language)
        if authors:=book.fields.get('dc:creator'):
            doc.metadata['authors'] = authors
        if identifiers:=book.fields.get('dc:identifier') or book.fields.get('ncc:identifier'):
            doc.metadata['source_identifiers'] = identifiers
        if book.navigation:
            doc.inventory.append({'kind':'daisy_navigation','entries':[
                {'href':ref.href,'member':descriptor.name,'line':ref.line} for ref in book.navigation]})
        records = {member.name:{**member_location(sha,member,role),'status':'preserved','media_type':media}
                   for member,(role,media) in book.resources.items()}

        def issue(problem,ordinal):
            ref = book.tracks[ordinal-1][1] if book.tracks else Reference(descriptor,'',None)
            block = builder.issue(problem,f'{ref.source.name}:playback/{ordinal}')
            if ref.line:
                block.locator['lines'] = [ref.line,ref.line]
            if ref.href:
                block.locator['href'] = ref.href
            bind_member_block(block,ref.source,ordinal,sha,records,target)

        def bind(block,member,ordinal):
            bind_member_block(block,member,ordinal,sha,records,target)

        if book.tracks:
            tracks = ((member,problem) for member,_,problem in book.tracks)
            def html_reader(raw,document,part):
                read_daisy_html(raw,document,part,book.note_targets)
            read_synchronized(archive,tracks,by_name,builder,records,issue,bind,html_reader=html_reader)
        else:
            issue('The DAISY navigation file has no local SMIL sequence.',1)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(records.values())
        return doc
