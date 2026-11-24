"""Shared archive, folder and original-resource handling for scoped XML editions."""

from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, name, xml, xml_root
from .xml_projection import element_text

SUFFIXES = ('.xml','.jats','.tei')
ROOTS = ('article','article-set','TEI','teiCorpus')


def reader_for(root,part):
    from .jats_intake import asset_links as jats_assets, is_jats, read_jats
    from .tei_intake import asset_links as tei_assets, is_tei, read_tei

    if is_jats(root,part):
        return 'jats',read_jats,jats_assets
    if is_tei(root):
        return 'tei',read_tei,tei_assets
    return None


def assets(root,part):
    from .opf_intake import target

    kind,_,links = reader_for(root,part)
    for node,href in links(root):
        reference = {'kind':kind+'_asset','href':href,'member':part,
                     'xml_path':node.getroottree().getpath(node),'line':node.sourceline}
        yield reference,target(part,href)


def read_projected(root,builder,part,*,collect_resources=False):
    schema = reader_for(root,part)
    if schema is None:
        return False
    schema[1](root,builder,part,collect_resources=collect_resources)
    if not collect_resources:
        builder.doc.inventory.extend({**item,'status':'unavailable'} for item,_ in assets(root,part))
    return True


def publication_label(root,part):
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


def declared_files(path,root):
    from .source_bundle import relative_file, stamp

    result = {path.name:stamp(path.stat())}
    for _,target in assets(root,path.name):
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
    books,claimed = [],set()
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
        books.append((member,publication_label(root,member.name)))
        claimed.add(member.index)
        for _,path in assets(root,member.name):
            claimed.update(asset.index for asset in by_name.get(path,()))
    return books,claimed


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
        builder = DocumentBuilder('xml-book',title,sha)
        if not read_projected(root,builder,text.name,collect_resources=True):
            raise Error('source_xml','The archived XML member no longer identifies the selected edition.')
        doc = builder.doc
        by_name = defaultdict(list)
        for member in members:
            by_name[member.name].append(member)
        records = {text.name:{**member_location(sha,text,'text'),'status':'represented'}}
        for reference,asset_path in assets(root,text.name):
            matched = by_name.get(asset_path,())
            if len(matched)==1 and asset_path!=text.name:
                member, = matched
                record = records.setdefault(asset_path,{**member_location(sha,member,'asset'),'status':'preserved'})
                record.setdefault('authored_references',[]).append(reference)
            else:
                doc.inventory.append({**reference,'status':'ambiguous' if len(matched)>1 else 'unavailable'})
        for ordinal,block in enumerate(doc.blocks,1):
            bind_member_block(block,text,ordinal,sha,records,target)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(records.values())
        return doc
