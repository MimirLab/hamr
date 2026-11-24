"""Translate native AbiWord structure into shared document markup."""

import base64
import binascii
import bz2
from collections import defaultdict
import gzip
from pathlib import PurePosixPath
import re
from urllib.parse import quote
from zlib import error as compression_error

from lxml import etree

from .core import Error
from .document_intake import MAX_STRUCTURE, XLINK_HREF, XML_LANG, html_read, name, xml, xml_formula
from .xml_projection import XMLProjection, element_text

AWML = '{http://www.abisource.com/awml.dtd}'
ROOTS = frozenset(('abiword','awml',AWML+'abiword',AWML+'awml'))
HEADING = re.compile(r'(?:Numbered )?Heading ([1-9][0-9]?)',re.I)
NOTE_IDS = {'foot':'footnote-id','endnote':'endnote-id'}
REFERENCES = {'footnote_ref':'foot','endnote_ref':'endnote'}
MARKERS = frozenset(('footnote_anchor','endnote_anchor','annotation','annotation_ref'))
METADATA = {'meta_title':'dc.title','meta_creator':'dc.creator','meta_subject':'dc.subject',
            'meta_description':'dc.description','meta_keywords':'abiword.keywords'}
GEOMETRY = ('top-attach','left-attach','bot-attach','right-attach')


def read_xml(path):
    with path.open('rb') as source:
        signature = source.peek(3)[:3]
    compression = gzip.open if signature.startswith(b'\x1f\x8b') else bz2.open if signature==b'BZh' else open
    try:
        with compression(path,'rb') as source:
            data = source.read(MAX_STRUCTURE+1)
    except (EOFError,OSError,compression_error) as exc:
        raise Error('source_abiword_compression','The compressed AbiWord document is incomplete or unreadable.',
                    'Open another local copy of the original document.') from exc
    if len(data)>MAX_STRUCTURE:
        raise Error('source_structure_size','The decompressed AbiWord document exceeds the structured reader’s memory budget.',
                    'Divide the original into smaller documents and open each separately.')
    return xml(data)


def properties(node):
    attributes = {}
    for item in node.get('props','').split(';'):
        key,separator,value = item.partition(':')
        if separator:
            attributes[key.strip()] = value.strip()
    return attributes


def hidden(node):
    return properties(node).get('display')=='none'


def has_revision(node):
    return bool(node.get('revision'))


class AbiWord(XMLProjection):
    def __init__(self,root,part):
        super().__init__(root,part)
        self.styles = self.named(root,'styles','s','name')
        self.lists = self.named(root,'lists','l','id')
        self.assets = self.named(root,'data','d','name')
        self.metadata = {ident:element_text(nodes[0]) for ident,nodes in
                         self.named(root,'metadata','m','key').items() if len(nodes)==1}
        self.notes = {node:self.note_id(name(node),node.get(NOTE_IDS[name(node)])) or f'abi-note:{index}'
                      for index,node in enumerate(root.iter()) if name(node) in NOTE_IDS}
        self.media = []

    @staticmethod
    def named(root,group,element,field):
        result = defaultdict(list)
        for node in root.findall('{*}'+group+'/{*}'+element):
            if ident:=node.get(field):
                result[ident].append(node)
        return result

    @staticmethod
    def note_id(kind,ident):
        return 'abi-'+kind+':'+ident if ident else None

    def style_chain(self,style):
        visited = set()
        while style and style not in visited:
            visited.add(style)
            yield style
            candidates = self.styles.get(style,())
            style = candidates[0].get('basedon') if len(candidates)==1 else None

    def field(self,node,depth):
        kind = node.get('type','')
        if kind in MARKERS or kind=='list_label' and not element_text(node):
            return self.element(node,'span',attributes={'aria-hidden':'true'})
        if note_kind:=REFERENCES.get(kind):
            target = self.note_id(note_kind,node.get(NOTE_IDS[note_kind]))
            following = node.getnext()
            if following in self.notes and name(following)==note_kind and (not target or target==self.notes[following]):
                return self.element(node,'span',attributes={'aria-hidden':'true'})
            return self.note_anchor(node,target) if target else self.review(node,'An AbiWord note field has no bound note.')
        if value:=self.metadata.get(METADATA.get(kind,'')):
            item = self.element(node,'span')
            item.text = value
            return item
        if element_text(node):
            return self.children(node,self.element(node,'span'),depth=depth)
        return self.review(node,f'The AbiWord field {kind or "without a type"!r} has no saved text.')

    def note_anchor(self,node,target):
        return self.element(node,'a',attributes={'href':'#'+quote(target,safe=''),'role':'doc-noteref'},
                            inline_location=self.location(node))

    def paragraph(self,node,depth):
        list_id = node.get('listid','0')
        evidence = {}
        if list_id not in ('','0'):
            evidence = {'list_id':list_id,'list_level':node.get('level'),'list_parent':node.get('parentid')}
            definitions = self.lists.get(list_id,())
            if len(definitions)==1:
                listing = definitions[0]
                evidence['list_parent'] = evidence['list_parent'] or listing.get('parentid')
                evidence['list_type'] = listing.get('type')
                evidence['list_start'] = listing.get('start-value')
        tag,quote_block = 'p',False
        for style in self.style_chain(node.get('style')):
            if heading:=HEADING.fullmatch(style):
                tag = 'h'+str(min(int(heading[1]),6))
                break
            if style.casefold()=='block text':
                quote_block = True
        result = self.children(node,self.element(node,tag,**evidence),depth=depth)
        if result.find('.//br') is not None:
            self.annotations[result.get('data-hamr-node')]['authored_line_breaks'] = True
        if quote_block and tag=='p':
            wrapper = etree.Element('blockquote')
            wrapper.append(result)
            return wrapper
        return result

    def image(self,node,ident):
        assets = self.assets.get(ident,())
        item = {'kind':'abiword_image',**self.location(node),'data_id':ident,'status':'unresolved'}
        if len(assets)==1:
            item.update(status='retained',asset_location=self.location(assets[0]),
                        media_type=assets[0].get('mime-type'))
        self.media.append(item)
        attrs = {'alt':node.get('alt') or node.get('title') or ''}
        if ident:
            attrs['src'] = 'abiword-data:'+quote(ident,safe='')
        if len(assets)==1 or attrs['alt']:
            return self.element(node,'img',attributes=attrs)
        return self.review(node,'This AbiWord illustration has no readable description or unique embedded original.')

    def formula(self,node):
        assets = self.assets.get(node.get('dataid'),())
        source = assets[0] if len(assets)==1 else None
        location = self.location(node)
        if source is not None and source.get('mime-type')=='application/mathml+xml':
            try:
                raw = (source.text or '').encode()
                if source.get('base64','no')=='yes':
                    raw = base64.b64decode(b''.join(raw.split()),validate=True)
                math = source[0] if len(source)==1 else xml(raw)
            except (Error,binascii.Error):
                math = None
            if math is not None:
                span = xml_formula(math,self.part)
                bound = {**location,'asset_location':self.location(source)}
                return self.element(node,'span',reading_span={**span,'source_locator':bound})
        return self.review(node,'This AbiWord equation has no readable embedded MathML.')

    def table(self,node,depth):
        rows = defaultdict(list)
        for cell in node:
            if name(cell)!='cell':
                continue
            values = properties(cell)
            positions = [values.get(key,'') for key in GEOMETRY]
            if not all(re.fullmatch(r'[0-9]{1,7}',value) for value in positions):
                return self.review(node,'AbiWord table cells have no complete row and column coordinates.')
            row,column,bottom,right = map(int,positions)
            if bottom<=row or right<=column:
                return self.review(node,'An AbiWord table cell has contradictory coordinates.')
            rows[row].append((column,bottom-row,right-column,cell))
        table = self.element(node,'table')
        next_row = 0
        expected_width = None
        complex_table = any(height>1 or width>1 for cells in rows.values() for _,height,width,_ in cells)
        for row,cells in sorted(rows.items()):
            if row!=next_row and not complex_table:
                return self.review(node,'AbiWord table rows are missing from the declared grid.')
            next_row = row+1
            line = etree.SubElement(table,'tr')
            next_column = 0
            for column,height,width,cell in sorted(cells,key=lambda row:row[0]):
                if column<next_column or column!=next_column and not complex_table:
                    return self.review(node,'AbiWord table cells overlap or leave an undeclared gap.')
                next_column = column+width
                attrs = {'rowspan':str(height),'colspan':str(width)}
                line.append(self.children(cell,self.element(cell,'td',attributes=attrs),depth=depth))
            if expected_width is not None and expected_width!=next_column and not complex_table:
                return self.review(node,'AbiWord table rows disagree about the number of columns.')
            expected_width = next_column
        return table

    def project(self,node,*,depth=1,literal=False):
        kind = name(node)
        if kind in ('annotate','annotation','toc') or hidden(node):
            return self.children(node,self.element(node,'span',attributes={'aria-hidden':'true'}),depth=depth)
        if kind=='section' and node.get('type','doc') not in ('','doc'):
            return self.children(node,self.element(node,'div',source_control=True),depth=depth)
        if has_revision(node):
            return self.review(node,'This AbiWord passage contains a tracked revision that needs a verified visible reading.')
        if kind in NOTE_IDS:
            return self.note_anchor(node,self.notes[node])
        if kind in ('field','f'):
            return self.field(node,depth)
        if kind=='p':
            return self.paragraph(node,depth)
        if kind=='table':
            return self.table(node,depth)
        if kind in ('image','i'):
            return self.image(node,node.get('dataid'))
        if kind=='math':
            return self.formula(node)
        if kind=='embed':
            return self.review(node,'This AbiWord embedded object needs a verified reading.')
        if kind=='frame' and (ident:=node.get('strux-image-dataid')):
            return self.image(node,ident)
        if kind in ('pbr','cbr'):
            span = self.element(node,'span',attributes={'role':'doc-pagebreak','aria-hidden':'true'})
            span.tail = ' '
            return span
        if kind=='br':
            return self.element(node,'br')
        if kind=='a':
            href = node.get(XLINK_HREF) or node.get('href')
            return self.children(node,self.element(node,'a',attributes={'href':href} if href else {}),depth=depth)
        if kind=='c':
            script = {'superscript':'sup','subscript':'sub'}.get(properties(node).get('text-position'),'span')
            return self.children(node,self.element(node,script),depth=depth,literal=literal)
        return self.children(node,self.element(node,'div' if kind in ('section','frame','margin') else 'span'),
                             depth=depth,literal=literal)

    def html(self,root):
        view = etree.Element('body')
        for node in root.findall('{*}section'):
            view.append(self.project(node))
        for node,ident in self.notes.items():
            note = self.children(node,self.element(node,'aside',attributes={'role':'doc-footnote','id':ident}),depth=1)
            view.append(note)
        return etree.tostring(view,encoding='unicode',method='html')


def read_abiword(root,builder,part):
    from .source_bundle import source_suffix

    if root.tag not in ROOTS:
        raise Error('source_abiword','The selected document has no AbiWord XML root.')
    edition = AbiWord(root,part)
    doc = builder.doc
    filename = PurePosixPath(part).name
    suffix = source_suffix(filename)
    fallback = filename[:-len(suffix)] if suffix else filename
    doc.title = edition.metadata.get('dc.title') or fallback or doc.title
    doc.language = root.get(XML_LANG) or edition.metadata.get('dc.language') or doc.language
    if author:=edition.metadata.get('dc.creator'):
        doc.metadata['authors'] = [author]
    html_read(edition.html(root),builder,metadata=False,where=part,
              collect_resources=True,source_annotations=edition.annotations)
    doc.inventory.extend(edition.media)
