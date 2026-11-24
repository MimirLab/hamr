"""Prepare authored JATS structure for the ordinary document reading pipeline."""

import re
from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import quote

from lxml import etree

from .core import Error
from .document_intake import (DocumentBuilder, FORMULA_ELEMENTS, MAX_STRUCTURE, XLINK_HREF, XML_LANG,
                              bounded_read, html_read, name, plain_xml_text, xml, xml_formula, xml_root)

XML_ID = '{http://www.w3.org/XML/1998/namespace}id'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
LAYOUT_SPACE = re.compile(r'[ \t\r\n]+')
NOTE_TYPES = frozenset(('fn','table-fn'))
HTML = frozenset(('p','table','thead','tbody','tfoot','tr','th','td','sup','sub','br','pre'))
FORMATTING = {'bold':'strong','italic':'em','monospace':'code','code':'code','preformat':'pre',
              'disp-quote':'blockquote','list-item':'li','def-list':'dl','def-item':'div',
              'term':'dt','def':'dd','verse-line':'p'}
CONTAINERS = frozenset(('body','abstract','back','sec','ack','app','app-group','fn-group','boxed-text',
                       'disp-quote','fig-group','table-wrap-group','verse-group','statement','glossary'))
METADATA = frozenset(('object-id','article-id','kwd-group','funding-group','permissions','processing-meta',
                     'journal-meta','col','colgroup','related-article','related-object'))
LOCATOR_ATTRIBUTES = ('id','headers','scope','rowspan','colspan')
ASSET_TYPES = frozenset(('graphic','inline-graphic','media','supplementary-material','inline-supplementary-material'))
SUFFIXES = ('.xml','.jats')


def is_jats(root,filename):
    if root.tag not in ('article','article-set'):
        return False
    articles = [root] if root.tag=='article' else root.findall('article')
    public_id = root.getroottree().docinfo.public_id or ''
    return bool(articles) and (
        PurePosixPath(filename).suffix.lower()=='.jats' or 'Journal Archiving' in public_id or
        'Journal Publishing' in public_id or 'JATS' in public_id or
        any(article.find('front/article-meta') is not None or article.get('dtd-version') for article in articles))


def identifier(node):
    return node.get('id') or node.get(XML_ID)


def element_text(node):
    return ' '.join(plain_xml_text(node).split())


def normal_text(text,literal=False):
    return text if literal or text is None else LAYOUT_SPACE.sub(' ',text)


def append_text(parent,text):
    if text:
        if len(parent):
            parent[-1].tail = (parent[-1].tail or '')+text
        else:
            parent.text = (parent.text or '')+text


class Article:
    def __init__(self,article,part):
        self.article = article
        self.part = part
        self.tree = article.getroottree()
        self.annotations = {}
        nodes = article.xpath('.//fn[not(ancestor::sub-article or ancestor::response)]')
        self.note_ids = {value for node in nodes if (value:=identifier(node))}
        references = article.xpath('.//xref[not(ancestor::sub-article or ancestor::response)]')
        self.cited_notes = {target for node in references for target in self.note_targets(node)}
        self.definitions = {node:None for node in nodes if identifier(node) and (
            identifier(node) in self.cited_notes or not node.get('fn-type'))}

    def note_targets(self,node):
        targets = (node.get('rid') or '').split()
        kind = node.get('ref-type')
        if kind in NOTE_TYPES:
            return targets
        return [target for target in targets if target in self.note_ids] if not kind else []

    def location(self,node):
        path = self.tree.getpath(node)
        return {'source_file':self.part,'xml_path':path,'lines':[node.sourceline,node.sourceline]}

    def element(self,node,tag,*,attributes=None,**evidence):
        result = etree.Element(tag,attrib=attributes or {})
        label = str(len(self.annotations)+1)
        result.set('data-hamr-node',label)
        for field in LOCATOR_ATTRIBUTES:
            if value:=node.get(field):
                result.set(field,value)
        if anchor:=identifier(node):
            result.set('id',anchor)
        if language:=node.get(XML_LANG):
            result.set('lang',language)
        location = self.location(node)
        self.annotations[label] = {**location,'source_element':self.part+':'+location['xml_path'],**evidence}
        return result

    def children(self,node,result,*,depth,literal=False):
        result.text = normal_text(node.text,literal)
        for child in node:
            if isinstance(child,etree._Entity):
                raise Error('source_xml',f'An unresolved XML entity occurs in {self.part} at line {child.sourceline}.',
                            'Open an edition with the entities expanded in its source.')
            projected = self.project(child,depth=depth,literal=literal) if name(child) else None
            if projected is not None:
                result.append(projected)
            append_text(result,normal_text(child.tail,literal))
        return result

    def reference(self,node,depth,literal):
        location = self.location(node)
        targets = self.note_targets(node)
        if targets or node.get('ref-type') in NOTE_TYPES:
            result = self.element(node,'span')
            for i,target in enumerate(targets or ['']):
                attrs = {'role':'doc-noteref','href':'#'+quote(target,safe='') if target else ''}
                marker = self.element(node,'a',attributes=attrs,inline_location=location)
                marker.attrib.pop('id',None)
                if i==0:
                    self.children(node,marker,depth=depth,literal=literal)
                result.append(marker)
            return result
        href = node.get(XLINK_HREF)
        if name(node)=='xref' and (targets:=node.get('rid','').split()):
            href = '#'+quote(targets[0],safe='')
        basis = {}
        if (alt:=node.get('alt')) and alt.strip():
            basis['reading_span'] = {'source':plain_xml_text(node),'kind':'typed_reading','reading':alt.strip(),
                                     'source_locator':location,'origin':'Authored JATS cross-reference label'}
        result = self.element(node,'a' if href else 'span',attributes={'href':href} if href else None,**basis)
        return self.children(node,result,depth=depth,literal=literal)

    def caption(self,node,depth):
        result = self.element(node,'figcaption')
        for child in node:
            if name(child) not in ('label','caption'):
                continue
            candidates = list(child) if name(child)=='caption' else [child]
            if not candidates:
                candidates = [child]
            for part in candidates:
                if not name(part):
                    continue
                append_text(result,' ')
                view = self.element(part,'span')
                result.append(self.children(part,view,depth=depth))
        return result if len(result) else None

    def object(self,node,depth):
        kind = name(node)
        result = self.element(node,'figure' if kind in ('fig','table-wrap') else 'div')
        caption = self.caption(node,depth)
        if caption is not None:
            result.append(caption)
        text_tables = list(node.findall('table'))+list(node.findall('alternatives/table'))
        tables = text_tables if len(text_tables)==1 else []
        if len(text_tables)>1:
            result.append(self.element(node,'p',source_issue='The table has multiple authored text renditions. Verify the intended table.'))
        if tables:
            result.append(self.project(tables[0],depth=depth))
        elif not text_tables:
            images = list(node.findall('graphic'))+list(node.findall('alternatives/graphic'))
            descriptions = [element_text(child) for child in node if name(child) in ('alt-text','long-desc')]
            display = list(dict.fromkeys(text for text in descriptions if text))
            if len(images)>1 and not display:
                result.append(self.element(node,'p',source_issue='This visual has multiple authored renditions. Verify its description.'))
            elif images:
                image = self.image(images[0],description=' '.join(display))
                captions = {element_text(child) for child in node.findall('caption')}
                if image.get('alt') and image.get('alt') in captions:
                    image.set('aria-hidden','true')
                result.append(image)
            elif display:
                description = self.element(node,'p',source_role='caption')
                description.text = ' '.join(display)
                result.append(description)
        for child in node:
            if name(child) in {'table-wrap-foot','attrib','p','media','list','disp-quote','preformat',
                               'fig','fig-group','table-wrap','table-wrap-group'} | FORMULA_ELEMENTS:
                if (rendered:=self.project(child,depth=depth)) is not None:
                    result.append(rendered)
        return result

    def image(self,node,description=''):
        descriptions = [element_text(child) for child in node if name(child) in ('alt-text','long-desc')]
        desc = description or ' '.join(dict.fromkeys(text for text in descriptions if text))
        attrs = {'alt':desc}
        if href:=node.get(XLINK_HREF):
            attrs['src'] = href
        return self.element(node,'img',attributes=attrs)

    def project(self,node,*,depth=0,literal=False):
        kind = name(node)
        if kind in METADATA or node in self.definitions:
            return None
        if kind in ('sub-article','response','trans-abstract'):
            return self.alternate(node)
        if kind in FORMULA_ELEMENTS:
            display = kind not in ('math','inline-formula') or node.get('display')=='block'
            basis = {'reading_span':xml_formula(node,self.part)}
            if display:
                basis['source_role'] = 'equation'
                if reading:=basis['reading_span']['reading']:
                    if reading[-1] not in '.!?':
                        basis['reading_span']['reading'] = reading+'.'
            return self.element(node,'span' if not display else 'p',**basis)
        if kind in ('xref','ext-link','uri'):
            return self.reference(node,depth,literal)
        if kind in ('fig','table-wrap','supplementary-material','media'):
            return self.object(node,depth)
        if kind in ('graphic','inline-graphic'):
            return self.image(node)
        if kind=='ref-list':
            result = self.element(node,'div')
            for child in node:
                if name(child) in ('title','ref'):
                    ref = self.element(child,'p',source_role='reference')
                    result.append(self.children(child,ref,depth=depth))
                elif name(child)=='ref-list':
                    result.append(self.project(child,depth=depth))
            return result
        literal = node.get(XML_SPACE)=='preserve' or kind in ('pre','preformat') or literal and node.get(XML_SPACE)!='default'
        depth += kind in ('sec','abstract','app','fn-group')
        if kind in ('title','subtitle','article-title'):
            tag = 'p' if any(name(parent) in ('fn','ref','table-wrap-foot','list-item') for parent in node.iterancestors()) else f'h{min(max(depth,1),6)}'
        elif kind=='list':
            tag = 'ol' if node.get('list-type') in ('order','ordered','alpha-lower','alpha-upper','roman-lower','roman-upper') else 'ul'
        else:
            tag = FORMATTING.get(kind) or (kind if kind in HTML else 'div' if kind in CONTAINERS else 'span')
        parent = node.getparent()
        note_label = kind=='label' and parent in self.definitions
        attrs = {'aria-hidden':'true'} if note_label else None
        return self.children(node,self.element(node,tag,attributes=attrs),depth=depth,literal=literal)

    def alternate(self,node):
        element = self.element(node,'div',source_control=True)
        element.text = element_text(node)
        return element

    def html(self):
        body = etree.Element('body')
        meta = self.article.find('front/article-meta')
        if meta is not None:
            for title in meta.findall('title-group/article-title')+meta.findall('title-group/subtitle'):
                body.append(self.project(title))
            abstracts = meta.findall('abstract')
            primary = [abstract for abstract in abstracts if not abstract.get('abstract-type')]
            for abstract in abstracts:
                if primary and abstract not in primary:
                    body.append(self.alternate(abstract))
                else:
                    body.append(self.project(abstract))
            for alternate in meta.findall('trans-abstract'):
                body.append(self.alternate(alternate))
        for section in self.article:
            if name(section) in ('body','back','sub-article','response'):
                body.append(self.project(section))
        for note in self.definitions:
            result = self.element(note,'aside',attributes={'role':'doc-footnote'})
            self.children(note,result,depth=0)
            body.append(result)
        return etree.tostring(body,encoding='unicode',method='html')


def read_jats(root,builder,part,*,collect_resources=False):
    articles = [root] if root.tag=='article' else root.findall('article')
    doc = builder.doc
    if len(articles)==1:
        article, = articles
        meta = article.find('front/article-meta')
        if meta is not None:
            node = meta.find('title-group/article-title')
            if node is not None and (title:=element_text(node)):
                doc.title = title
            if author_nodes:=meta.xpath('./contrib-group/contrib[not(@contrib-type) or @contrib-type="author"]'):
                authors = [author_name(author) for author in author_nodes]
                doc.metadata['authors'] = [author for author in authors if author]
            if ids:=[element_text(ident) for ident in meta.findall('article-id') if element_text(ident)]:
                doc.metadata['source_identifiers'] = ids
        if language:=article.get(XML_LANG):
            doc.language = language
    for node in articles:
        article = Article(node,part)
        html_read(article.html(),builder,metadata=False,where=part,collect_resources=collect_resources,
                  source_annotations=article.annotations)
    if not collect_resources:
        doc.inventory.extend({**asset_reference(node,href,part),'status':'unavailable'}
                             for node,href,_ in assets(root,part))


def author_name(author):
    for tag in ('string-name','collab'):
        node = author.find(tag)
        if node is not None and (text:=element_text(node)):
            return text
    node = author.find('name')
    if node is None:
        return ''
    order = ('surname','given-names') if node.get('name-style')=='eastern' else ('given-names','surname')
    return ' '.join(element_text(child) for field in ('prefix',*order,'suffix')
                    if (child:=node.find(field)) is not None and element_text(child))


def assets(root,part):
    from .opf_intake import target

    for node in root.iter():
        if name(node) in ASSET_TYPES and (href:=node.get(XLINK_HREF)):
            yield node,href,target(part,href)


def asset_reference(node,href,part):
    return {'kind':'jats_asset','href':href,'member':part,'xml_path':node.getroottree().getpath(node),
            'line':node.sourceline}


def candidate(stream,filename):
    return PurePosixPath(filename).suffix.lower() in SUFFIXES and xml_root(stream) in ('article','article-set')


def loose_article(path):
    with path.open('rb') as stream:
        if not candidate(stream,path.name):
            return None
    root = xml(bounded_read(path))
    return root if is_jats(root,path.name) else None


def declared_files(path,root):
    from .source_bundle import relative_file, stamp

    result = {path.name:stamp(path.stat())}
    for _,_,target in assets(root,path.name):
        if target and target not in result:
            try:
                result[target] = stamp(relative_file(path.parent,target).stat())
            except Error as exc:
                if exc.code!='source_member_missing':
                    raise
    return result


def capture_jats(path,target_dir,source):
    from .publication_package import capture_folder
    from .source_bundle import relative_file, stamp

    before = stamp(path.stat())
    root = loose_article(path)
    if root is None:
        return None
    anchor = relative_file(path.parent,path.name)
    listed = declared_files(anchor,root)
    if listed[path.name]!=before:
        raise Error('source_changed','The selected JATS article changed before capture.')
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
        if not is_jats(root,member.name):
            continue
        title = root.find('front/article-meta/title-group/article-title')
        books.append((member,element_text(title) if title is not None else member.name))
        claimed.add(member.index)
        for _,_,path in assets(root,member.name):
            claimed.update(asset.index for asset in by_name.get(path,()))
    return books,claimed


def read_jats_archive(path,source,sha,title):
    from .archive_intake import containers
    from .opf_intake import target
    from .publication_package import bind_member_block, member_bytes, member_location

    with containers(path,source.kind) as archive:
        members = list(archive.members())
        text = next((item for item in members if item.index==source.entry),None)
        if text is None:
            raise Error('source_member_missing','The saved JATS article is missing from the source archive.')
        root = xml(member_bytes(archive,text,MAX_STRUCTURE))
        if not is_jats(root,text.name):
            raise Error('source_xml','The selected source archive member does not identify a JATS article.')
        builder = DocumentBuilder('xml-book',title,sha)
        read_jats(root,builder,text.name,collect_resources=True)
        doc = builder.doc
        by_name = defaultdict(list)
        for member in members:
            by_name[member.name].append(member)
        records = {text.name:{**member_location(sha,text,'text'),'status':'represented'}}
        for node,href,asset_path in assets(root,text.name):
            matched = by_name.get(asset_path,())
            reference = asset_reference(node,href,text.name)
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
