"""Thin, format-aware readers feeding the common Block and Document model."""
from contextlib import contextmanager
import csv
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import PurePosixPath
import re
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup, NavigableString
from lxml import etree

from .core import Document, Error, key

MAX_STRUCTURE = 32*1024*1024
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'
CONTENT_TAGS = frozenset(('p','para','simpara','title','subtitle','h','hd','heading',
                          'li','item','line','v','l','caption','blockquote','epigraph','programlisting'))


def name(element):
    return element.tag.rsplit('}',1)[-1] if isinstance(element.tag,str) else ''


def attr(element,key):
    return next((value for field,value in element.attrib.items() if field.rsplit('}',1)[-1]==key),None)


def xml(data):
    try:
        return etree.fromstring(data,etree.XMLParser(load_dtd=False,no_network=True,resolve_entities=False))
    except etree.XMLSyntaxError as exc:
        raise Error('source_xml', 'The selected structured document has unreadable XML.') from exc


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
        if not (text or issue or loc and loc.get('inline_semantics')):
            return
        ordinal = len(self.doc.blocks)+1
        if role=='heading' and text:
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

    def table(self,rows,where,*,headers=None,issue=None):
        text = '\n'.join(' | '.join(cell for cell in row) for row in rows)
        if not text.strip(' |\n') and not issue:
            return
        return self.add(text,'table',where,loc={'rows':rows,'table_header_cells': [
            c for c in range(len(rows[0])) if 0 in headers] if headers is not None else None},issue=issue)


def html_read(data,builder):
    from .epub_semantics import AUDIT_SPANS, hidden, note_container, uncertainty
    from .importers import html_text
    from .epub_semantics import describe as semantics

    soup = BeautifulSoup(data,'html.parser')
    for node in soup.find_all(['script','style','template','noscript']):
        node.decompose()
    if soup.title and soup.title.get_text(strip=True):
        builder.doc.title = soup.title.get_text(' ',strip=True)
    if soup.html and soup.html.get('lang'):
        builder.doc.language = soup.html['lang']
    if soup.find('link',rel='stylesheet'):
        builder.doc.warnings.append('External stylesheet retained as a reference. Its remote contents were not fetched.')
    body = soup.body or soup
    note_ids = {node['id']:node for node in body.find_all(id=True) if note_container(node)}
    inserted_notes = {}
    atomic = {'p','h1','h2','h3','h4','h5','h6','table','pre','img','svg','math','figcaption','dt','dd','li',
              'blockquote','address','caption'}

    def text_node(node,role,where):
        source,spans = semantics(node)
        spans = [span for span in spans if span['kind'] not in AUDIT_SPANS]
        links = []
        for span in spans:
            if span['kind']!='note_reference':
                continue
            href = span.get('href') or ''
            target = note_ids.get(href.removeprefix('#')) if href.startswith('#') else None
            if target is None:
                links.append({'kind':'note','href':href,'source_start':span['source_start'],'text':'',
                              'issue':'The cited note is unavailable in this HTML source.'})
                continue
            contents = target.get_text(' ',strip=True)
            links.append({'kind':'note','href':href,'source_start':span['source_start'],'text':contents})
        block = builder.add(source,role,where,loc={'anchor':node.get('id'),'inline_semantics':spans,
                            'exact':str(node)},links=links,issue=uncertainty(spans))
        if block and node.get('id'):
            inserted_notes[node['id']] = block.id

    def visit(node,path):
        if isinstance(node,NavigableString):
            from .epub_semantics import NON_TEXT
            if not isinstance(node,NON_TEXT):
                builder.add(str(node),where=path)
            return
        if hidden(node) or node.name=='nav':
            builder.add(node.get_text(' ',strip=True),'hidden',path)
            return
        if node.get('id') in note_ids:
            text_node(node,'note',path)
            return
        if node.name=='table':
            rows = [[html_text(cell).strip() for cell in line.find_all(['th','td'],recursive=False)]
                    for line in node.find_all('tr') if line.find_parent('table') is node]
            head = next((r for r in node.find_all('tr') if r.find_parent('table') is node),None)
            headers = [0] if head and head.find('th',recursive=False) else []
            issue = ('Complex table spans or nesting need a verified reading.' if
                     node.find('table') or node.find(attrs={'rowspan':True}) or node.find(attrs={'colspan':True}) else None)
            builder.table(rows,path,headers=headers,issue=issue)
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

    visit(body,'html')
    for block in builder.doc.blocks:
        for link in block.links:
            if link['href'].startswith('#') and link['href'][1:] in inserted_notes:
                link['destinations'] = ['source.html#'+inserted_notes[link['href'][1:]]]


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


def semantic_xml(root,builder,part):
    fmt = builder.doc.format
    title_node = next((item for item in root.iter() if name(item) in ('book-title','article-title','doctitle')),None)
    if title_node is not None and plain_xml_text(title_node):
        builder.doc.title = plain_xml_text(title_node)
    if language:=root.get(XML_LANG):
        builder.doc.language = language
    if fmt in ('fb2','fb3'):
        children = [n for n in root if name(n) in ('body','fb3-body')] or [root]
    elif name(root) in ('TEI','teiCorpus'):
        children = [n for n in root if name(n) in ('text','TEI')]
    elif name(root) in ('article','article-set'):
        children = [n for n in root if name(n) in ('front','body','back','article')]
    else:
        children = [root]
    notes = {n.get('id'):n for n in root.iter() if name(n) in ('fn','note') and n.get('id')}

    def inline(node):
        from .epub_semantics import uncertainty
        clone = BeautifulSoup(etree.tostring(node,encoding='unicode',with_tail=False),'html.parser')
        for item in clone.find_all(lambda n: n.name and ':' in n.name):
            item.name = item.name.rsplit(':',1)[-1]
        from .epub_semantics import describe
        source,spans = describe(clone)
        return source,spans,uncertainty(spans)

    def visit(node):
        tag = name(node)
        where = part+':'+node.getroottree().getpath(node)
        if tag in ('description','binary','style','script','teiHeader','article-meta','journal-meta','bookinfo'):
            return
        if tag in ('graphic','image','inline-graphic','formula','disp-formula','inline-formula','equation'):
            text = attr(node,'alt') or attr(node,'alttext')
            if not text:
                alternative = next((n for n in node if name(n) in ('alt-text','math')),None)
                if alternative is not None:
                    text = plain_xml_text(alternative)
            if text and tag in ('graphic','image','inline-graphic'):
                builder.add(text,'caption',where)
            else:
                builder.issue('An embedded visual or formula needs a verified reading.',where)
            return
        if tag in ('table','informaltable'):
            rows = [[plain_xml_text(cell) for cell in tr if name(cell) in ('td','th','entry','cell')]
                    for tr in node.iter() if name(tr) in ('tr','row')]
            issue = ('Merged or nested cells need a verified reading.' if any(
                any(attr(x,k) for k in ('rowspan','colspan','morerows','namest','nameend'))
                for x in node.iter()) else None)
            builder.table(rows,where,headers=[] if not any(name(x)=='th' for x in node.iter()) else [0],issue=issue)
            return
        if tag in ('note','fn') and node.get('id'):
            builder.add(plain_xml_text(node),'note',where,loc={'anchor':node.get('id')})
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
                        links.append({'kind':'note','href':'#'+ref,'text':plain_xml_text(notes[ref]),'source_start':start})
                    else:
                        issue = 'Could not establish the exact position of an authored note.'
            role = ('heading' if tag in ('title','subtitle','hd','h','heading') else
                    'caption' if tag=='caption' else 'code' if tag=='programlisting' else 'body')
            if any(name(n) in ('graphic','image','inline-formula','equation') for n in node.iterdescendants()):
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


def docx(path,builder):
    from .epub_semantics import uncertainty

    with structured_zip(path) as (read,members):
        document = xml(read('word/document.xml'))
        meta = read('docProps/core.xml',required=False)
        if meta:
            title = next((plain_xml_text(item) for item in xml(meta) if name(item)=='title'),None)
            if title:
                builder.doc.title = title
        pending_notes = {}
        for part in ('footnotes','endnotes'):
            data = read('word/'+part+'.xml',required=False)
            if data:
                for note in xml(data):
                    ident = attr(note,'id')
                    if ident and ident not in ('-1','0'):
                        pending_notes[part[:-1]+':'+ident] = note

        def paragraph(node, *, reading_note=False):
            pieces,spans,links,excluded,problems = [],[],[],[],[]
            offset = 0

            def add(text):
                nonlocal offset
                pieces.append(text)
                offset += len(text)

            def walk(item):
                tag = name(item)
                if tag in ('del','moveFrom'):
                    excluded.append({'kind':tag,'text':plain_xml_text(item)})
                    return
                if tag=='r':
                    properties = next((n for n in item if name(n)=='rPr'),())
                    if any(name(n) in ('vanish','webHidden') and attr(n,'val') not in ('0','false','off') for n in properties):
                        excluded.append({'kind':'hidden','text':plain_xml_text(item)})
                        return
                    start = offset
                    for child in item:
                        if name(child)!='rPr':
                            walk(child)
                    if any(name(n) in ('i','b') and attr(n,'val') not in ('0','off','false') for n in properties):
                        spans.append({'kind':'emphasis','source_start':start,'source_end':offset})
                    if align:=next((attr(n,'val') for n in properties if name(n)=='vertAlign'),None):
                        if align in ('superscript','subscript') and start<offset:
                            spans.append({'kind':align,'source_start':start,'source_end':offset})
                    return
                if tag in ('drawing','pict','object','txbxContent','oMath','oMathPara','altChunk'):
                    label = next((attr(n,'descr') for n in item.iter() if attr(n,'descr')),None)
                    if label:
                        spans.append({'kind':'object_label','source_start':offset,'source_end':offset,
                                      'reading':label})
                    else:
                        problems.append(f'Word {tag} needs a verified reading.')
                    return
                if tag in ('footnoteReference','endnoteReference') and not reading_note:
                    ident = tag.removesuffix('Reference')+':'+str(attr(item,'id'))
                    href = '#'+ident
                    spans.append({'kind':'note_reference','source_start':offset,'source_end':offset,'href':href})
                    note = pending_notes.get(ident)
                    data = (' '.join(paragraph(p,reading_note=True)[0] for p in note if name(p)=='p')
                            if note is not None else '')
                    link = {'kind':'note','href':href,'text':data,'source_start':offset}
                    if not data:
                        link['issue']='A Word note reference has no readable definition.'
                    links.append(link)
                    return
                if tag=='t':
                    add(item.text or '')
                    return
                if tag in ('tab','br','cr'):
                    add('\t' if tag=='tab' else '\n')
                    return
                if tag in ('pPr','instrText','delText','fldChar','bookmarkStart','bookmarkEnd'):
                    return
                for child in item:
                    walk(child)
            walk(node)
            untrimmed = ''.join(pieces)
            left = len(untrimmed)-len(untrimmed.lstrip())
            text = untrimmed.strip()
            for span in spans:
                span['source_start'] = min(len(text),max(0,span['source_start']-left))
                span['source_end'] = min(len(text),max(0,span['source_end']-left))
                span['source'] = text[span['source_start']:span['source_end']]
            for link in links:
                link['source_start'] = min(len(text),max(0,link['source_start']-left))
            issue = ' '.join(dict.fromkeys(problems)) or uncertainty(spans)
            return text,spans,links,excluded,issue

        def visit(node):
            tag = name(node)
            loc = 'word/document.xml:'+node.getroottree().getpath(node)
            if tag=='p':
                text,spans,links,excluded,issue = paragraph(node)
                properties = next((n for n in node if name(n)=='pPr'),())
                style = next((attr(n,'val') for n in properties if name(n)=='pStyle'),None) or ''
                role = 'heading' if style.lower().startswith(('heading','title')) or any(
                    name(n)=='outlineLvl' for n in properties) else 'body'
                if excluded:
                    builder.doc.inventory.append({'location':loc,'selected_edition':'visible final',
                                                  'excluded_runs':excluded})
                builder.add(text,role,loc,loc={'inline_semantics':spans},links=links,issue=issue)
            elif tag=='tbl':
                rows = []
                heads = []
                issue = None
                if any(name(child) in ('gridSpan','vMerge','drawing','oMath','footnoteReference','endnoteReference')
                       for child in node.iter()) or any(name(child)=='tbl' for child in node.iterdescendants()):
                    issue = 'Merged cells, nested tables, notes, or visual content need a verified table reading.'
                for row in (n for n in node if name(n)=='tr'):
                    if any(name(n)=='tblHeader' for n in row.iter()):
                        heads.append(len(rows))
                    rows.append([' '.join(paragraph(p)[0] for p in cell if name(p)=='p')
                                 for cell in row if name(cell)=='tc'])
                builder.table(rows,loc,headers=heads,issue=issue)
            elif tag in ('altChunk','object'):
                builder.issue('The Word body refers to embedded content that must be reviewed.',loc)
            elif tag not in ('del','moveFrom','sectPr'):
                for child in node:
                    visit(child)
        visit(document)
        if any(n.startswith(('word/header','word/footer')) for n in members):
            builder.doc.warnings.append('Page header and footer parts remain in the portable Word document.')


def odt(path,builder):
    with structured_zip(path) as (read,_):
        document = xml(read('content.xml'))
        meta = read('meta.xml',required=False)
        if meta:
            title = next((plain_xml_text(n) for n in xml(meta).iter() if name(n)=='title'),None)
            if title:
                builder.doc.title = title
        body = next((n for n in document.iter() if name(n)=='body'),None)
        if body is None:
            raise Error('source_xml','OpenDocument has no document body.')
        if any(name(n)=='changed-region' for n in body.iter()):
            builder.doc.warnings.append('Tracked change records are preserved in the original. The visible body is selected.')

        def paragraph(node):
            pieces,spans,links,findings = [],[],[],[]
            offset = 0

            def add(text):
                nonlocal offset
                if text:
                    pieces.append(text)
                    offset += len(text)

            def walk(item):
                tag = name(item)
                if tag in ('tracked-changes','deletion','annotation'):
                    return
                if tag=='note':
                    locator = f'#odt-note-{offset}'
                    text_node = next((n for n in item if name(n)=='note-body'),None)
                    text = plain_xml_text(text_node) if text_node is not None else ''
                    spans.append({'kind':'note_reference','source_start':offset,'source_end':offset,
                                  'source':'','href':locator})
                    links.append({'kind':'note','href':locator,'source_start':offset,'text':text,
                                  **({'issue':'OpenDocument note has no body.'} if not text else {})})
                    return
                if tag in ('s','tab','line-break'):
                    count = int(attr(item,'c') or '1')
                    if not 0<=count<=4096:
                        raise Error('source_structure_size','OpenDocument space repetition exceeds its reading bound.')
                    add(' '*count if tag=='s' else '\t' if tag=='tab' else '\n')
                    return
                if tag in ('image','object','object-ole','frame'):
                    label = next((plain_xml_text(n) for n in item.iter() if name(n) in ('desc','title')),None)
                    if label:
                        spans.append({'kind':'object_label','source_start':offset,'source_end':offset,
                                      'source':'','reading':label})
                    else:
                        findings.append('OpenDocument image, text frame, or embedded object needs a verified reading.')
                    return
                add(item.text)
                for child in item:
                    walk(child)
                    add(child.tail)
            walk(node)
            raw = ''.join(pieces)
            left = len(raw)-len(raw.lstrip())
            text = raw.strip()
            for item in [*spans,*links]:
                for part in ('source_start','source_end'):
                    if part in item:
                        item[part] = min(len(text),max(0,item[part]-left))
            return text,spans,links,' '.join(dict.fromkeys(findings)) or None

        def visit(node):
            tag = name(node)
            loc = 'content.xml:'+node.getroottree().getpath(node)
            if tag in ('p','h'):
                text,spans,links,issue = paragraph(node)
                builder.add(text,'heading' if tag=='h' else 'body',loc,
                            loc={'inline_semantics':spans},links=links,issue=issue)
            elif tag=='table':
                rows,headers = [],[]
                issue = None
                for row in (n for n in node.iter() if name(n)=='table-row'):
                    repetition = int(attr(row,'number-rows-repeated') or 1)
                    if not 0<repetition<=1000:
                        raise Error('source_structure_size','OpenDocument row repetition exceeds its reading bound.')
                    cells = []
                    for cell in row:
                        if name(cell) not in ('table-cell','covered-table-cell'):
                            continue
                        repeats = int(attr(cell,'number-columns-repeated') or 1)
                        if not 0<repeats<=1000:
                            raise Error('source_structure_size','OpenDocument cell repetition exceeds its reading bound.')
                        values = [paragraph(p) for p in cell if name(p) in ('p','h')]
                        if any(v[2] or v[3] for v in values) or name(cell)=='covered-table-cell':
                            issue = 'Merged cells or embedded content need a verified table reading.'
                        cells.extend([' '.join(v[0] for v in values)]*repeats)
                    if any(name(parent)=='table-header-rows' for parent in row.iterancestors()):
                        headers.extend(range(len(rows),len(rows)+repetition))
                    rows.extend([cells]*repetition)
                    if len(rows)>10000 or len(cells)>2000:
                        raise Error('source_structure_size','OpenDocument table exceeds the narration budget.')
                builder.table(rows,loc,headers=headers,issue=issue)
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
        docx(path,builder)
    elif route=='odt':
        odt(path,builder)
    elif route in ('xlsx','pptx'):
        from .structured_intake import read_pptx, read_xlsx
        (read_xlsx if route=='xlsx' else read_pptx)(path,builder)
    elif route=='fb3':
        with structured_zip(path) as (read,index):
            candidates = [n for n in index if n.endswith('fb3-body.xml')]
            if len(candidates)!=1:
                raise Error('source_selection','FB3 requires one authored body part.')
            body, = candidates
            semantic_xml(xml(read(body)),builder,body)
    elif route=='mobi':
        from .intake_helpers import resident_mobi
        return resident_mobi(path,source,sha,cache)
    elif route=='legacy-ebook':
        from .intake_helpers import legacy_ebook
        return legacy_ebook(path,source,sha,cache)
    elif route=='office-legacy':
        from .intake_helpers import legacy_document
        return legacy_document(path,source,builder,cache)
    else:
        data = bounded_read(path)
        if route=='html':
            html_read(data,builder)
        elif route in ('xml-book','fb2'):
            root = xml(data)
            if name(root) not in ('FictionBook','book','article','article-set','TEI','teiCorpus','dtbook',
                                  'abiword','section','reference','refentry'):
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
            from .importers import markdown
            loaded = json.loads(data)
            if loaded.get('nbformat')!=4:
                raise Error('unsupported_format','This notebook version is not supported.')
            for index,cell in enumerate(loaded['cells'],1):
                text = ''.join(cell['source'])
                cell_type = cell.get('cell_type')
                if cell_type=='markdown':
                    for item in markdown(text.encode()).blocks:
                        builder.add(item.source,item.role,f'cell {index}',loc={
                            k:v for k,v in item.locator.items() if k in ('inline_semantics','exact','rows')},
                            issue=item.issue,links=item.links)
                elif cell_type=='code':
                    builder.add(text,'code',f'cell {index}')
                else:
                    builder.add(text,where=f'cell {index}')
                if cell.get('outputs'):
                    builder.doc.inventory.append({'cell':index,'status':'authored outputs retained in original'})
        elif route=='outline':
            from .structured_intake import read_outline
            read_outline(data,builder)
        elif route=='subtitles':
            from .intake_helpers import subtitles
            subtitles(data,builder)
        else:
            raise Error('unsupported_format', f'No reader is registered for {route}.')
    return builder.doc
