"""Thin, format-aware readers feeding the common Block and Document model."""
from contextlib import contextmanager
import csv
from email import policy
from email.parser import BytesParser
import io
from itertools import zip_longest
import json
from pathlib import PurePosixPath
import re
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup, NavigableString, Tag
from lxml import etree

from .core import Block, Document, Error, key
from .source_math import MATHML, MATH_SPANS, mathml, mathml_text, speak

MAX_STRUCTURE = 32*1024*1024
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
MATH_ROOT = '{'+MATHML+'}math'
FORMULA_ELEMENTS = frozenset(('math','formula','disp-formula','inline-formula','equation','informalequation'))
CONTENT_TAGS = frozenset(('p','para','simpara','title','subtitle','h','hd','heading',
                          'li','item','line','v','l','caption','blockquote','epigraph','programlisting'))
BOOK_ROOTS = frozenset(('FictionBook','book','article','article-set','TEI','teiCorpus','dtbook',
                        'abiword','awml','section','reference','refentry','docbook','book-part-wrapper'))


def name(element):
    return element.tag.rsplit('}',1)[-1] if isinstance(element.tag,str) else ''


def attr(element,key):
    return next((value for field,value in element.attrib.items() if field.rsplit('}',1)[-1]==key),None)


def multiple_cells(value):
    return value is not None and re.fullmatch(r'0*1',value.strip()) is None


def xml(data):
    try:
        return etree.fromstring(data,etree.XMLParser(load_dtd=False,no_network=True,resolve_entities=False))
    except etree.XMLSyntaxError as exc:
        raise Error('source_xml', 'The selected structured document has unreadable XML.') from exc


def xml_start(stream):
    parser = etree.XMLPullParser(events=('start',),load_dtd=False,no_network=True,resolve_entities=False)
    for _ in range(16):
        data = stream.read(4096)
        if not data:
            return None
        try:
            parser.feed(data)
        except etree.XMLSyntaxError:
            break
        for _,element in parser.read_events():
            return element
    return None


def xml_root(stream):
    element = xml_start(stream)
    return name(element) if element is not None else None


def xml_route(stream):
    from .docbook_intake import is_docbook
    from .odf_content import flat_text
    from .odf_presentation import flat_presentation

    element = xml_start(stream)
    if flat_text(element):
        return 'odt'
    if flat_presentation(element):
        return 'odp'
    if element is not None and (name(element) in BOOK_ROOTS or is_docbook(element,'')):
        return 'xml-book'


def bounded_read(path):
    if path.stat().st_size>MAX_STRUCTURE:
        raise Error('source_structure_size', 'This structured reader cannot buffer the selected source within its memory budget.',
                    'Divide the document by chapter or use a compatible local export.')
    return path.read_bytes()


class DocumentBuilder:
    def __init__(self,format,title,sha):
        self.doc = Document(title or 'Book',format,sha,[])
        self.chapter = 'Opening'
        self.chapter_id = 'document:opening'

    def add(self,text,role='body',where='',*,loc=None,links=None,issue=None):
        from .importers import block

        text = text.strip()
        if not (text or issue or loc and (loc.get('inline_semantics') or loc.get('table_grid'))):
            return
        ordinal = len(self.doc.blocks)+1
        if text and (role=='heading' or loc and loc.get('chapter_marker')):
            self.chapter = text
            self.chapter_id = f'document:{ordinal}'
        location = {'format':self.doc.format,'ordinal':ordinal,'element':where,
                    'chapter_id':self.chapter_id,'location_id':key([self.doc.format,ordinal,where])[:20],
                    **(loc or {})}
        item = block(text,location,role,self.chapter,issue=issue,links=links)
        self.doc.blocks.append(item)
        return item

    def issue(self,reason,where):
        return self.add('','unreadable',where,issue=reason)

    def table(self,rows,where,*,headers=None,issue=None,semantics=None):
        text = '\n'.join(' | '.join(cell for cell in row) for row in rows)
        if not text.strip(' |\n') and not issue:
            return
        loc = {'rows':rows,'table_header_cells': [
            c for c in range(len(rows[0])) if 0 in headers] if headers is not None else None}
        if semantics:
            loc['table_cell_semantics'] = semantics
        return self.add(text,'table',where,loc=loc,issue=issue)

    def grid(self,grid,where,*,loc=None):
        from .table_grid import passages, source_rows, source_text

        text = source_text(grid)
        if text or grid['issue'] or any(part['spans'] for part in passages(grid)):
            return self.add(text,'table',where,loc={**(loc or {}),'table_grid':grid,'rows':source_rows(grid)},issue=grid['issue'])


def formula_span(raw, parse, start, origin, locator):
    try:
        reading, issue = speak(parse()), None
    except ValueError as error:
        reading, issue = None, str(error)
    return {'kind':'formula','source_start':start,'source_end':start+len(raw),'source':raw,
            'reading':reading,'issue':issue,'origin':origin,'source_locator':locator}


def join_formulas(paragraphs):
    text,spans = [],[]
    offset = 0
    for content,parts in paragraphs:
        text.append(content)
        spans.extend({**span,'source_start':span['source_start']+offset,'source_end':span['source_end']+offset}
                     for span in parts if span['kind'] in MATH_SPANS)
        offset += len(content)+1
    return ' '.join(text),spans


def formula_note(link,text,spans,issue=None):
    from .epub_semantics import reading, uncertainty

    link['text'] = text
    if spans:
        link['inline_semantics'] = spans
        link['reading'] = reading(Block('',text,{'inline_semantics':spans}))
    if problem := issue or uncertainty(spans):
        link['issue'] = problem
    return link


def cell_semantics(row,column,text,spans):
    return {'row':row,'column':column,'source':text,'spans':spans,'notes':[]}


def renderer_controls(body,docutils,sphinx):
    controls = []
    if docutils or sphinx:
        controls.extend(body.select('aside[role="doc-footnote"] > span.label, span.sphinx-navigation'))
    if sphinx:
        controls.extend(body.select('a.headerlink, span.section-number'))
        generated = {'genindex.html','modindex.html','py-modindex.html','search.html'}
        controls.extend(node for node in body.select('a.reference.internal[href]') if node['href'] in generated)
    return {id(node):{'source':node.get_text(),'kind':'hidden','disposition':'companion',
                      'evidence':'Renderer navigation or note label'} for node in controls}


def html_too_deep(root):
    nodes = [(root,0)]
    while nodes:
        node,depth = nodes.pop()
        if depth>=128:
            return True
        nodes.extend((child,depth+1) for child in node.children if isinstance(child,Tag))
    return False


def html_reference_evidence(soup):
    fields = ('id','name','href','src','alt','aria-label','role','colspan','rowspan')
    for tag in soup.find_all(True):
        facts = tuple((field,tag[field]) for field in fields if field in tag.attrs)
        if facts or tag.name in ('img','svg','math','audio','video'):
            yield tag.name,facts


def html_tree(data,encoding):
    soup = BeautifulSoup(data,'html.parser',from_encoding=encoding)
    if not html_too_deep(soup):
        return soup,False
    recovered = BeautifulSoup(data,'lxml',from_encoding=encoding or soup.original_encoding)
    original_words = (word for piece in soup.stripped_strings for word in piece.split())
    recovered_words = (word for piece in recovered.stripped_strings for word in piece.split())
    references = zip_longest(html_reference_evidence(soup),html_reference_evidence(recovered))
    if (html_too_deep(recovered) or any(a!=b for a,b in zip_longest(original_words,recovered_words))
            or any(a!=b for a,b in references)):
        raise Error('source_html_structure','Deeply nested HTML could not be repaired without losing or moving text.',
                    'Open a repaired HTML or EPUB export with the original reading order.')
    return recovered,True


def html_read(data,builder, *, where='html', part=None, metadata=True, fragment=None, encoding=None, collect_links=False,
              collect_resources=False, source_annotations=None, docutils=False):
    from urllib.parse import unquote
    from .epub_semantics import AUDIT_SPANS, hidden, note_container, reading, uncertainty
    from .html_navigation import illustration_titles, linked_contents
    from .html_notes import RenderedNotes
    from .importers import html_text
    from .epub_semantics import describe as semantics
    from .source_notes import Definition, NoteGraph, note_text
    from .source_numbering import bind_ordered, html_ordered_items

    soup,recovered = html_tree(data,encoding)
    if recovered:
        builder.doc.inventory.append({'kind':'html_structure_recovered','element':where,
                                      'basis':'HTML recovery preserved the parsed words and references in order'})
    sphinx = any(item.get('src','').split('?',1)[0].endswith('/documentation_options.js')
                 or item.get('id')=='documentation_options' for item in soup.find_all('script'))
    for node in soup.find_all(['script','style','template','noscript']):
        node.decompose()
    if metadata and soup.title and soup.title.get_text(strip=True):
        builder.doc.title = soup.title.get_text(' ',strip=True)
    if metadata and soup.html and soup.html.get('lang'):
        builder.doc.language = soup.html['lang']
    if soup.find('link',rel='stylesheet'):
        builder.doc.warnings.append('External stylesheet retained as a reference. Its remote contents were not fetched.')
    body = soup.body or soup
    if not fragment:
        main = body.select('div#mdbook-content.content > main')
        if len(main)==1:
            body = main[0]
            builder.doc.inventory.append({'kind':'html_main','element':'#mdbook-content > main',
                                          'basis':'mdBook rendered publication region'})
        elif sphinx:
            main = body.select('.body[role="main"], main[role="main"], main#main-content')
            if len(main)==1:
                body = main[0]
                builder.doc.inventory.append({'kind':'html_main','element':'main',
                                              'basis':'Sphinx rendered publication region'})
    markup = RenderedNotes(body)
    if fragment:
        targets = body.find_all(lambda node: node.get('id')==fragment or
                                node.name=='a' and node.get('name')==fragment)
        if len(targets)!=1 or targets[0].name in ('a','h1','h2','h3','h4','h5','h6','span'):
            raise Error('source_topic_fragment','The topic link does not name one bounded HTML region.')
        body = targets[0]
    contents = linked_contents(body,part or where)
    illustrations,illustration_index = illustration_titles(body,part or where)
    contents.update(illustration_index)
    ordinals = html_ordered_items(body)
    projected = renderer_controls(body,docutils,sphinx)
    inline_locations = {}
    if source_annotations:
        for node in body.find_all(attrs={'data-hamr-node':True}):
            annotation = source_annotations.get(node['data-hamr-node'],{})
            if span:=annotation.get('reading_span'):
                projected[id(node)] = span
            if location:=annotation.get('inline_location'):
                inline_locations[id(node)] = location
    notes = {}
    atomic = {'p','h1','h2','h3','h4','h5','h6','table','pre','img','svg','math','figcaption','dt','dd','li',
              'blockquote','address','caption'}

    def preserve_resources(block,node):
        if not block or not collect_resources:
            return
        resources = []
        for item in [node,*node.find_all(['a','img','image'])]:
            if item.name not in ('a','img','image'):
                continue
            href = item.get('src') or item.get('href') or item.get('xlink:href')
            if href:
                resources.append({'kind':'link' if item.name=='a' else 'image','href':href,
                                  'label':item.get('alt') or item.get_text(' ',strip=True)})
        if resources:
            block.locator['resource_references'] = resources

    def local_notes(spans):
        for span in spans:
            if span['kind']=='note_reference' and (span.get('href') or '').startswith('#'):
                span['target_href'] = unquote(span['href'])

    def annotated_semantics(node,*,note_body=False,children=None):
        source,spans = semantics(node,resolve_asset=illustrations.get if illustrations else None,
                                 note_markup=markup,note_body=note_body,projected=projected,children=children)
        for span in spans:
            if span['kind']=='object_label' and (caption:=span.get('image_resource')):
                span['image_resource'] = caption['image']
                if span['issue']:
                    label = caption['title']
                    if not re.search(r'''[.!?]["'”’\)\]]*$''',label):
                        label += '.'
                    span.update(description_source=caption,reading='' if source else label,issue=None)
        if inline_locations:
            from bs4 import Tag

            anchors = (node.find_all('a') if children is None else
                       [anchor for child in children if isinstance(child,Tag)
                        for anchor in ([child] if child.name=='a' else child.find_all('a'))])
            markers = [(tag.get('href') or '',inline_locations[id(tag)])
                       for tag in anchors if id(tag) in inline_locations]
            notes = [span for span in spans if span['kind']=='note_reference']
            if len(markers)==len(notes) and all(href==span['href'] for (href,_),span in zip(markers,notes)):
                for (_,location),span in zip(markers,notes):
                    span['source_locator'] = location
        return source,spans

    def source_issue(spans):
        return next((span['issue'] for span in spans if span['kind']=='source_review'),None) or uncertainty(spans)

    def text_node(node,role,where):
        source,spans = annotated_semantics(node,note_body=role=='note')
        if role=='body' and not source and any(span.get('description_source') for span in spans):
            role = 'caption'
        spans = [span for span in spans if span['kind'] not in AUDIT_SPANS or
                 collect_links and span['kind']=='hyperlink']
        local_notes(spans)
        issue = source_issue(spans)
        loc = {'anchor':node.get('id'),'inline_semantics':spans,'exact':str(node)}
        names = [tag['name'] for tag in node.find_all('a',attrs={'name':True}) if tag['name']]
        if names:
            loc['container_anchors'] = list(dict.fromkeys(names))
        block = builder.add(source,role,where,loc=loc,issue=None if role=='note' else issue)
        if block and role=='heading' and projected and any(id(child) in projected for child in node.find_all(True)):
            if label:=reading(block):
                block.chapter = builder.chapter = label
        preserve_resources(block,node)
        bind_ordered(block,node,ordinals)
        if block and (node.name == 'blockquote' or node.find_parent('blockquote') is not None):
            block.locator['block_quote'] = True
        if block and role=='note' and node.get('id'):
            value = note_text(block,terminal=id(node) in markup.definitions or node.get('role')=='doc-footnote')
            if issue:
                block.locator['note_issue'] = issue
            notes.setdefault('#'+node['id'],[]).append(Definition(block,value,issue))

    def visit(node,path):
        annotation = source_annotations.get(node.get('data-hamr-node')) if (
            source_annotations and not isinstance(node,NavigableString)) else None
        if annotation:
            path = annotation.get('source_element',path)
        if annotation and annotation.get('source_control'):
            builder.add(node.get_text(' ',strip=True),'navigation',path,loc=annotation)
            return
        if annotation and (issue:=annotation.get('source_issue')):
            item = builder.add('','unreadable',path,loc=annotation,issue=issue)
            preserve_resources(item,node)
            return
        first = len(builder.doc.blocks)
        if annotation and (role:=annotation.get('source_role')):
            text_node(node,role,path)
        else:
            visit_content(node,path)
        if annotation:
            for item in builder.doc.blocks[first:]:
                for field,value in annotation.items():
                    if field not in ('source_role','reading_span','source_element','inline_location'):
                        item.locator.setdefault(field,value)
        if first<len(builder.doc.blocks) and not isinstance(node,NavigableString):
            anchor = node.get('id') or (node.get('name') if node.name=='a' else None)
            block = builder.doc.blocks[first]
            if anchor and anchor!=block.locator.get('anchor') and not any(
                    span['kind']=='anchor' and span['id']==anchor for span in block.locator.get('inline_semantics',())):
                block.locator.setdefault('container_anchors',[]).append(anchor)

    def visit_content(node,path):
        if isinstance(node,NavigableString):
            from .epub_semantics import NON_TEXT
            if not isinstance(node,NON_TEXT):
                block = builder.add(str(node),where=path)
                bind_ordered(block,node.parent,ordinals)
            return
        if node.name in ('head','title','meta','link'):
            return
        if id(node) in contents:
            text_node(node,'navigation',path)
            return
        if node.get('id') and (note_container(node) or id(node) in markup.definitions):
            text_node(node,'note',path)
            return
        if hidden(node) or node.name=='nav':
            builder.add(node.get_text(' ',strip=True),'hidden',path)
            return
        if node.name=='table':
            lines = [line for line in node.find_all('tr') if line.find_parent('table') is node]
            if node.get('data-view')=='cards':
                for row,line in enumerate(lines):
                    for column,cell in enumerate(line.find_all('td',recursive=False)):
                        where=f'{path}/card/{row+1}/{column+1}'
                        glyphs=cell.find_all('i',class_=re.compile(r'^fa-'))
                        if cell.has_attr('data-hidden') or (glyphs and html_text(cell).strip()==
                                ''.join(html_text(glyph) for glyph in glyphs).strip()):
                            text_node(cell,'hidden',where)
                        else:
                            visit(cell,where)
                return
            from .html_table import read_grid, requires_grid

            if requires_grid(node):
                ranks = {id(tag):rank for rank,tag in enumerate(node.descendants)}
                def read_cell(owner,children):
                    source,spans = annotated_semantics(owner,children=children)
                    local_notes(spans)
                    return source,spans,source_issue(spans)
                def locate(owner):
                    return {'element':path,'table_dom_rank':ranks[id(owner)],
                            **({'anchor':owner['id']} if owner.get('id') else {})}
                block = builder.grid(read_grid(node,read_cell,locate),path)
                preserve_resources(block,node)
                return
            headers = [0] if lines and lines[0].find('th',recursive=False) else []
            issue = ('Complex table spans or nesting need a verified reading.' if
                     node.find('table') or node.find(attrs={'rowspan':multiple_cells}) or
                     node.find(attrs={'colspan':multiple_cells}) else None)
            rows,entries = [],[]
            for row,line in enumerate(lines):
                cells = []
                for column,cell in enumerate(line.find_all(['th','td'],recursive=False)):
                    semantic = cell.find('math') or any(id(tag) in projected for tag in cell.find_all(True))
                    if semantic or cell.find('a'):
                        source,spans = annotated_semantics(cell)
                    else:
                        spans = []
                    if semantic or any(span['kind']=='note_reference' for span in spans):
                        local_notes(spans)
                        issue = issue or source_issue(spans)
                        entries.append(cell_semantics(row,column,source,spans))
                    else:
                        source = html_text(cell).strip()
                    cells.append(source)
                rows.append(cells)
            block = builder.table(rows,path,headers=headers,issue=issue,semantics=entries)
            preserve_resources(block,node)
            if caption:=node.find('caption',recursive=False):
                text_node(caption,'caption',path+'/caption')
            return
        if node.name in atomic and not (node.name in ('li','blockquote') and node.find(list(atomic))):
            roles = {'pre':'code','figcaption':'caption','caption':'caption','img':'figure','svg':'figure'}
            role = 'heading' if re.fullmatch(r'h[1-6]',node.name) else roles.get(node.name,'body')
            text_node(node,role,path)
            return
        if not node.find(list(atomic)) and node.get_text(strip=True):
            text_node(node,'body',path)
            return
        for i,child in enumerate(node.children,1):
            visit(child,f'{path}/{i}')

    start = len(builder.doc.blocks)
    visit(body,where)
    graph = NoteGraph(notes.get)
    for block in builder.doc.blocks[start:]:
        block.links.extend(graph.links_for(block.locator.get('inline_semantics',()),follow=block.role!='note'))
        if cells:=block.locator.get('table_cell_semantics'):
            graph.bind_cells(cells)
            block.links.extend(link for cell in cells for link in cell['notes'])
        if grid:=block.locator.get('table_grid'):
            from .table_grid import passages

            cells = list(passages(grid))
            graph.bind_cells(cells)
            block.links.extend(link for cell in cells for link in cell['notes'])


@contextmanager
def structured_zip(path):
    from .source_bundle import zip_members

    try:
        with ZipFile(path) as archive:
            index = {}
            for _,entry in zip_members(archive):
                if entry.filename in index:
                    raise Error('ambiguous_package_member', f'Duplicate package member {entry.filename}.')
                index[entry.filename] = entry

            def read(name,required=True):
                if name not in index:
                    if required:
                        raise Error('source_member_missing', f'The document package is missing {name}.')
                    return None
                if index[name].file_size>MAX_STRUCTURE:
                    raise Error('source_structure_size', f'Structured member exceeds the memory budget: {name}.')
                return archive.read(index[name])

            yield read,tuple(index)
    except BadZipFile as exc:
        raise Error('unreadable_archive','The document package could not be read.') from exc


def plain_xml_text(node):
    return ''.join(node.itertext()).strip()


def formula_elements(node, *, element_name, children):
    if element_name(node) in FORMULA_ELEMENTS:
        yield node
        return
    for child in children(node):
        yield from formula_elements(child,element_name=element_name,children=children)


def xml_formula(node, part):
    locator = part+':'+node.getroottree().getpath(node)
    candidates = [node] if node.tag==MATH_ROOT else []
    label = ''
    if name(node)!='math':
        content = [child for child in node if name(child) not in ('','label','object-id')]
        if len(content)==1:
            child = content[0]
            if child.tag==MATH_ROOT:
                candidates = [child]
            elif name(child)=='alternatives':
                candidates = [item for item in child if item.tag==MATH_ROOT]
        labels = [plain_xml_text(child) for child in node if name(child)=='label']
        if len(labels)==1:
            label = labels[0]
    if len(candidates)!=1:
        raw = plain_xml_text(node)
        return {'kind':'formula','source':raw,'source_start':0,'source_end':len(raw),'reading':None,
                'origin':'Authored XML equation','source_locator':locator,
                'issue':'The equation needs one bound MathML representation.'}
    math = candidates[0]
    raw = mathml_text(math)
    locator = part+':'+math.getroottree().getpath(math)
    result = formula_span(raw,lambda:mathml(math),0,'Authored XML equation',locator)
    if label:
        result['source'] = label+' '+raw
        result['source_end'] = len(result['source'])
        if result['reading']:
            prefix = 'Equation '+label[1:-1] if re.fullmatch(r'\([0-9][0-9a-z.]*\)',label) else label
            result['reading'] = prefix.rstrip('.')+'. '+result['reading']
    return result


def semantic_xml(root,builder,part):
    title_node = next((item for item in root.iter() if name(item) in ('book-title','article-title','doctitle')),None)
    if title_node is not None and plain_xml_text(title_node):
        builder.doc.title = plain_xml_text(title_node)
    if language:=root.get(XML_LANG):
        builder.doc.language = language
    if name(root) in ('TEI','teiCorpus'):
        children = [n for n in root if name(n) in ('text','TEI')]
    elif name(root) in ('article','article-set'):
        children = [n for n in root if name(n) in ('front','body','back','article')]
    else:
        children = [root]
    notes = {n.get('id'):n for n in root.iter() if name(n) in ('fn','note') and n.get('id')}

    def inline(node):
        from .epub_semantics import describe, uncertainty

        clone = BeautifulSoup(etree.tostring(node,encoding='unicode',with_tail=False),'html.parser')
        for item in clone.find_all(lambda n: n.name and ':' in n.name):
            item.name = item.name.rsplit(':',1)[-1]
        originals = list(formula_elements(node,element_name=name,children=iter))
        rendered = list(formula_elements(clone,element_name=lambda n:n.name,children=lambda n:n.find_all(True,recursive=False)))
        if [name(item) for item in originals]!=[item.name for item in rendered]:
            return plain_xml_text(node),[],'Could not locate the authored equation in the selected passage.'
        projected = {id(view):xml_formula(original,part) for original,view in zip(originals,rendered)}
        source,spans = describe(clone,projected=projected)
        return source,spans,uncertainty(spans)

    def has_formula(node):
        return next(formula_elements(node,element_name=name,children=iter),None) is not None

    def includes_visual(node):
        if name(node) in FORMULA_ELEMENTS:
            return False
        return name(node) in ('graphic','image','inline-graphic') or any(includes_visual(child) for child in node)

    def visit(node):
        tag = name(node)
        where = part+':'+node.getroottree().getpath(node)
        if tag in ('description','binary','style','script','teiHeader','article-meta','journal-meta','bookinfo'):
            return
        if tag in FORMULA_ELEMENTS:
            from .epub_semantics import uncertainty

            span = xml_formula(node,part)
            if span['reading'] and span['reading'][-1] not in '.!?':
                span['reading'] += '.'
            builder.add(span['source'],'equation',where,loc={'inline_semantics':[span]},issue=uncertainty([span]))
            return
        if tag in ('graphic','image','inline-graphic'):
            text = attr(node,'alt') or attr(node,'alttext')
            if not text:
                alternative = next((n for n in node if name(n) in ('alt-text','math')),None)
                if alternative is not None:
                    text = plain_xml_text(alternative)
            if text:
                builder.add(text,'caption',where)
            else:
                builder.issue('An embedded visual needs a verified reading.',where)
            return
        if tag in ('table','informaltable'):
            rows,entries = [],[]
            issue = ('Merged or nested cells need a verified reading.' if any(
                any(multiple_cells(attr(x,k)) for k in ('rowspan','colspan')) or
                any(attr(x,k) for k in ('morerows','namest','nameend')) or
                name(x) in ('table','informaltable') and x is not node
                for x in node.iter()) else None)
            for tr in (item for item in node.iter() if name(item) in ('tr','row')):
                row = []
                for cell in (item for item in tr if name(item) in ('td','th','entry','cell')):
                    text = plain_xml_text(cell)
                    if has_formula(cell):
                        text,spans,problem = inline(cell)
                        issue = issue or problem
                        entries.append(cell_semantics(len(rows),len(row),text,spans))
                    row.append(text)
                rows.append(row)
            builder.table(rows,where,headers=[] if not any(name(x)=='th' for x in node.iter()) else [0],
                          issue=issue,semantics=entries)
            return
        if tag in ('note','fn') and node.get('id'):
            loc = {'anchor':node.get('id')}
            text = plain_xml_text(node)
            if has_formula(node):
                text,spans,_ = inline(node)
                loc['inline_semantics'] = spans
            builder.add(text,'note',where,loc=loc)
            return
        if tag in CONTENT_TAGS and not any(name(child) in CONTENT_TAGS for child in node):
            source,spans,issue = inline(node)
            links = []
            # Some structured XML uses xref/rid instead of an HTML anchor.
            if any(name(child) in ('xref','noteref','a') for child in node.iterdescendants()):
                for child in node.iterdescendants():
                    ref = (attr(child,'rid') or attr(child,'href') or '').lstrip('#')
                    if ref not in notes:
                        continue
                    marker = plain_xml_text(child)
                    if marker and source.count(marker)==1:
                        start = source.index(marker)
                        spans.append({'kind':'note_reference','source':marker,'source_start':start,
                                      'source_end':start+len(marker),'href':'#'+ref})
                        link = {'kind':'note','href':'#'+ref,'source_start':start}
                        note = notes[ref]
                        if has_formula(note):
                            text,parts,problem = inline(note)
                            links.append(formula_note(link,text,parts,problem))
                        else:
                            links.append({**link,'text':plain_xml_text(note)})
                    else:
                        issue = 'Could not establish the exact position of an authored note.'
            role = ('heading' if tag in ('title','subtitle','hd','h','heading') else
                    'caption' if tag=='caption' else 'code' if tag=='programlisting' else 'body')
            if includes_visual(node):
                issue = issue or 'A paragraph contains inline visual content requiring review.'
            builder.add(source,role,where,loc={'inline_semantics':spans},links=links,issue=issue)
            return
        if node.text and node.text.strip():
            builder.add(node.text,where=where)
        for child in node:
            if name(child):
                visit(child)
            if child.tail and child.tail.strip():
                builder.add(child.tail,where=where+'/tail')
    for child in children:
        visit(child)


def odt(path,builder,source_file=None):
    from .odf_content import Content, OFFICE, flat_text

    with path.open('rb') as stream:
        flat = flat_text(xml_start(stream))
    if flat:
        document = xml(bounded_read(path))
        _odt_body(document,builder,Content(None,source_file or path.name),document.find(OFFICE+'meta'))
    else:
        with structured_zip(path) as (read,_):
            document = xml(read('content.xml'))
            metadata = read('meta.xml',required=False)
            _odt_body(document,builder,Content(read),xml(metadata) if metadata else None)


def _odt_body(document,builder,content,metadata):
    from .odf_content import OFFICE
    from .table_grid import simple_matrix

    if metadata is not None:
        title = next((plain_xml_text(n) for n in metadata.iter() if name(n)=='title'),None)
        if title:
            builder.doc.title = title
    body = document.find(OFFICE+'body')
    if body is None:
        raise Error('source_xml','OpenDocument has no document body.')
    if any(name(n)=='changed-region' for n in body.iter()):
        builder.doc.warnings.append('Tracked change records are preserved in the original. The visible body is selected.')

    def visit(node):
        tag = name(node)
        loc = content.source_file+':'+node.getroottree().getpath(node)
        if tag in ('p','h') or node.tag==MATH_ROOT:
            text,spans,links,issue = content.paragraph(node)
            item = builder.add(text,'heading' if tag=='h' else 'body',loc,
                               loc={'inline_semantics':[span for span in spans if span['kind']!='line_break']},
                               links=links,issue=issue)
            if item and any(span['kind']=='line_break' and span['source_end']>span['source_start'] for span in spans):
                item.locator['authored_line_breaks'] = True
        elif tag=='table':
            grid = content.table(node)
            if matrix:=simple_matrix(grid):
                rows,headers,entries = matrix
                builder.table(rows,loc,headers=headers,semantics=entries)
            else:
                builder.grid(grid,loc)
        elif tag not in ('tracked-changes','annotation','deletion','scripts','forms'):
            for child in node:
                visit(child)
    visit(body)


def mime_read(data,builder,route):
    item = BytesParser(policy=policy.default).parsebytes(data)
    if item.get_content_type() in ('multipart/encrypted','application/pkcs7-mime'):
        raise Error('protected_source','This saved message is encrypted or protected.',
                    'Supply a readable local export.')
    if route=='mhtml' and item.is_multipart() and item.get_param('start'):
        start = item.get_param('start').strip('<>')
        body = next((part for part in item.iter_parts() if str(part.get('Content-ID','')).strip('<>')==start),None)
        if body is None:
            raise Error('source_member_missing','MHTML identifies a root part that is missing.')
    else:
        body = item.get_body(preferencelist=('html','plain'))
    if body is None:
        raise Error('empty_document','No supported readable body was selected in this saved message.')
    if subject:=item.get('Subject'):
        builder.doc.title = str(subject)
    builder.doc.inventory = [{'part':part.get_content_type(),'content_location':part.get('Content-Location'),
                              'status':'selected' if part is body else 'retained in original'}
                             for part in item.walk() if not part.is_multipart()]
    content = body.get_content()
    if body.get_content_type()=='text/html':
        html_read(content,builder)
    elif body.get_content_type()=='text/plain':
        for index,section in enumerate(re.split(r'\n\s*\n',content),1):
            builder.add(section,where=f'mime-body:{index}')
    else:
        raise Error('unsupported_format','The selected MIME root is not readable text or HTML.')


def read_document(path,source,sha,cache,title):
    route = source.route
    builder = DocumentBuilder(route,title,sha)
    if route=='docx':
        from .word_intake import read_word

        read_word(path,builder)
    elif route=='odt':
        odt(path,builder,source.member_name)
    elif route=='abiword':
        from .abiword_intake import read_abiword, read_xml

        read_abiword(read_xml(path),builder,source.member_name or path.name)
    elif route=='xlsx':
        from .structured_intake import read_xlsx

        read_xlsx(path,builder)
    elif route=='mobi':
        from .intake_helpers import resident_mobi
        return resident_mobi(path,source,sha,cache)
    elif route=='legacy-ebook':
        from .intake_helpers import legacy_ebook
        return legacy_ebook(path,source,sha,cache)
    elif route=='office-legacy':
        from .intake_helpers import legacy_document
        return legacy_document(path,source,builder,cache)
    elif route=='wordperfect':
        from .legacy_office_intake import read_wordperfect

        return read_wordperfect(path,source,builder,cache)
    else:
        data = bounded_read(path)
        if route=='html':
            html_read(data,builder,part=source.member_name or (title+path.suffix if title else path.name))
        elif route in ('xml-book','fb2'):
            from .abiword_intake import ROOTS as ABIWORD_ROOTS, read_abiword
            from .dtbook_intake import is_dtbook, read_dtbook
            from .fictionbook_intake import read_fictionbook
            from .xml_package import read_projected

            root = xml(data)
            part = source.member_name or PurePosixPath(title).name+path.suffix
            if is_dtbook(root):
                read_dtbook(root,builder,part)
            elif name(root)=='FictionBook':
                read_fictionbook(root,builder,part)
            elif root.tag in ABIWORD_ROOTS:
                read_abiword(root,builder,part)
            elif not read_projected(root,builder,part):
                if name(root) not in BOOK_ROOTS:
                    raise Error('unsupported_format', f'No book reader is available for XML root {name(root)!r}.')
                semantic_xml(root,builder,path.name)
        elif route in ('tcr','amigaguide'):
            from .compact_documents import read_guide, read_tcr
            (read_tcr if route=='tcr' else read_guide)(data,builder)
        elif route=='rtf':
            from .rtf_intake import read_rtf
            read_rtf(data,builder)
        elif route in ('mhtml','mail'):
            mime_read(data,builder,route)
        elif route=='csv':
            dialect = 'excel-tab' if path.suffix.lower()=='.tsv' else 'excel'
            rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'),newline=''),dialect,strict=True))
            if rows:
                builder.table(rows,'delimited table',headers=[0])
        elif route=='notebook':
            from .notebook_intake import read_notebook

            read_notebook(data,builder)
        elif route=='outline':
            from .structured_intake import read_outline
            read_outline(data,builder)
        elif route=='subtitles':
            from .intake_helpers import subtitles
            subtitles(data,builder)
        else:
            raise Error('unsupported_format', f'No reader is registered for {route}.')
    return builder.doc
