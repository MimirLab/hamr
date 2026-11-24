"""Prepare authored JATS structure for the ordinary document reading pipeline."""

from pathlib import PurePosixPath
from urllib.parse import quote

from lxml import etree

from .document_intake import FORMULA_ELEMENTS, XLINK_HREF, XML_LANG, html_read, name, plain_xml_text, xml_formula
from .xml_projection import XML_SPACE, XMLProjection, append_text, element_text, identifier

NOTE_TYPES = frozenset(('fn','table-fn'))
HTML = frozenset(('p','table','thead','tbody','tfoot','tr','th','td','sup','sub','br','pre'))
FORMATTING = {'bold':'strong','italic':'em','monospace':'code','code':'code','preformat':'pre',
              'disp-quote':'blockquote','list-item':'li','def-list':'dl','def-item':'div',
              'term':'dt','def':'dd','verse-line':'p'}
CONTAINERS = frozenset(('body','abstract','back','sec','ack','app','app-group','fn-group','boxed-text',
                       'disp-quote','fig-group','table-wrap-group','verse-group','statement','glossary'))
METADATA = frozenset(('object-id','article-id','kwd-group','funding-group','permissions','processing-meta',
                     'journal-meta','col','colgroup','related-article','related-object'))
ASSET_TYPES = frozenset(('graphic','inline-graphic','media','supplementary-material','inline-supplementary-material'))


def is_jats(root,filename):
    if root.tag not in ('article','article-set'):
        return False
    articles = [root] if root.tag=='article' else root.findall('article')
    public_id = root.getroottree().docinfo.public_id or ''
    return bool(articles) and (
        PurePosixPath(filename).suffix.lower()=='.jats' or 'Journal Archiving' in public_id or
        'Journal Publishing' in public_id or 'JATS' in public_id or
        any(article.find('front/article-meta') is not None or article.get('dtd-version') for article in articles))


class Article(XMLProjection):
    def __init__(self,article,part):
        super().__init__(article,part)
        self.article = article
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


def asset_links(root):
    for node in root.iter():
        if name(node) in ASSET_TYPES and (href:=node.get(XLINK_HREF)):
            yield node,href
