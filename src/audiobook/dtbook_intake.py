"""Project DTBook semantics into Hamr's shared structured-text reader."""

import re

from lxml import etree

from .core import Error
from .document_intake import XML_LANG, html_read, name
from .source_math import MATHML

NAMESPACE = 'http://www.daisy.org/z3986/2005/dtbook/'
HEADINGS = frozenset(f'h{level}' for level in range(1,7))
SECTIONS = frozenset(('level',*(f'level{level}' for level in range(1,7))))
HTML = frozenset('a abbr acronym address blockquote br cite code dd dfn div dl dt em img kbd li p pre q samp '
                 'span strong sub sup table tbody td tfoot th thead tr caption'.split())
PARAGRAPHS = frozenset(('author','bridgehead','byline','covertitle','dateline','docauthor','hd','levelhd','line'))
BLOCKS = frozenset(('bodymatter','epigraph','frontmatter','linegroup','notice','poem','rearmatter'))
ATTRIBUTES = ('alt','abbr','class','colspan','headers','href','id','rowspan','scope','src','title')
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
LAYOUT_SPACE = re.compile(r'[ \t\r\n]+')


def is_dtbook(node):
    return node.tag in ('dtbook','{'+NAMESPACE+'}dtbook')


def read_dtbook(root,builder,part,*,collect_resources=False):
    if not is_dtbook(root):
        raise Error('source_dtbook','The selected book does not contain a supported DTBook root.')
    namespace = etree.QName(root).namespace
    book = next((node for node in root if node.tag in ('book','{'+NAMESPACE+'}book')),None)
    if book is None:
        raise Error('source_dtbook','The DTBook has no book body.')
    if language:=root.get(XML_LANG):
        builder.doc.language = language
    if title:=next((' '.join(''.join(node.itertext()).split()) for node in book.iter()
                    if name(node)=='doctitle'),None):
        builder.doc.title = title
    front = next((node for node in book if name(node)=='frontmatter'),None)
    repeat_in_front = front is not None and any(' '.join(''.join(node.itertext()).split())==title
                                                for node in front.iter() if name(node) in HEADINGS)
    only_title = front is not None and all(name(node) in ('','doctitle') for node in front)
    first_heading = next((node for node in book.iter() if name(node) in HEADINGS),None)
    repeated_title = title and (repeat_in_front or only_title and first_heading is not None and
                                ' '.join(''.join(first_heading.itertext()).split())==title)
    authors = [' '.join(''.join(node.itertext()).split()) for node in book.iter() if name(node)=='docauthor']
    if any(authors):
        builder.doc.metadata['authors'] = [author for author in authors if author]

    annotations = {}
    tree = root.getroottree()

    def project(node,*,depth=0,front=False,inset=False,literal=False):
        tag = etree.QName(node)
        if tag.namespace==MATHML:
            item = etree.Element(tag.localname,attrib=node.attrib)
        elif tag.namespace!=namespace:
            return None
        else:
            original = tag.localname
            classes = set(node.get('class','').casefold().split())
            front = front or original=='frontmatter'
            inset = inset or original in ('sidebar','note','annotation','prodnote','list')
            depth += original in SECTIONS
            if original in ('pagenum','lic') and (original=='pagenum' or 'pagenum' in classes):
                item = etree.Element('span',{'role':'doc-pagebreak','aria-hidden':'true'})
            elif original in SECTIONS or original in BLOCKS or original in ('book','sidebar','prodnote'):
                nav = original in SECTIONS and front and bool(classes & {'contents','toc'})
                item = etree.Element('nav' if nav else 'div')
            elif original in ('note','annotation'):
                item = etree.Element('aside',{'role':'doc-footnote'})
            elif original in ('noteref','annoref'):
                item = etree.Element('a',{'role':'doc-noteref'})
                if target:=node.get('idref'):
                    item.set('href',target if '#' in target else '#'+target)
            elif original in HEADINGS or original in ('doctitle','hd','levelhd','bridgehead'):
                heading = original if original in HEADINGS else f'h{min(max(depth,1),6)}'
                item = etree.Element('p' if inset else heading)
                if original=='doctitle' and repeated_title:
                    item.set('aria-hidden','true')
            elif original=='list':
                item = etree.Element('ol' if node.get('type')=='ol' else 'ul')
            elif original=='imggroup':
                item = etree.Element('figure')
            elif original=='caption' and name(node.getparent())=='imggroup':
                item = etree.Element('figcaption')
            else:
                item = etree.Element(original if original in HTML else 'p' if original in PARAGRAPHS else 'span')
            for field in ATTRIBUTES:
                if value:=node.get(field):
                    item.set(field,value)
            if original=='img' and 'alt' in node.attrib:
                item.set('alt',node.get('alt'))
            if language:=node.get(XML_LANG):
                item.set('lang',language)
        literal = node.get(XML_SPACE)=='preserve' or tag.localname=='pre' or literal and node.get(XML_SPACE)!='default'
        label = str(len(annotations)+1)
        annotations[label] = {'source_file':part,'xml_path':tree.getpath(node),'lines':[node.sourceline,node.sourceline]}
        item.set('data-hamr-node',label)
        def layout(text):
            return text if literal or text is None else LAYOUT_SPACE.sub(' ',text)

        item.text = layout(node.text)
        for child in node:
            if isinstance(child,etree._Entity):
                raise Error('source_dtbook',f'An unresolved XML entity occurs in {part} at line {child.sourceline}.',
                            'Export the text with expanded entities and open it again.')
            generated = project(child,depth=depth,front=front,inset=inset,literal=literal) if isinstance(child.tag,str) else None
            if generated is not None:
                item.append(generated)
                generated.tail = layout(child.tail)
            elif child.tail:
                if len(item):
                    item[-1].tail = (item[-1].tail or '')+layout(child.tail)
                else:
                    item.text = (item.text or '')+layout(child.tail)
        return item

    body = etree.Element('body')
    body.append(project(book))
    html_read(etree.tostring(body,encoding='unicode',method='html'),builder,where=part,
              collect_resources=collect_resources,source_annotations=annotations)
