"""Project Bambook metadata, reading order and chapter content into shared intake."""

from lxml import etree

from .core import Error, key
from .document_intake import DocumentBuilder, html_read, xml
from .publication_package import bind_resources, local_target
from .snb_container import Package

BOOK = 'snbf/book.snbf'
TOC = 'snbf/toc.snbf'


def markup(package,member,tag):
    root = xml(package.read(member))
    if root.tag!=tag:
        raise Error('source_snb_markup',f'The Bambook member {member.name} does not contain {tag} markup.')
    return root


def member_location(member,sha,role):
    return {'name':member.name,'path':member.name,'role':role,'resource_label':member.name,
            'location_id':key([sha,member.index,member.name])[:20],'snb_member':member.identity()}


def read_chapter(builder,chapter,record,where,label,lookup,cover):
    body = chapter.find('body')
    if body is None:
        raise Error('source_snb_markup','The Bambook chapter has no body.')
    title = (chapter.findtext('head/title') or label).strip() or label
    hidden = (chapter.findtext('head/hidetitle') or '').strip().casefold()=='true'
    loc = {'path':record['path'],'snb_member':record['snb_member']}
    heading = builder.add(title,'navigation' if hidden else 'heading',where+'/head/title',
                          loc={**loc,'chapter_marker':True})
    if heading:
        heading.chapter = builder.chapter = label or title
    rendered = etree.Element('body')
    annotations = {}
    if (body.text or '').strip() or any((node.tail or '').strip() for node in body):
        builder.add('','unreadable',where+'/body',loc=loc,
                    issue='The Bambook chapter has text outside its defined paragraph elements.')
    for index,node in enumerate(body,1):
        if not isinstance(node.tag,str):
            continue
        entry = {**loc,'source_element':f'{where}/body/{index}',
                 'exact':etree.tostring(node,encoding='unicode',with_tail=False)}
        part = etree.SubElement(rendered,'p',{'data-hamr-node':str(index)})
        if node.tag=='text' and not len(node):
            part.text = node.text
        elif node.tag=='img' and not len(node):
            reference = (node.text or '').strip()
            item = lookup({'href':reference})
            if item:
                image = etree.SubElement(part,'img',src=reference)
                if item['name']==cover:
                    image.set('role','presentation')
                    image.set('alt','')
            else:
                entry['source_issue'] = f'The Bambook illustration {reference or "(unnamed)"} has no matching member.'
        else:
            entry['source_issue'] = f'The Bambook chapter contains unsupported {node.tag} content.'
        annotations[str(index)] = entry
    start = len(builder.doc.blocks)
    if len(rendered):
        html_read(etree.tostring(rendered,encoding='unicode',method='html'),builder,where=where,
                  metadata=False,collect_resources=True,source_annotations=annotations)
    if len(builder.doc.blocks)==start:
        builder.add('','unreadable',where+'/body',loc=loc,issue='The Bambook chapter has no readable body content.')
    for block in builder.doc.blocks[start:]:
        bind_resources(block,lookup)


def read_snb(path,sha,title):
    with path.open('rb') as stream:
        package = Package(stream)
        book = package.by_name.get(BOOK)
        info = markup(package,book,'book-snbf') if book else etree.Element('book-snbf')
        builder = DocumentBuilder('snb',(info.findtext('head/name') or '').strip() or title,sha)
        doc = builder.doc
        if language:=(info.findtext('head/language') or '').strip():
            doc.language = language
        if author:=(info.findtext('head/author') or '').strip():
            doc.metadata['authors'] = [author]
        doc.metadata['snb_revision'] = package.revision
        resources = {}
        for member in package.members:
            role = ('descriptor' if member.name in (BOOK,TOC) else 'text' if member.name.endswith('.snbc')
                    else 'asset')
            resources[member.name] = {**member_location(member,sha,role),
                                      'status':'unlisted' if role=='text' else 'preserved'}

        def image_record(reference):
            target = local_target(reference['href'],'snbc/images/_')
            item = resources.get(target[0]) if target and not target[1] else None
            return item if item and item['role']=='asset' else None

        cover_item = image_record({'href':(info.findtext('head/cover') or '').strip()})
        cover = cover_item['name'] if cover_item else None
        if cover_item:
            doc.metadata['cover_asset'] = cover_item['location_id']
        toc = package.by_name.get(TOC)
        if toc is None:
            raise Error('source_snb_contents','The Bambook ebook has no chapter directory.',
                        'Open a complete SNB file or an EPUB export with its reading order.')
        contents = markup(package,toc,'toc-snbf')
        body = contents.find('body')
        if body is None:
            raise Error('source_snb_contents','The Bambook chapter directory has no body.')
        order = []
        toc_locator = {'path':TOC,'snb_member':toc.identity()}
        for index,node in enumerate(body,1):
            if not isinstance(node.tag,str):
                continue
            label = ''.join(node.itertext()).strip()
            requested = node.get('src') or ''
            target = local_target(requested,'snbc/_') if node.tag=='chapter' else None
            record = resources.get(target[0]) if target and not target[1] else None
            where = f'{TOC}/body/{index}'
            label = label or requested
            order.append({'position':index,'label':label,'reference':requested})
            try:
                if record is None or record['role']!='text':
                    raise Error('source_snb_chapter',f'Bambook chapter {label or index} has no readable member.')
                chapter = markup(package,package.by_name[record['name']],'snbc')
                start = len(doc.blocks)
                read_chapter(builder,chapter,record,f"{record['name']} (chapter {index})",label,image_record,cover)
                record['status'] = 'needs_review' if any(block.issue for block in doc.blocks[start:]) else 'represented'
            except Error as exc:
                loc = {**toc_locator,'exact':etree.tostring(node,encoding='unicode',with_tail=False)}
                if label:
                    builder.add(label,'navigation',where,loc={**loc,'chapter_marker':True})
                builder.add('','unreadable',where+'/issue',loc=loc,issue=str(exc))
                if record and record['role']=='text':
                    record['status'] = 'unreadable'
        if not order:
            raise Error('source_snb_contents','The Bambook chapter directory contains no entries.')
        declared = (contents.findtext('head/chapters') or '').strip()
        if declared and declared.lstrip('0')!=str(len(order)):
            builder.add('','unreadable',TOC+'/head/chapters',loc=toc_locator,
                        issue='The Bambook directory does not match its declared chapter count.')
        doc.metadata['snb_contents'] = order
        doc.metadata['source_resources'] = list(resources.values())
        doc.inventory.extend(resources.values())
        if any(item['status']=='unlisted' for item in resources.values()):
            doc.warnings.append('Bambook chapters outside the declared directory are available in the source companion.')
        return doc
