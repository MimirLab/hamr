"""Shared archive, folder and original-resource handling for scoped XML editions."""

from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, name, xml, xml_root
from .xml_projection import element_text

SUFFIXES = ('.xml','.nxml','.jats','.tei','.bits')
ROOTS = ('article','article-set','TEI','teiCorpus','book','book-part-wrapper')
XML_DEPENDENCIES = frozenset(('xml_include','xml_chapter'))


def reader_for(root,part):
    from .bits_intake import asset_links as bits_assets, is_bits, read_bits
    from .jats_intake import asset_links as jats_assets, is_jats, read_jats
    from .tei_intake import asset_links as tei_assets, is_tei, read_tei

    if is_bits(root):
        return 'bits',read_bits,bits_assets
    if is_jats(root,part):
        return 'jats',read_jats,jats_assets
    if is_tei(root):
        return 'tei',read_tei,tei_assets
    return None


def include_graph(root,part,load=None):
    from .bits_intake import BookIncludes, is_bits

    return BookIncludes(root,part,load) if is_bits(root) else None


def assets(root,part,includes=None):
    from .opf_intake import target
    from .xml_includes import local_target

    kind,_,links = reader_for(root,part)
    if includes is not None:
        yield from includes.references
        for path in includes.alternates:
            yield {'kind':'xml_alternate','member':part,'href':path},path
    roots = includes.roots if includes is not None else {part:root}
    for owner,source in roots.items():
        for node,href in links(source):
            if includes is not None and node not in includes.active:
                continue
            reference = {'kind':kind+'_asset','href':href,'member':owner,
                         'xml_path':node.getroottree().getpath(node),'line':node.sourceline}
            yield reference,local_target(owner,node,href) if includes is not None else target(owner,href)


def read_projected(root,builder,part,*,includes=None,collect_resources=False):
    schema = reader_for(root,part)
    if schema is None:
        return False
    options = {'collect_resources':collect_resources}
    if schema[0]=='bits':
        includes = includes if includes is not None else include_graph(root,part)
        options['includes'] = includes
    schema[1](root,builder,part,**options)
    if not collect_resources:
        builder.doc.inventory.extend({'status':'unavailable',**item} for item,_ in assets(root,part,includes)
                                     if item['kind']!='xml_alternate')
    return True


def publication_label(root,part,includes=None):
    from .bits_intake import is_bits, publication_label as bits_label

    if is_bits(root):
        return bits_label(root,includes) or part
    if name(root) in ('TEI','teiCorpus'):
        title = root.find('{*}teiHeader/{*}fileDesc/{*}titleStmt/{*}title')
    else:
        title = root.find('front/article-meta/title-group/article-title')
    return element_text(title) if title is not None else part


def candidate(stream,filename):
    return PurePosixPath(filename).suffix.lower() in SUFFIXES and xml_root(stream) in ROOTS


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


def loose_assets(path,root):
    includes = include_graph(root,path.name,lambda name:local_xml(path.parent,name))
    return assets(root,path.name,includes)


def declared_files(path,root):
    from .source_bundle import relative_file, stamp

    result = {path.name:stamp(path.stat())}
    includes = include_graph(root,path.name,lambda name:local_xml(path.parent,name,result))
    for _,target in assets(root,path.name,includes):
        if target and target not in result:
            try:
                result[target] = stamp(relative_file(path.parent,target).stat())
            except Error as exc:
                if exc.code!='source_member_missing':
                    raise
    return result


def capture_xml(path,target_dir,source):
    from .publication_package import capture_folder
    from .source_bundle import relative_file, stamp

    before = stamp(path.stat())
    root = loose_document(path)
    if root is None:
        return None
    anchor = relative_file(path.parent,path.name)
    listed = declared_files(anchor,root)
    if listed[path.name]!=before:
        raise Error('source_changed','The selected XML edition changed before capture.')
    if len(listed)==1:
        return None
    output = target_dir/'source.zip'

    def verify(_):
        # Publication capture checks every current member stamp, then checks
        # whether an authored dependency appeared or disappeared.
        return declared_files(anchor,root)

    capture_folder(path.parent,output,lambda:listed,verify=verify,include_root=False)
    return output,replace(source,kind='zip',entry=sorted(listed).index(path.name)+1,member_name=None)


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
