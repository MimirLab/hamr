"""Read the authored manuscript from a Scrivener binder and frozen RTF members."""

from collections import Counter, defaultdict
from pathlib import PurePosixPath
import posixpath
import re

from .archive_intake import containers
from .core import Error, key
from .document_intake import DocumentBuilder, MAX_STRUCTURE, cell_semantics, xml
from .publication_package import capture_folder, folder_files, member_bytes, member_location
from .rtf_intake import read_rtf
from .source_bundle import MAX_MANIFEST, relative_file, relative_path, stamp

BINDER_SUFFIX = '.scrivx'
PROJECT_DIRS = frozenset(('Files','Settings','Snapshots','QuickLook','Icons','Accessibility'))
PROJECT_FILES = frozenset(('docs.checksum','version.txt','writing.history'))
UUID = re.compile(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')
NUMERIC_ID = re.compile(r'[0-9]{1,16}')
STYLE = re.compile(r'<(!?)\$Scr_(?:[PC]s|H)::[0-9]+>')


def folder_binders(root):
    return sorted(item.name for item in root.iterdir()
                  if item.suffix.casefold()==BINDER_SUFFIX and item.is_file() and not item.name.startswith('.'))


def capture_scrivener(root,target,binder):
    def files():
        result = {binder:stamp(relative_file(root,binder).stat())}
        for name in PROJECT_DIRS | PROJECT_FILES:
            path = relative_path(root,name)
            if path.is_dir() and name in PROJECT_DIRS:
                result.update({name+'/'+file:version for file,version in folder_files(path).items()})
            elif path.is_file() and name in PROJECT_FILES:
                result[name] = stamp(path.stat())
        return result

    capture_folder(root,target,files)


def in_project(name,root):
    if root:
        if not name.startswith(root+'/'):
            return False
        name = name.removeprefix(root+'/')
    top,sep,_ = name.partition('/')
    return (top in PROJECT_DIRS) if sep else (name in PROJECT_FILES)


def archive_books(members):
    """A backup binder inside an owned project directory is an asset."""
    possible = [item for item in members if item.name.casefold().endswith(BINDER_SUFFIX)
                and not item.name.startswith('__MACOSX/')]
    if not possible:
        return [],set()
    storage = set()
    for item in members:
        directory = PurePosixPath(item.name).parent
        while directory.name:
            if directory.name in ('Data','Docs') and directory.parent.name=='Files':
                storage.add(directory.parent.parent.as_posix())
                break
            directory = directory.parent
    candidates = []
    for item in possible:
        path = PurePosixPath(item.name)
        if any(parent.suffix.casefold() in ('.scriv','.textbundle') for parent in path.parents[1:]):
            continue
        if path.parent.suffix.casefold()!='.scriv' and path.parent.name and path.parent.as_posix() not in storage:
            continue
        candidates.append(item)
    candidates.sort(key=lambda item:(item.name.count('/'),item.index))
    books,roots = [],set()
    for item in candidates:
        root = posixpath.dirname(item.name)
        if any(in_project(item.name,other) for other in roots):
            continue
        roots.add(root)
        books.append(item)
    owned = {item.index for item in members if any(in_project(item.name,root) for root in roots)}
    return books,owned|{item.index for item in books}


def binder_document(raw,name):
    try:
        tree = xml(raw)
    except Error as exc:
        raise Error('source_scrivener_binder',f'Cannot read the Scrivener binder {name}.') from exc
    if tree.tag!='ScrivenerProject' or tree.getroottree().docinfo.doctype:
        raise Error('source_scrivener_binder',f'{name} does not identify a Scrivener project binder.')
    version = tree.get('Version','')
    if version=='2.0':
        modern = True
    elif re.fullmatch(r'1(?:\.[0-9]+)?',version):
        modern = False
    else:
        raise Error('source_scrivener_version',f'Scrivener binder {name} declares unsupported version {version!r}.',
                    'Open an EPUB or PDF compiled from this project.')
    drafts = tree.findall('./Binder/BinderItem[@Type="DraftFolder"]')
    if len(drafts)!=1:
        raise Error('source_scrivener_draft',f'{name} needs one Draft or Manuscript binder branch.')
    return tree,drafts[0],modern


def ordered_items(draft):
    stack = [(node,(),None) for node in reversed(draft.findall('./Children/BinderItem'))]
    while stack:
        node,parents,chapter = stack.pop()
        title = (node.findtext('Title') or '').strip()
        if chapter is None:
            chapter = node
        yield node,title,parents,chapter
        ancestors = (*parents,title)
        stack.extend((child,ancestors,chapter) for child in reversed(node.findall('./Children/BinderItem')))


def transcript_flag(item):
    raw = item.findtext('./MetaData/IncludeInCompile','Yes').strip().casefold()
    if raw in ('yes','true','1'):
        return True
    if raw in ('no','false','0'):
        return False
    return None


def member_key(path,modern):
    # UUID is case-insensitive. Preserve the archive's exact physical name and
    # report competing spellings instead of relying on the host filesystem.
    if modern:
        segments = path.split('/')
        if len(segments)>=4 and segments[:2]==['Files','Data'] and UUID.fullmatch(segments[2]):
            segments[2] = segments[2].upper()
            return '/'.join(segments)
    return path


def style_controls(blocks):
    # Styles can cross paragraphs and cells. Match their delimiters in the
    # authored order, then project the markers out of each reading.
    regions = []
    for block in blocks:
        if block.role=='table':
            regions.extend((block,(row,column),text) for row,values in enumerate(block.locator['rows'])
                           for column,text in enumerate(values))
        else:
            regions.append((block,None,block.source))
    pending,accepted = defaultdict(list),defaultdict(list)
    for index,(_,_,text) in enumerate(regions):
        for marker in STYLE.finditer(text):
            ident = marker[0].replace('!','',1)
            source = {'kind':'hidden','source_start':marker.start(),'source_end':marker.end(),
                      'source':marker[0],'disposition':'companion','evidence':'Scrivener style delimiter'}
            if not marker[1]:
                pending[ident].append((index,source))
            elif pending[ident]:
                start,opening = pending[ident].pop()
                accepted[start].append(opening)
                accepted[index].append(source)
    for index,spans in accepted.items():
        block,cell,text = regions[index]
        spans.sort(key=lambda span:span['source_start'])
        if cell is None:
            block.locator.setdefault('inline_semantics',[]).extend(spans)
        else:
            block.locator.setdefault('table_cell_semantics',[]).append(cell_semantics(*cell,text,spans))


def manuscript(archive,members,binder,sha,title):
    root = posixpath.dirname(binder.name)
    tree,draft,modern = binder_document(member_bytes(archive,binder,MAX_MANIFEST),binder.name)
    authored_title = (tree.findtext('./ProjectProperties/ProjectTitle') or
                      tree.findtext('./ProjectSettings/ProjectTitle') or '').strip()
    builder = DocumentBuilder('rtf',authored_title or PurePosixPath(binder.name).stem or title,sha)
    doc = builder.doc
    binder_origin = member_location(sha,binder,'descriptor')
    resources = {binder.index:binder_origin|{'status':'preserved'}}
    by_name = {}
    prefix = root+'/' if root else ''
    for item in members:
        if in_project(item.name,root):
            filename = member_key(item.name.removeprefix(prefix),modern)
            by_name.setdefault(filename,[]).append(item)
            role = 'package_member'
            basename = PurePosixPath(filename).name
            if basename=='notes.rtf' or basename.endswith('_notes.rtf'):
                role = 'document_notes'
            elif basename=='synopsis.txt' or basename.endswith('_synopsis.txt'):
                role = 'synopsis'
            resources[item.index] = member_location(sha,item,role)|{'status':'retained'}
    id_field,valid_id = ('UUID',UUID) if modern else ('ID',NUMERIC_ID)
    counts = Counter(member_key_value(b.get(id_field,''),modern) for b in tree.iter('BinderItem'))
    chapter_ids = {item:f'scrivener:chapter:{index}'
                   for index,item in enumerate(draft.findall('./Children/BinderItem'),1)}
    for index,(node,label,ancestors,chapter) in enumerate(ordered_items(draft),1):
        ident = node.get(id_field,'')
        canonical = member_key_value(ident,modern)
        flag = transcript_flag(node)
        kind = node.get('Type','')
        chapter_id = chapter_ids[chapter]
        chapter_label = (chapter.findtext('Title') or '').strip() or 'Chapter '+chapter_id.rsplit(':',1)[1]
        occurrence = f'scrivener:{index}'
        row = {'occurrence':occurrence,'label':label or chapter_label,'type':kind,'depth':len(ancestors),
               'parents':list(ancestors),'chapter_id':chapter_id,'include_in_compile':flag}
        doc.inventory.append(row)
        if flag is False:
            row['status'] = 'excluded from compile'
            continue
        info = {**binder_origin,'binder_item':ident,'xpath':tree.getroottree().getpath(node)}
        if flag is None:
            chapter_issue(builder,row,info,chapter_label,'The Scrivener item has an unknown Include in Compile value.')
            continue
        if not valid_id.fullmatch(ident) or counts[canonical]!=1:
            chapter_issue(builder,row,info,chapter_label,'The Scrivener item has an invalid or repeated binder ID.')
            continue
        relative = f'Files/Data/{canonical}/content.rtf' if modern else f'Files/Docs/{ident}.rtf'
        hits = by_name.get(relative,())
        if not hits and kind=='Folder':
            row['status'] = 'binder group'
            continue
        if kind not in ('Folder','Text'):
            chapter_issue(builder,row,info,chapter_label,f'The Scrivener manuscript item {kind!r} needs a verified text reading.')
            continue
        if len(hits)!=1:
            row['path'] = prefix+relative
            chapter_issue(builder,row,info,chapter_label,f'The chapter body is {"missing" if not hits else "ambiguous"}: {row["path"]}.')
            continue
        body, = hits
        row['member'] = body.name
        resources[body.index].update(role='text',status='selected')
        try:
            content = DocumentBuilder('rtf',chapter_label,sha)
            raw = member_bytes(archive,body,MAX_STRUCTURE)
            read_rtf(raw,content)
            if b'$Scr_' in raw:
                style_controls(content.doc.blocks)
        except (Error,UnicodeError) as exc:
            chapter_issue(builder,row,resources[body.index],chapter_label,str(exc))
            continue
        identifiers = {item.id:'b'+key([body.name,occurrence,item.id])[:16] for item in content.doc.blocks}
        destinations = {'source.html#'+old:'source.html#'+new for old,new in identifiers.items()}
        for item in content.doc.blocks:
            for link in item.links:
                if link.get('destination') in destinations:
                    link['destination'] = destinations[link['destination']]
                if 'destinations' in link:
                    link['destinations'] = [destinations.get(target,target) for target in link['destinations']]
            item.id = identifiers[item.id]
            item.chapter = chapter_label
            item.locator.update(member=body.name,archive_entry=body.index,binder_item=ident,
                                chapter_id=chapter_id,location_id=key([sha,body.index,item.id])[:20])
            doc.blocks.append(item)
        row['status'] = ('needs_review' if any(block.issue for block in content.doc.blocks) else
                         'represented' if content.doc.blocks else 'empty')
    doc.metadata['source_resources'] = list(resources.values())
    doc.metadata['scrivener'] = {'binder':binder.name,'version':tree.get('Version'),
                                'scope':'Draft','draft_title':(draft.findtext('Title') or '').strip()}
    return doc


def member_key_value(ident,modern):
    return ident.upper() if modern else ident


def chapter_issue(builder,row,origin,label,reason):
    row.update(status='failed',reason=reason)
    location = {**origin,'chapter_id':row['chapter_id'],'binder_occurrence':row['occurrence'],
                'location_id':key([builder.doc.source_sha256,origin['archive_entry'],row['occurrence']])[:20]}
    if 'path' in row:
        location['path'] = row['path']
    # Bound the hold to this binder item without changing healthy text nearby.
    block = builder.add('','unreadable',row['occurrence'],loc=location,issue=reason)
    block.chapter = label


def read_scrivener(path,source,sha,title):
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        books,_ = archive_books(members)
        binder = next((item for item in books if item.index==source.entry),None)
        if binder is None:
            raise Error('source_scrivener_binder','The selected Scrivener binder is unavailable in the snapshot.')
        return manuscript(archive,members,binder,sha,title)
