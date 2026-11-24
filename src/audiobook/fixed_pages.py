"""Follow EPUB spine order for one-image fixed-layout scans."""
import posixpath

from .core import Error
from .document_intake import attr, name, xml
from .source_bundle import safe_name


def get_xml(archive,part):
    from .document_intake import MAX_STRUCTURE

    info = archive.getinfo(part)
    if info.file_size>MAX_STRUCTURE:
        raise Error('source_structure_size', f'The EPUB part cannot fit the structured reader: {part}.')
    return xml(archive.read(info))


def package(archive):
    from .importers import archive_path

    try:
        container = get_xml(archive,'META-INF/container.xml')
        root = next((attr(node,'full-path') for node in container.iter() if name(node)=='rootfile'),None)
        if not root:
            raise Error('unreadable_epub','The EPUB does not name a package document.')
        part,_ = archive_path('',root)
        return part,get_xml(archive,part)
    except KeyError as exc:
        raise Error('unreadable_epub','The EPUB package is missing a named document.') from exc


def image_only_fixed(archive):
    part,root = package(archive)
    return any(name(node)=='meta' and attr(node,'property')=='rendition:layout'
               and (node.text or '').strip()=='pre-paginated' for node in root.iter())


def page_sequence(archive):
    from .importers import archive_path

    part,root = package(archive)
    images = {}
    records = {item.name:item for item in archive.members()}
    if 'META-INF/encryption.xml' in records:
        raise Error('protected_epub','This fixed-layout EPUB declares protected or obfuscated resources.',
                    'Provide an unprotected scan or ordinary EPUB.')
    table = {attr(node,'id'):node for node in root.iter() if name(node)=='item' and attr(node,'id')}
    for index,entry in enumerate((n for n in root.iter() if name(n)=='itemref'),1):
        if attr(entry,'linear')=='no':
            continue
        item = table.get(attr(entry,'idref'))
        if item is None or not attr(item,'href'):
            raise Error('source_member_missing',f'Fixed-layout EPUB spine entry {index} lacks a package member.')
        sheet,_ = archive_path(posixpath.dirname(part),attr(item,'href'))
        try:
            page = get_xml(archive,sheet)
        except KeyError as exc:
            raise Error('source_member_missing',f'EPUB spine member is missing: {sheet}.') from exc
        body = next((node for node in page.iter() if name(node)=='body'),page)
        words = [(n.text or '') for n in body.iter() if name(n) not in ('img','image','style','script','title')]
        if any(item.strip() for item in words) or any((n.tail or '').strip() for n in body.iter()):
            raise Error('fixed_layout','This fixed EPUB includes positioned text as well as its images.',
                        'Supply a verified reading-order export or a PDF of the same edition.')
        inline = [node for node in body.iter() if name(node) in ('img','image')]
        if len(inline)!=1:
            raise Error('fixed_layout', f'Fixed EPUB page {index} does not have one identifiable page image.')
        image, = inline
        ref = attr(image,'src') or attr(image,'href')
        if not ref:
            raise Error('source_member_missing',f'Fixed EPUB page {index} has no image reference.')
        path,_ = archive_path(posixpath.dirname(sheet),ref)
        path = safe_name(path)
        if path not in records:
            raise Error('source_member_missing',f'EPUB page image is missing: {path}.')
        images[index] = {'path':path,'entry':records[path].index,'occurrence':f'spine-{index}'}
    title = next((n.text for n in root.iter() if name(n)=='title' and n.text),None)
    return title,list(images.values())
