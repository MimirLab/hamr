"""Prepare resident AsciiDoc output in the shared structured reading pipeline."""

from collections import defaultdict
import re

from bs4 import BeautifulSoup

from .asciidoc_engine import parse, source_path
from .core import Error
from .document_intake import DocumentBuilder, html_read


DEPENDENCIES = re.compile(r'^include::',re.M)
ASC_AUTHORING = re.compile(
    rb'(?m)^(?:include::[^\r\n\[\]]+\[[^\r\n\]]*\]|\[\[[A-Za-z_][\w.:\-]*\]\]\r?\n={2,5} \S[^\r\n]*)\r?$')
ASC_TITLE = re.compile(rb'(?m)^= [^\r\n]+\r?\n:(?:doctype|toc|sectnums|icons|imagesdir|lang):')
ASC_META = re.compile(r'(?m)^={1,6}[ \t]+')
LIST_MARK = re.compile(r'^(?:\.{1,5}|\*{1,5}|-|[1-9][0-9]*\.)[ \t]+')
ADMONITION_MARK = re.compile(r'^(?:NOTE|TIP|IMPORTANT|CAUTION|WARNING):[ \t]+')
ADMONITION_STYLE = re.compile(r'^\[(?:NOTE|TIP|IMPORTANT|CAUTION|WARNING)(?:,[^\]]*)?\]$')


def matches_asc(prefix):
    return bool(b'\0' not in prefix and (ASC_AUTHORING.search(prefix) or ASC_TITLE.search(prefix)))


def decode_asciidoc(raw,encoding):
    try:
        return raw.decode(encoding or 'utf-8-sig')
    except (UnicodeError,LookupError) as error:
        raise Error('text_encoding','The AsciiDoc source does not decode with the selected encoding.') from error


class SourcePositions:
    def __init__(self,main,files):
        self.main = main
        self.lines = {name:value.splitlines() for name,value in files.items()}
        self.anchors = {}

    def line_index(self,part):
        if part not in self.anchors:
            lookup = defaultdict(list)
            for line,raw in enumerate(self.lines[part],1):
                lookup[raw.strip()].append(line)
            self.anchors[part] = lookup
        return self.anchors[part]

    def locate(self,node):
        raw_path = node.get('file') or node.get('path')
        try:
            part = source_path(raw_path,self.main)
        except Error:
            part = None
        line = node.get('line')
        if part not in self.lines or not isinstance(line,int):
            return {}
        source = node.get('source')
        lines = self.lines[part]
        loc = {'source_file':part,'reported_line':line,'source_element':part}
        if line<1 or line>len(lines):
            return loc
        loc.update(lines=[line,line],line_verified=False,source_element=f'{part}:{line}')
        raw_line = lines[line-1].strip()
        context = node.get('context')
        if (context=='table' and re.fullmatch(r'[|!:,]={3,}',raw_line) or
                context=='colist' and re.match(r'<(?:\d+|\.)>\s',raw_line) or
                context=='image' and raw_line.startswith('image::')):
            return {**loc,'line_verified':True}
        if context=='admonition' and line>1 and ADMONITION_STYLE.fullmatch(lines[line-2].strip()):
            return {**loc,'lines':[line-1,line-1],'source_element':f'{part}:{line-1}','line_verified':True}
        if not isinstance(source,str) or not source.strip():
            return loc
        sample = source.splitlines()
        raw = [text.strip() for text in sample]
        physical = [text.strip() for text in lines[line-1:line-1+len(raw)]]
        if context=='list_item':
            physical[0] = LIST_MARK.sub('',physical[0])
        elif context=='admonition':
            physical[0] = ADMONITION_MARK.sub('',physical[0])
        if context=='section' and ASC_META.sub('',raw_line)==raw[0]:
            first,last = line,line
        elif raw_line.startswith('include::') and raw[0].startswith('Unresolved directive in '):
            first,last = line,line
        elif physical==raw:
            first,last = line,line+len(raw)-1
        elif len(candidates:=self.line_index(part).get(raw[0],[]))==1:
            first, = candidates
            end = first+len(raw)-1
            last = end if [text.strip() for text in lines[first-1:end]]==raw else first
        else:
            return loc
        return {**loc,'lines':[first,last],'source_element':f'{part}:{first}-{last}','line_verified':True}

    def caption(self,node,location):
        part,line = location.get('source_file'),location.get('reported_line')
        source = node.get('source')
        if part is None or not isinstance(line,int) or not source:
            return None
        lines = self.lines[part]
        if not 1<=line<=len(lines):
            return None
        candidates = [i+1 for i in range(max(0,line-12),line-1) if lines[i].strip()=='.'+source]
        if len(candidates)==1:
            first, = candidates
            return {**location,'lines':[first,first],'source_element':f'{part}:{first}-{first}',
                    'line_verified':True}


def project_notes(soup,annotations):
    definitions = {}
    for definition in soup.select('#footnotes > .footnote[id^="_footnotedef_"]'):
        anchor = definition.find('a',href=re.compile(r'^#_footnoteref_\d+$'))
        if anchor is None:
            continue
        anchor.extract()
        for child in definition.contents:
            if isinstance(child,str) and child.strip():
                child.replace_with(re.sub(r'^\s*\.\s*','',child))
                break
        definition.name = 'aside'
        definition['role'] = 'doc-footnote'
        definitions['#'+definition['id']] = definition
    if definitions:
        container = soup.find(id='footnotes')
        container.unwrap()
    for note in soup.select('sup.footnote'):
        anchor = note.find('a',href=re.compile(r'^#_footnotedef_\d+$'))
        if anchor is None:
            continue
        target = anchor['href']
        anchor['role'] = 'doc-noteref'
        owner = note.find_parent(attrs={'data-hamr-node':True})
        if owner and (loc:=annotations.get(owner['data-hamr-node'])):
            marker = 'note-'+str(len(annotations))
            anchor['data-hamr-node'] = marker
            annotations[marker] = {'inline_location':loc}
            if target in definitions and not definitions[target].has_attr('data-hamr-node'):
                definitions[target]['data-hamr-node'] = 'definition-'+marker
                annotations['definition-'+marker] = loc
        note.replace_with(anchor)


def project_callouts(soup):
    for group in soup.select('div.colist'):
        table = group.find('table',recursive=False)
        if table is None:
            continue
        rows = []
        for row in table.find_all('tr'):
            cells = row.find_all('td',recursive=False)
            if len(cells)!=2:
                break
            label = cells[0].get_text(strip=True).strip('()')
            if not label.isdecimal():
                break
            rows.append((label,cells[1]))
        else:
            if rows:
                replace_callout_table(soup,table,rows)


def replace_callout_table(soup,table,rows):
    listing = soup.new_tag('ol')
    for label,cell in rows:
        item = soup.new_tag('li',attrs={'value':label})
        for child in list(cell.contents):
            item.append(child.extract())
        listing.append(item)
    table.replace_with(listing)


def project_admonitions(soup):
    for admonition in soup.select('div.admonitionblock'):
        table = admonition.find('table',recursive=False)
        row = table.find('tr') if table else None
        if row is None:
            continue
        icon = row.find('td',class_='icon',recursive=False)
        body = row.find('td',class_='content',recursive=False)
        if icon is None or body is None:
            continue
        label = icon.get_text(strip=True)
        if not label and (image:=icon.find('img')):
            label = image.get('alt','')
        admonition.name = 'aside'
        new = []
        if label:
            heading = soup.new_tag('p')
            heading.string = label.rstrip('.')+'.'
            new.append(heading)
        if body.find(['p','div','ul','ol','pre','table','blockquote','dl'],recursive=False):
            new.extend(list(body.contents))
        else:
            paragraph = soup.new_tag('p')
            for child in list(body.contents):
                paragraph.append(child.extract())
            new.append(paragraph)
        table.extract()
        for child in new:
            admonition.append(child.extract())


def project_list_locations(container,items,key,positions,annotations):
    listing = container.find(['ol','ul'],recursive=False)
    daughters = listing.find_all('li',recursive=False) if listing else []
    if len(daughters)!=len(items):
        return
    for index,(tag,item) in enumerate(zip(daughters,items),1):
        if item and (loc:=positions.locate(item)):
            label = f'item-{key}-{index}'
            tag['data-hamr-node'] = label
            annotations[label] = loc


def asciidoc_document(text,name,sha,title,*,include=None,encoding=None):
    def read(current,target):
        if include is None:
            raise Error('asciidoc_include','This include is unavailable in the saved AsciiDoc publication.')
        part,raw = include(current,target)
        return part,decode_asciidoc(raw,encoding)

    result,files,denied = parse(text,name,read)
    positions = SourcePositions(name,files)
    annotations = {str(index):positions.locate(node) for index,node in enumerate(result['nodes'],1)}
    soup = BeautifulSoup(result['markup'],'html.parser')
    project_admonitions(soup)
    project_callouts(soup)
    if toc:=soup.find(id='toc'):
        toc.name = 'nav'
    for rendered in soup.find_all(attrs={'data-hamr-node':True}):
        key = rendered['data-hamr-node']
        if not key.isdecimal() or key not in annotations:
            continue
        node = result['nodes'][int(key)-1]
        if any(line.startswith('Unresolved directive in ') for line in str(node.get('source','')).splitlines()):
            annotations[key]['source_issue'] = 'An AsciiDoc include could not be read in this source.'
        if rendered.name=='table' and (caption:=rendered.find('caption',recursive=False)):
            if loc:=positions.caption(node,annotations[key]):
                label = 'caption-'+key
                caption['data-hamr-node'] = label
                annotations[label] = loc
        if items:=node.get('items'):
            project_list_locations(rendered,items,key,positions,annotations)
    project_notes(soup,annotations)
    supplied_title = BeautifulSoup(result.get('title') or '', 'html.parser').get_text(' ',strip=True)
    builder = DocumentBuilder('asciidoc',supplied_title or title,sha)
    builder.doc.language = result.get('lang') or 'en'
    html_read(str(soup),builder,where='asciidoc',metadata=False,
              source_annotations=annotations,collect_links=True,collect_resources=True)
    for record in result['messages']:
        builder.doc.warnings.append(record['message'])
    if denied:
        builder.doc.metadata['unresolved_asciidoc_targets'] = list(denied.values())
    return builder.doc
