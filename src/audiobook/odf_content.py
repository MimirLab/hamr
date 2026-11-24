"""Prepare OpenDocument paragraphs and tables for the common planner."""

from urllib.parse import unquote, urlsplit

from .core import Error, key
from .document_intake import attr, formula_note, formula_span, join_formulas, name, plain_xml_text, xml
from .epub_semantics import uncertainty
from .source_bundle import safe_name
from .source_math import MATHML, mathml, mathml_text
from .table_grid import Grid, count, join_passages


OFFICE = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
DRAW = '{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}'
TEXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
TABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
MANIFEST = '{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}'
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
MATH_ROOT = '{'+MATHML+'}math'
TEXT_MEDIA = frozenset((
    'application/vnd.oasis.opendocument.text',
    'application/vnd.oasis.opendocument.text-template',
    'application/vnd.oasis.opendocument.text-web',
))


def flat_text(root):
    return (root is not None and root.tag==OFFICE+'document'
            and root.get(OFFICE+'mimetype') in TEXT_MEDIA)


def table_rows(node,header=False):
    for child in node:
        tag = name(child)
        if tag=='table-row':
            yield child,header
        elif tag in ('table-row-group','table-rows','table-header-rows'):
            yield from table_rows(child,header or tag=='table-header-rows')

def table_content(node):
    for child in node:
        kind = name(child)
        if kind in ('list','list-item','list-header','numbered-paragraph','section'):
            yield from table_content(child)
        elif kind in ('table','p','h','frame','image','object','object-ole') or child.tag==MATH_ROOT:
            yield child


class Content:
    def __init__(self,read,source_file='content.xml'):
        self.read = read
        self.source_file = source_file
        self.declared_objects = None

    def object_formula(self,frame):
        if frame.tag!=DRAW+'frame' or frame.get(TEXT+'anchor-type')!='as-char':
            return None
        objects = [child for child in frame if child.tag==DRAW+'object']
        if len(objects)!=1:
            return None
        inline = objects[0].findall(MATH_ROOT)
        if len(inline)==1 and not objects[0].get(XLINK_HREF):
            return inline[0],self.source_file
        if self.read is None:
            return None
        href = objects[0].get(XLINK_HREF,'')
        try:
            reference = urlsplit(href)
            if reference.scheme or reference.netloc or reference.query or reference.fragment:
                return None
            directory = safe_name(unquote(reference.path).removeprefix('./').removesuffix('/'))
        except (ValueError,Error):
            return None
        if self.declared_objects is None:
            self.declared_objects = {}
            if manifest := self.read('META-INF/manifest.xml',required=False):
                for entry in xml(manifest):
                    if entry.tag==MANIFEST+'file-entry':
                        member = entry.get(MANIFEST+'full-path')
                        self.declared_objects[member] = (entry.get(MANIFEST+'media-type')
                                                    if member not in self.declared_objects else None)
        if self.declared_objects.get(directory+'/')!='application/vnd.oasis.opendocument.formula':
            return None
        member = directory+'/content.xml'
        if body := self.read(member,required=False):
            root = xml(body)
            if root.tag==MATH_ROOT:
                return root,member
        raise Error('source_formula','The OpenDocument formula has no readable bound MathML member.')

    def paragraph(self,node, *, in_note=False):
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
                if in_note:
                    findings.append('A nested OpenDocument note needs a verified reading.')
                    return
                locator = '#odt-note-'+key(item.getroottree().getpath(item))[:16]
                text_node = next((n for n in item if name(n)=='note-body'),None)
                paragraphs = [self.paragraph(p,in_note=True) for p in text_node if name(p) in ('p','h')] if text_node is not None else []
                text,parts = join_formulas((p[0],p[1]) for p in paragraphs)
                problem = next((p[3] for p in paragraphs if p[3]),None)
                if not parts and text_node is not None:
                    text = plain_xml_text(text_node)
                spans.append({'kind':'note_reference','source_start':offset,'source_end':offset,
                              'source':'','href':locator})
                link = formula_note({'kind':'note','href':locator,'reference_href':locator,'source_start':offset,
                                     'source_locator':{'source_file':self.source_file,'xml_path':item.getroottree().getpath(item)}},
                                    text,parts,problem or ('OpenDocument note has no body.' if not text else None))
                link.setdefault('reading',text)
                links.append(link)
                return
            if tag in ('s','tab','line-break'):
                count = int(attr(item,'c') or '1')
                if not 0<=count<=4096:
                    raise Error('source_structure_size','OpenDocument space repetition exceeds its reading bound.')
                start = offset
                add(' '*count if tag=='s' else '\t' if tag=='tab' else '\n')
                if tag=='line-break':
                    spans.append({'kind':'line_break','source_start':start,'source_end':offset})
                return
            is_visual = tag in ('image','object','object-ole','frame')
            formula = (item,self.source_file) if item.tag==MATH_ROOT else None
            if tag=='frame':
                try:
                    formula = self.object_formula(item)
                except Error as error:
                    findings.append(str(error))
                    return
            if formula:
                equation,member = formula
                raw = mathml_text(equation)
                locator = member+':'+equation.getroottree().getpath(equation)
                spans.append(formula_span(raw,lambda:mathml(equation),offset,'OpenDocument equation',locator))
                add(raw)
                return
            if is_visual:
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
        for span in spans:
            span['source'] = text[span['source_start']:span['source_end']]
        return text,spans,links,' '.join(dict.fromkeys(findings)) or uncertainty(spans)

    def table(self,node,*,typed=False):
        grid = Grid()
        for row,header in table_rows(node):
            repeats = count(attr(row,'number-rows-repeated'),maximum=1000)
            authored = []
            for cell in row:
                tag = name(cell)
                if tag not in ('table-cell','covered-table-cell'):
                    continue
                loc = {'source_file':self.source_file,'xml_path':cell.getroottree().getpath(cell)}
                if typed and (kind:=cell.get(OFFICE+'value-type')):
                    loc.update(office_type=kind,office_value=cell.get(OFFICE+'value',''))
                copies = count(attr(cell,'number-columns-repeated'),maximum=1000)
                columns = count(attr(cell,'number-columns-spanned'))
                rows = count(attr(cell,'number-rows-spanned'),maximum=10000)
                parts, paragraphs, locations = [],[],[]

                def flush():
                    part = join_passages(paragraphs)
                    if part['source'] or part['spans']:
                        part['location'] = locations[0] if len(locations)==1 else loc
                        if len(locations)>1:
                            part['source_members'] = locations.copy()
                        parts.append(part)
                    paragraphs.clear()
                    locations.clear()

                if tag=='table-cell':
                    for child in table_content(cell):
                        if name(child)=='table':
                            flush()
                            parts.append({'table':self.table(child)})
                        else:
                            source,spans,links,issue = self.paragraph(child)
                            if issue:
                                grid.issue(issue)
                            if source or spans:
                                paragraphs.append((source,spans,links))
                                locations.append({'source_file':self.source_file,'xml_path':child.getroottree().getpath(child)})
                    flush()
                authored.append((tag,loc,copies,columns,rows,parts))
            for row_repeat in range(repeats):
                grid.begin_row()
                for tag,loc,copies,columns,rows,parts in authored:
                    for cell_repeat in range(copies):
                        location = {**loc,'row_repeat':row_repeat,'cell_repeat':cell_repeat}
                        start = grid.column
                        if tag=='covered-table-cell':
                            grid.cover(location)
                        else:
                            grid.place(parts,location,columns=columns,rows=rows,role='column' if header else 'data')
                            # ODF serializes covered cells explicitly.
                            grid.column = start+1
                grid.end_row()
        return grid.finish()
