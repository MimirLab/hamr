"""Resolve TEI editorial readings before shared narration preparation."""

from collections import defaultdict
from urllib.parse import quote, unquote

from lxml import etree

from .core import Error
from .document_intake import FORMULA_ELEMENTS, XML_LANG, html_read, name, xml_formula
from .xml_projection import XML_SPACE, XMLProjection, append_text, element_text, identifier, normal_text

TEI = '{http://www.tei-c.org/ns/1.0}'
PREFERRED = {'orig':'reg','sic':'corr','abbr':'expan'}
BLOCKS = frozenset(('text','front','body','back','div','group','lg','sp','argument','epigraph','opener','closer'))
HTML = {'p':'p','ab':'p','l':'p','item':'li','label':'span','table':'table','row':'tr','quote':'blockquote',
        'q':'q','said':'q','emph':'em','code':'code','eg':'pre','bibl':'p','cit':'blockquote',
        'speaker':'p','stage':'p','trailer':'p','salute':'p','signed':'p','dateline':'p'}
NOTE_SCOPE = frozenset(('p','ab','l','cell','head','item','quote','q','note'))


def is_tei(root):
    return root.tag in ('TEI','teiCorpus',TEI+'TEI',TEI+'teiCorpus')


def asset_links(root):
    namespace = etree.QName(root).namespace
    tags = {f'{{{namespace}}}'+tag if namespace else tag for tag in ('graphic','media')}
    for node in root.iter(*tags):
        if href:=node.get('url'):
            yield node,href


class Edition(XMLProjection):
    def __init__(self,root,part):
        super().__init__(root,part)
        self.root = root
        self.namespace = etree.QName(root).namespace
        self.base = root.find(self.tag('text'))
        if self.base is None:
            raise Error('source_tei_text','This TEI document has no authored text body.')
        self.variants = []
        self.ids = defaultdict(list)
        self.base_elements = set()
        for node in self.base.iter():
            if (name(node) and self.local(node) not in ('app','listApp','standOff')
                    and not any(self.local(parent) in ('app','listApp','standOff') for parent in node.iterancestors())):
                self.base_elements.add(node)
        for node in root.iter():
            if name(node) and (ident:=identifier(node)):
                self.ids[ident].append(node)
        self.definitions = {}
        self.note_order = []
        self.links = {unquote(ref) for node in self.base.iter() if self.local(node) in ('ref','ptr')
                      for ref in node.get('target','').split() if ref.startswith('#')}

    def tag(self,value):
        return f'{{{self.namespace}}}{value}' if self.namespace else value

    def local(self,node):
        if node is None or not name(node):
            return ''
        return name(node) if etree.QName(node).namespace==self.namespace else ''

    def review(self,node,message):
        origin = self.location(node)
        return self.element(node,'span',source_issue=message,
                            reading_span={'kind':'source_review','source':'','issue':message,
                                          'source_locator':origin})

    def readings(self,node):
        for child in node:
            if self.local(child)=='rdgGrp':
                yield from self.readings(child)
            elif self.local(child) in ('lem','rdg'):
                yield child

    def note_id(self,node):
        if ident:=identifier(node):
            return ident
        ident = 'hamr-tei-note-'+str(len(self.definitions)+1)
        while ident in self.ids:
            ident += '-note'
        return ident

    def note_marker(self,node,target):
        item = self.element(node,'a',attributes={'href':'#'+quote(target,safe=''),'role':'doc-noteref'},
                            inline_location=self.location(node))
        item.attrib.pop('id',None)
        return item

    def defined_note(self,node):
        if node not in self.definitions:
            self.definitions[node] = self.note_id(node)
            self.note_order.append(node)
        return self.definitions[node]

    def link(self,node,depth,literal):
        target = node.get('target','')
        refs = self.ids.get(unquote(target[1:]),()) if target.startswith('#') else ()
        note = next((item for item in refs if self.local(item)=='note'),None)
        if note is not None:
            self.defined_note(note)
        if note is not None or target.startswith('#') and node.get('type') in ('note','noteref','footnote'):
            item = self.note_marker(node,unquote(target[1:]))
        else:
            item = self.element(node,'a' if target else 'span',attributes={'href':target} if target else None)
        return self.children(node,item,depth=depth,literal=literal)

    def record_variant(self,node,choices,selected=None,*,stand_off=False):
        record = {'kind':'text_variant','encoding':self.local(node),**self.location(node),
                  'status':'base_text' if stand_off else 'selected' if selected is not None else 'review',
                  'readings':[{**self.location(choice),'element':self.local(choice),'text':element_text(choice),
                               'witnesses':choice.get('wit','').split(),'selected':choice is selected}
                              for choice in choices]}
        if stand_off:
            targets = {}
            for field in ('from','to','loc'):
                if value:=node.get(field):
                    candidates = self.ids.get(unquote(value[1:]),()) if value.startswith('#') else ()
                    exact = [self.location(item) for item in candidates if item in self.base_elements]
                    targets[field] = {'reference':value,'targets':exact}
            record['base_reference'] = targets
        self.variants.append(record)

    def variant(self,node,kind,depth,literal):
        if kind=='app':
            choices = list(self.readings(node))
            preferred = [item for item in choices if self.local(item)=='lem']
        else:
            choices = [item for item in node if self.local(item)]
            names = {self.local(item) for item in choices}
            preferred = [item for item in choices if any(names=={original,form} and self.local(item)==form
                                                          for original,form in PREFERRED.items())]
        selected = preferred[0] if len(preferred)==1 else choices[0] if len(choices)==1 else None
        self.record_variant(node,choices,selected)
        if selected is None:
            return self.review(node,'The TEI passage has competing readings. Supply the intended text for this occurrence.')
        result = self.element(node,'span')
        result.append(self.project(selected,depth=depth,literal=literal))
        return result

    def apparatus(self,node):
        return node.get('from') is not None or node.get('to') is not None or node.get('loc') is not None or any(
            self.local(parent) in ('standOff','listApp') or parent.get('type','').casefold() in ('apparatus','critical-apparatus')
            for parent in node.iterancestors())

    def skip_apparatus(self,node):
        for entry in node.iter(self.tag('app')):
            self.record_variant(entry,list(self.readings(entry)),stand_off=True)

    def figure(self,node,depth):
        result = self.element(node,'figure')
        for caption in node.findall(self.tag('head')):
            result.append(self.children(caption,self.element(caption,'figcaption'),depth=depth))
        label = ' '.join(element_text(desc) for desc in node.findall(self.tag('figDesc')) if element_text(desc))
        graphics = node.findall(self.tag('graphic'))
        if label and len(graphics)!=1:
            description = self.element(node,'p',source_role='caption')
            description.text = label
            result.append(description)
        elif len(graphics)>1:
            marker = self.element(node,'p',source_issue='This TEI figure contains multiple graphics. Verify the intended description.')
            result.append(marker)
        for graphic in graphics:
            if len(graphics)==1:
                result.append(self.image(graphic,label))
        return result

    def image(self,node,label=''):
        href = node.get('url')
        fields = {'alt':label}
        if href:
            fields['src'] = href
        return self.element(node,'img',attributes=fields)

    def project(self,node,*,depth=0,literal=False):
        kind = self.local(node)
        if kind in ('teiHeader','facsimile','listWit'):
            return None
        if kind in ('standOff','listApp') or kind=='div' and node.get('type','').casefold() in ('apparatus','critical-apparatus'):
            self.skip_apparatus(node)
            return None
        if kind=='app' and self.apparatus(node):
            self.record_variant(node,list(self.readings(node)),stand_off=True)
            return None
        if kind in ('choice','app'):
            return self.variant(node,kind,depth,literal)
        if kind=='gap':
            return self.review(node,'The TEI source marks missing text here. Supply a verified reading.')
        if kind=='note':
            ident = self.defined_note(node)
            if '#'+ident not in self.links and any(self.local(parent) in NOTE_SCOPE for parent in node.iterancestors()):
                return self.note_marker(node,ident)
            return None
        if kind in ('ref','ptr'):
            return self.link(node,depth,literal)
        if kind in ('pb','cb'):
            result = self.element(node,'span',attributes={'role':'doc-pagebreak','aria-hidden':'true'})
            result.text = node.get('n')
            return result
        if kind=='lb':
            return self.element(node,'span' if node.get('break')=='no' else 'br')
        if kind=='figure':
            return self.figure(node,depth)
        if kind=='graphic':
            return self.image(node)
        if name(node) in FORMULA_ELEMENTS:
            display = node.get('display')=='block' or kind=='formula' and self.local(node.getparent()) not in NOTE_SCOPE
            evidence = {'source_role':'equation'} if display else {}
            return self.element(node,'p' if display else 'span',reading_span=xml_formula(node,self.part),**evidence)
        literal = node.get(XML_SPACE)=='preserve' or kind=='eg' or literal and node.get(XML_SPACE)!='default'
        depth += kind=='div'
        attrs,evidence = {},{}
        if kind=='head':
            tag = 'p' if self.local(node.getparent()) in ('list','table','note') else f'h{min(max(depth,1),6)}'
        elif kind=='list':
            tag = 'ol' if node.get('type')=='ordered' else 'ul'
        elif kind=='cell':
            tag = 'th' if node.get('role')=='label' or node.getparent().get('role')=='label' else 'td'
            attrs = {target:value for field,target in [('rows','rowspan'),('cols','colspan')]
                     if (value:=node.get(field)) and value.isdecimal() and int(value)>1}
        else:
            tag = HTML.get(kind) or ('div' if kind in BLOCKS else 'span')
        if kind=='l':
            evidence['authored_line_breaks'] = True
        if kind=='bibl' and any(self.local(parent)=='listBibl' for parent in node.iterancestors()):
            evidence['source_role'] = 'reference'
        return self.children(node,self.element(node,tag,attributes=attrs,**evidence),depth=depth,literal=literal)

    def html(self):
        out = etree.Element('body')
        out.append(self.project(self.base))
        for apparatus in self.root.findall(self.tag('standOff')):
            self.skip_apparatus(apparatus)
        # Note bodies can themselves introduce notes. New definitions append
        # to this order while each original node is visited only once.
        for node in self.note_order:
            result = self.element(node,'aside',attributes={'role':'doc-footnote'})
            result.set('id',self.definitions[node])
            out.append(self.children(node,result,depth=0))
        return etree.tostring(out,encoding='unicode',method='html')


def read_tei(root,builder,part,*,collect_resources=False):
    books = [root] if name(root)=='TEI' else [child for child in root if is_tei(child) and name(child)=='TEI']
    if not books:
        raise Error('source_tei_text','The TEI corpus has no authored text documents.')
    namespace = etree.QName(root).namespace
    from_root = {'t':namespace} if namespace else {}
    prefix = 't:' if namespace else ''
    title_stmt = root.find('/'.join(prefix+step for step in ('teiHeader','fileDesc','titleStmt')),from_root)
    doc = builder.doc
    if title_stmt is not None:
        titles = title_stmt.findall(prefix+'title',from_root)
        primary = [title for title in titles if title.get('type')=='main']
        if preferred:=next((element_text(title) for title in primary or titles if element_text(title)),None):
            doc.title = preferred
        if authors:=[element_text(author) for author in title_stmt.findall(prefix+'author',from_root) if element_text(author)]:
            doc.metadata['authors'] = authors
    if lang:=root.get(XML_LANG):
        doc.language = lang
    for book in books:
        edition = Edition(book,part)
        html_read(edition.html(),builder,metadata=False,where=part,collect_resources=collect_resources,
                  source_annotations=edition.annotations)
        doc.inventory.extend(edition.variants)
