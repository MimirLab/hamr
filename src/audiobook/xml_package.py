"""Shared archive, folder and original-resource handling for scoped XML editions."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, name, xml, xml_start
from .xml_projection import element_text

SUFFIXES = ('.xml','.nxml','.jats','.tei','.bits','.dbk','.docbook','.zai')
ROOTS = ('article','article-set','TEI','teiCorpus','book','book-part-wrapper')
XML_DEPENDENCIES = frozenset(('xml_include','xml_chapter'))


@dataclass(frozen=True)
class Reader:
    kind: str
    read: Callable
    links: Callable
    bind: Callable | None = None
    label: Callable | None = None


def reader_for(root,part):
    from . import bits_intake, docbook_intake, jats_intake, tei_intake, zedai_intake
    from .xml_includes import XMLIncludes

    if bits_intake.is_bits(root):
        return Reader('bits',bits_intake.read_bits,bits_intake.asset_links,bits_intake.BookIncludes,
                      bits_intake.publication_label)
    if jats_intake.is_jats(root,part):
        return Reader('jats',jats_intake.read_jats,jats_intake.asset_links)
    if tei_intake.is_tei(root):
        return Reader('tei',tei_intake.read_tei,tei_intake.asset_links)
    if docbook_intake.is_docbook(root,part):
        return Reader('docbook',docbook_intake.read_docbook,docbook_intake.asset_links,XMLIncludes,
                      docbook_intake.publication_label)
    if zedai_intake.is_zedai(root):
        return Reader('zedai',zedai_intake.read_zedai,zedai_intake.asset_links,zedai_intake.DescriptionResources,
                      zedai_intake.publication_label)
    return None


def include_graph(root,part,load=None):
    if (reader:=reader_for(root,part)) is not None and reader.bind is not None:
        return reader.bind(root,part,load)
    return None


def assets(root,part,includes=None):
    from .xml_includes import local_target

    reader = reader_for(root,part)
    if includes is not None:
        yield from includes.references
        for path in includes.alternates:
            yield {'kind':'xml_alternate','member':part,'href':path},path
    roots = includes.roots if includes is not None else {part:root}
    for owner,source in roots.items():
        for node,href in reader.links(source):
            if includes is not None and node not in includes.active:
                continue
            reference = {'kind':reader.kind+'_asset','href':href,'member':owner,
                         'xml_path':node.getroottree().getpath(node),'line':node.sourceline}
            yield reference,local_target(owner,node,href)


def read_projected(root,builder,part,*,includes=None,collect_resources=False):
    reader = reader_for(root,part)
    if reader is None:
        return False
    options = {'collect_resources':collect_resources}
    if reader.bind is not None:
        includes = includes if includes is not None else reader.bind(root,part)
        options['includes'] = includes
    reader.read(root,builder,part,**options)
    if not collect_resources:
        builder.doc.inventory.extend({'status':'unavailable',**item} for item,_ in assets(root,part,includes)
                                     if item['kind']!='xml_alternate')
    return True


def publication_label(root,part,includes=None):
    if (reader:=reader_for(root,part)) is not None and reader.label is not None:
        return reader.label(root,includes) or part
    if name(root) in ('TEI','teiCorpus'):
        title = root.find('{*}teiHeader/{*}fileDesc/{*}titleStmt/{*}title')
    else:
        title = root.find('front/article-meta/title-group/article-title')
    return element_text(title) if title is not None else part


def candidate(stream,filename):
    from .docbook_intake import is_docbook
    from .zedai_intake import is_zedai

    if PurePosixPath(filename).suffix.lower() not in SUFFIXES:
        return False
    root = xml_start(stream)
    return root is not None and (name(root) in ROOTS or is_docbook(root,filename) or is_zedai(root))


def loose_document(path):
    with path.open('rb') as stream:
        if not candidate(stream,path.name):
            return None
    root = xml(bounded_read(path))
    return root if reader_for(root,path.name) else None


def local_xml(folder,part,stamps=None):
    from .source_bundle import relative_file, stamp

    try:
        path = relative_file(folder,part)
    except Error as exc:
        if exc.code!='source_member_missing':
            raise
        return None
    before = stamp(path.stat())
    raw = bounded_read(path)
    if stamp(path.stat())!=before or len(raw)!=before[2]:
        raise Error('source_changed',f'The included XML changed while reading {part}.')
    if stamps is not None:
        stamps[part] = before
    return raw


def loose_assets(path,root,scope):
    part = path.relative_to(scope).as_posix()
    includes = include_graph(root,part,lambda name:local_xml(scope,name))
    return assets(root,part,includes)


def declared_files(path,root,scope):
    from .source_bundle import relative_file, stamp

    part = path.relative_to(scope).as_posix()
    result = {part:stamp(path.stat())}
    includes = include_graph(root,part,lambda name:local_xml(scope,name,result))
    for _,target in assets(root,part,includes):
        if target and target not in result:
            try:
                result[target] = stamp(relative_file(scope,target).stat())
            except Error as exc:
                if exc.code!='source_member_missing':
                    raise
    return result


def capture_xml(path,target_dir,source,*,scope=None):
    from .publication_package import capture_folder
    from .source_bundle import relative_file, stamp

    before = stamp(path.stat())
    root = loose_document(path)
    if root is None:
        return None
    scope = scope if scope is not None else path.parent
    part = path.relative_to(scope).as_posix()
    anchor = relative_file(scope,part)
    listed = declared_files(anchor,root,scope)
    if listed[part]!=before:
        raise Error('source_changed','The selected XML edition changed before capture.')
    if len(listed)==1:
        return None
    output = target_dir/'source.zip'

    def verify(_):
        # Publication capture checks every current member stamp, then checks
        # whether an authored dependency appeared or disappeared.
        return declared_files(anchor,root,scope)

    capture_folder(scope,output,lambda:listed,verify=verify,include_root=False)
    return output,replace(source,kind='zip',entry=sorted(listed).index(part)+1,member_name=None)


def archive_books(archive,members):
    from .publication_package import member_bytes

    by_name = defaultdict(list)
    for member in members:
        by_name[member.name].append(member)
    books,claimed,parts = [],set(),set()
    for member in members:
        if (member.size is not None and member.size>MAX_STRUCTURE
                or PurePosixPath(member.name).suffix.lower() not in SUFFIXES):
            continue
        with archive.open_member(member) as stream:
            if not candidate(stream,member.name):
                continue
        try:
            root = xml(member_bytes(archive,member,MAX_STRUCTURE))
        except Error as exc:
            if exc.code not in ('source_xml','source_structure_size'):
                raise
            continue
        if not reader_for(root,member.name):
            continue
        includes = include_graph(root,member.name,lambda part:archive_xml(archive,by_name,part))
        books.append((member,publication_label(root,member.name,includes)))
        claimed.add(member.index)
        for reference,path in assets(root,member.name,includes):
            indices = {asset.index for asset in by_name.get(path,())}
            if reference['kind']=='xml_alternate' and len(indices)!=1:
                continue
            claimed.update(indices)
            if reference['kind'] in XML_DEPENDENCIES and path!=member.name:
                parts.update(indices)
    return [book for book in books if book[0].index not in parts] or books,claimed


def archive_xml(archive,by_name,part):
    from .publication_package import member_bytes

    choices = by_name.get(part,())
    if len(choices)==1:
        return member_bytes(archive,choices[0],MAX_STRUCTURE)


def read_xml_archive(path,source,sha,title):
    from .archive_intake import containers
    from .opf_intake import target
    from .publication_package import bind_member_block, member_bytes, member_location

    with containers(path,source.kind) as archive:
        members = list(archive.members())
        text = next((item for item in members if item.index==source.entry),None)
        if text is None:
            raise Error('source_member_missing','The saved XML edition is missing from the source archive.')
        root = xml(member_bytes(archive,text,MAX_STRUCTURE))
        by_name = defaultdict(list)
        for member in members:
            by_name[member.name].append(member)
        includes = include_graph(root,text.name,lambda part:archive_xml(archive,by_name,part))
        builder = DocumentBuilder('xml-book',title,sha)
        if not read_projected(root,builder,text.name,includes=includes,collect_resources=True):
            raise Error('source_xml','The archived XML member no longer identifies the selected edition.')
        doc = builder.doc
        records = {text.name:{**member_location(sha,text,'text'),'status':'represented'}}
        for reference,asset_path in assets(root,text.name,includes):
            matched = by_name.get(asset_path,())
            is_include = reference['kind'] in XML_DEPENDENCIES
            if is_include:
                doc.inventory.append(reference)
            if len(matched)==1 and asset_path!=text.name:
                member, = matched
                role = 'include' if is_include else 'asset'
                record = records.setdefault(asset_path,{**member_location(sha,member,role),'status':'preserved'})
                record.setdefault('authored_references',[]).append(reference)
            elif not is_include and reference['kind']!='xml_alternate':
                doc.inventory.append({**reference,'status':'ambiguous' if len(matched)>1 else 'unavailable'})
        for ordinal,block in enumerate(doc.blocks,1):
            owner = block.locator.get('source_file',text.name)
            resource = records.get(owner)
            member = by_name[owner][0] if resource is not None else text
            if resource is not None and resource['role']=='include':
                resource['status'] = 'represented'
            bind_member_block(block,member,ordinal,sha,records,target)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(records.values())
        return doc
