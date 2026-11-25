"""Prepare OpenDocument paragraphs and tables for the common planner."""

from collections import Counter
from dataclasses import dataclass, replace
import re
from urllib.parse import unquote, urlsplit

from .core import Error, key
from .document_intake import attr, formula_note, formula_span, join_formulas, name, plain_xml_text, xml
from .epub_semantics import uncertainty
from .source_bundle import safe_name
from .source_math import MATHML, mathml, mathml_text
from .source_scripts import prepare as prepare_scripts
from .table_grid import Grid, MAX_COLUMNS, MAX_ROWS, count, join_passages


OFFICE = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
DRAW = '{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}'
FO = '{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}'
TEXT = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
STYLE = '{urn:oasis:names:tc:opendocument:xmlns:style:1.0}'
TABLE = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
LOEXT = '{urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0}'
XML_ID = '{http://www.w3.org/XML/1998/namespace}id'
MANIFEST = '{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}'
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
MATH_ROOT = '{'+MATHML+'}math'
TEXT_ATTRIBUTES = (STYLE+'text-position', FO+'font-style', FO+'font-weight')
BOLD_WEIGHTS = frozenset(('bold','600','700','800','900'))
TEXT_MEDIA = frozenset((
    'application/vnd.oasis.opendocument.text',
    'application/vnd.oasis.opendocument.text-template',
    'application/vnd.oasis.opendocument.text-web',
))
MASTER_MEDIA = 'application/vnd.oasis.opendocument.text-master'
TEXT_BOX_CONTENT = frozenset((TEXT+'p',TEXT+'h',TEXT+'list',TEXT+'numbered-paragraph',
                              TEXT+'section',TABLE+'table'))
TABLE_REVISION_FAMILY = {TABLE+'table-row':'table-row', TABLE+'table-cell':'table-cell',
                         TABLE+'covered-table-cell':'table-cell'}
EMPTY_REVISION_CONTENT = frozenset(TABLE_REVISION_FAMILY) | {
    TEXT+tag for tag in ('p','h','span','a','s','tab','line-break','soft-page-break',
                         'list','list-item','list-header','numbered-paragraph')
}
CELL_VALUE_FIELDS = ((TABLE+'formula',) + tuple(OFFICE+field for field in
                     ('value','string-value','boolean-value','date-value','time-value')))


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


class Styles:
    def __init__(self,root,external=None,*,source_file='content.xml',location=None):
        self.nodes = {}
        self.location = location or (lambda node:node.getroottree().getpath(node))
        self.sources = {root:source_file}
        if external is not None:
            self.sources[external] = 'styles.xml'
        self.character_properties = {}
        for document in (external,root):
            if document is None:
                continue
            for node in document.iter():
                if node.tag in (STYLE+'style',STYLE+'master-page',STYLE+'page-layout') and (label:=node.get(STYLE+'name')):
                    self.nodes[node.tag,node.get(STYLE+'family'),label] = node

    def inheritance(self,family,label,*,strict=True):
        seen = set()
        while label:
            if label in seen:
                if strict:
                    raise Error('source_style','OpenDocument style inheritance is cyclic.')
                break
            seen.add(label)
            style = self.nodes.get((STYLE+'style',family,label))
            if style is None:
                break
            yield style
            label = style.get(STYLE+'parent-style-name')

    def properties(self,family,label,tag):
        values = {}
        for style in self.inheritance(family,label):
            if (node:=style.find(STYLE+tag)) is not None:
                values = dict(node.attrib)|values
        return values

    def character(self,label):
        if (STYLE+'style','text',label) not in self.nodes:
            return {}
        if label not in self.character_properties:
            fields = {}
            for style in self.inheritance('text',label):
                properties = style.find(STYLE+'text-properties')
                if properties is None:
                    continue
                tree = style.getroottree()
                origin = {'id':style.get(STYLE+'name'),'source_file':self.sources[tree.getroot()],
                          'xml_path':self.location(style)}
                for key in TEXT_ATTRIBUTES:
                    if (value:=properties.get(key)) is not None:
                        fields.setdefault(key,(value,origin))
            self.character_properties[label] = fields
        return self.character_properties[label]


class TableRevisions:
    """Resolve Writer's structural flag together with its saved deletion points."""
    def __init__(self,body,styles,source_file,inventory):
        self.styles = styles
        self.source_file = source_file
        self.inventory = inventory
        self.records = {}
        self.structure_styles = {}
        self.omissions = {}
        anchors = Counter(point.get(TEXT+'change-id') for point in
                          body.iter(TEXT+'change',TEXT+'change-start',TEXT+'change-end')
                          if next(point.iterancestors(TEXT+'tracked-changes'),None) is None)
        for ledger in body.iter(TEXT+'tracked-changes'):
            for region in ledger.iterchildren(TEXT+'changed-region'):
                identifiers = {value for field in (XML_ID,TEXT+'id') if (value:=region.get(field))}
                if len(identifiers)!=1:
                    self.records.update(dict.fromkeys(identifiers))
                    continue
                ident, = identifiers
                children = [child for child in region if isinstance(child.tag,str)]
                sole_deletion = len(children)==1 and children[0].tag==TEXT+'deletion'
                self.records[ident] = (children[0] if ident not in self.records and
                                       sole_deletion and anchors[ident]==1 else None)

    def structural(self,family,label):
        key = family,label
        if key not in self.structure_styles:
            value = None
            for style in self.styles.inheritance(family,label,strict=False):
                props = style.find(STYLE+family+'-properties')
                if props is not None and (value:=props.get(LOEXT+'text-changes-only')) is not None:
                    break
            self.structure_styles[key] = value=='false'
        return self.structure_styles[key]

    def deletion_points(self,node):
        points = {}
        for child in node.iter():
            if not isinstance(child.tag,str):
                if (child.tail or '').strip():
                    return {}
                continue
            if (child.text or '').strip() or (child is not node and (child.tail or '').strip()):
                return {}
            if child.tag==TEXT+'change':
                ident = child.get(TEXT+'change-id')
                deletion = self.records.get(ident)
                if deletion is None:
                    return {}
                points[ident] = deletion
            elif child.tag not in EMPTY_REVISION_CONTENT:
                # Insertion borders, drawings, notes and nested tables are current content.
                return {}
            elif child.tag in (TABLE+'table-cell',TABLE+'covered-table-cell') and any(
                    child.get(name) not in (None,'') for name in CELL_VALUE_FIELDS):
                return {}
        return points

    def omits(self,node):
        family = TABLE_REVISION_FAMILY.get(node.tag)
        label = node.get(TABLE+'style-name') if family else None
        if not (self.records and label and self.structural(family,label)):
            return False
        if node not in self.omissions:
            points = self.deletion_points(node)
            self.omissions[node] = bool(points)
            if points:
                tree = node.getroottree()
                self.inventory.append({
                    'kind':'odf_table_revision','selected_edition':'visible final','element':family,
                    'source_file':self.source_file,'xml_path':tree.getpath(node),'style':label,
                    'deletions':[{'id':ident,'xml_path':tree.getpath(record)} for ident,record in points.items()],
                })
        return self.omissions[node]


@dataclass
class TableCell:
    location: dict
    copies: int
    columns: int
    rows: int
    parts: list
    covered: bool
    deleted: bool


def projected_span(offset,size,visible):
    """Translate a physical span while preserving any out-of-bounds tail."""
    end = min(offset+size,len(visible)-1)
    return visible[end]-visible[offset]+offset+size-end


def project_row(authored,deleted_coverage,orphan_origins,source_row,deleted_row):
    """Project explicit covered slots after accepting row and cell deletions."""
    slots,visible = [],[0]
    for cell in authored:
        for repeat in range(cell.copies):
            column = len(slots)
            if column>=MAX_COLUMNS:
                raise Error('source_structure_size','An authored table exceeds the column budget.')
            if cell.deleted and not cell.covered:
                until = source_row+cell.rows
                for covered in range(column,min(column+cell.columns,MAX_COLUMNS)):
                    deleted_coverage[covered] = max(until,deleted_coverage.get(covered,0))
            elif deleted_row and not cell.covered and cell.rows>1:
                orphan_origins[column] = source_row+cell.rows,cell.columns
            omitted = deleted_row or cell.deleted or (cell.covered and deleted_coverage.get(column,0)>source_row)
            actual = cell
            if not deleted_row and (orphan:=orphan_origins.pop(column,None)):
                end,width = orphan
                if cell.covered and not omitted and end>source_row:
                    actual = replace(cell,columns=width,rows=end-source_row,covered=False)
            slots.append(None if omitted else (actual,repeat))
            visible.append(visible[-1]+(not omitted))
    return slots,visible


def script_position(value, inherited):
    match = re.fullmatch(r'(super|sub|[+\-]?\d+(?:\.\d+)?%)(?:\s+\d+(?:\.\d+)?%)?',value.strip())
    if not match:
        return inherited
    position = match[1]
    if position=='super' or position!='sub' and float(position[:-1])>0:
        return 'superscript'
    if position=='sub' or float(position[:-1])<0:
        return 'subscript'
    return None


def typography(fields):
    style,origin = fields.get(FO+'font-style',('',None))
    if style in ('italic','oblique','normal'):
        yield {'kind':'font_style','style':style,'style_definition':origin}
    weight,origin = fields.get(FO+'font-weight',('',None))
    if weight in BOLD_WEIGHTS:
        yield {'kind':'emphasis','style_definition':origin}


class Content:
    def __init__(self,read,source_file='content.xml',*,styles=None):
        self.read = read
        self.source_file = source_file
        self.declared_objects = None
        self.numbering = {}
        self.styles = styles
        self.table_revisions = None
        self.locations = {}

    def location(self,node):
        return self.locations.get(node) or node.getroottree().getpath(node)

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
                from .openoffice_xml import project

                declarations = xml(manifest)
                if declarations.tag.startswith('{http://openoffice.org/2001/manifest}'):
                    project(declarations,{})
                for entry in declarations:
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

    def paragraph(self,node, *, in_note=False, body=False, stories=None):
        pieces,spans,links,findings = [],[],[],[]
        offset = 0

        scripts = []
        marked = {'font_style':[], 'emphasis':[]}

        def add(text,script=None,marks=()):
            nonlocal offset
            if text:
                start = offset
                pieces.append(text)
                offset += len(text)
                if script:
                    if scripts and scripts[-1]['kind']==script and scripts[-1]['source_end']==start:
                        scripts[-1]['source_end'] = offset
                    else:
                        scripts.append({'kind':script,'source_start':start,'source_end':offset,
                                        'origin':'OpenDocument text-position style'})
                for mark in marks:
                    previous = marked[mark['kind']]
                    if (previous and previous[-1]['source_end']==start
                            and all(previous[-1].get(k)==v for k,v in mark.items())):
                        previous[-1]['source_end'] = offset
                    else:
                        previous.append({**mark,'source_start':start,'source_end':offset})

        def walk(item,script=None,fonts=None,marks=()):
            tag = name(item)
            if tag in ('tracked-changes','deletion','annotation'):
                return
            if tag=='note':
                if in_note:
                    findings.append('A nested OpenDocument note needs a verified reading.')
                    return
                locator = '#odt-note-'+key(self.location(item))[:16]
                text_node = next((n for n in item if name(n)=='note-body'),None)
                paragraphs = [self.paragraph(p,in_note=True) for p in text_node if name(p) in ('p','h')] if text_node is not None else []
                text,parts = join_formulas((p[0],p[1]) for p in paragraphs)
                problem = next((p[3] for p in paragraphs if p[3]),None)
                if not parts and text_node is not None:
                    text = plain_xml_text(text_node)
                spans.append({'kind':'note_reference','source_start':offset,'source_end':offset,
                              'source':'','href':locator})
                link = formula_note({'kind':'note','href':locator,'reference_href':locator,'source_start':offset,
                                     'source_locator':{'source_file':self.source_file,'xml_path':self.location(item)}},
                                    text,parts,problem or ('OpenDocument note has no body.' if not text else None))
                link.setdefault('reading',text)
                links.append(link)
                return
            if tag in ('s','tab','line-break'):
                count = int(attr(item,'c') or '1')
                if not 0<=count<=4096:
                    raise Error('source_structure_size','OpenDocument space repetition exceeds its reading bound.')
                start = offset
                add(' '*count if tag=='s' else '\t' if tag=='tab' else '\n',marks=marks)
                if tag=='line-break':
                    spans.append({'kind':'line_break','source_start':start,'source_end':offset})
                return
            is_visual = tag in ('image','object','object-ole','frame')
            formula = (item,self.source_file) if item.tag==MATH_ROOT else None
            if tag=='frame':
                if stories is not None and item.tag==DRAW+'frame':
                    boxes = item.findall(DRAW+'text-box')
                    if len(boxes)>1:
                        findings.append('An OpenDocument frame has more than one saved text story.')
                        return
                    if boxes and any(child.tag in TEXT_BOX_CONTENT for child in boxes[0]):
                        stories.append(boxes[0])
                        return
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
            if item.tag==TEXT+'span' and self.styles:
                primary = item.get(TEXT+'style-name')
                labels = ([primary] if primary else [])+item.get(TEXT+'class-names','').split()
                try:
                    fields = {}
                    for label in labels:
                        fields.update(self.styles.character(label))
                except Error as error:
                    findings.append(error.message)
                    fields = {}
                if position:=fields.pop(STYLE+'text-position',None):
                    script = script_position(position[0],script)
                if fields:
                    fonts = {**(fonts or {}),**fields}
                    marks = tuple(typography(fonts))
            add(item.text,script,marks)
            for child in item:
                walk(child,script,fonts,marks)
                add(child.tail,script,marks)
        walk(node)
        spans.extend(scripts)
        spans.extend(span for ranges in marked.values() for span in ranges)
        raw = ''.join(pieces)
        left = len(raw)-len(raw.lstrip())
        text = raw.strip()
        for item in [*spans,*links]:
            for part in ('source_start','source_end'):
                if part in item:
                    item[part] = min(len(text),max(0,item[part]-left))
        for span in spans:
            span['source'] = text[span['source_start']:span['source_end']]
        spans = prepare_scripts(text,spans)
        if label:=self.numbering.get(node):
            from .source_numbering import embed_ordered_label

            _,marker = label
            if issue:=marker.get('issue'):
                findings.append(issue)
            elif not body:
                embed_ordered_label(text,spans,label)
        return text,spans,links,' '.join(dict.fromkeys(findings)) or uncertainty(spans)

    def table(self,node,*,typed=False):
        grid = Grid()
        declared_rows,visible_rows = [],[0]
        for row,header in table_rows(node):
            repeats = count(attr(row,'number-rows-repeated'),maximum=1000)
            if len(visible_rows)+repeats>MAX_ROWS+1:
                raise Error('source_structure_size','An authored table exceeds the row budget.')
            deleted = self.table_revisions is not None and self.table_revisions.omits(row)
            declared_rows.append((row,header,repeats,deleted,len(visible_rows)-1))
            for _ in range(repeats):
                visible_rows.append(visible_rows[-1]+(not deleted))

        deleted_coverage,orphan_origins = {},{}
        for row,header,repeats,deleted,source_start in declared_rows:
            authored = []
            for cell in row:
                tag = name(cell)
                if tag not in ('table-cell','covered-table-cell'):
                    continue
                loc = {'source_file':self.source_file,'xml_path':self.location(cell)}
                if typed and (kind:=cell.get(OFFICE+'value-type')):
                    loc.update(office_type=kind,office_value=cell.get(OFFICE+'value',''))
                copies = count(attr(cell,'number-columns-repeated'),maximum=1000)
                columns = count(attr(cell,'number-columns-spanned'))
                rows = count(attr(cell,'number-rows-spanned'),maximum=10000)
                cell_deleted = self.table_revisions is not None and self.table_revisions.omits(cell)
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

                if tag=='table-cell' and not (cell_deleted or deleted):
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
                                locations.append({'source_file':self.source_file,'xml_path':self.location(child)})
                    flush()
                authored.append(TableCell(loc,copies,columns,rows,parts,tag=='covered-table-cell',cell_deleted))
            for row_repeat in range(repeats):
                source_row = source_start+row_repeat
                slots,visible_columns = project_row(authored,deleted_coverage,orphan_origins,source_row,deleted)
                if deleted:
                    continue
                grid.begin_row()
                for source_column,slot in enumerate(slots):
                    if slot is None:
                        continue
                    cell,cell_repeat = slot
                    location = {**cell.location,'row_repeat':row_repeat,'cell_repeat':cell_repeat}
                    if cell.covered:
                        grid.cover(location)
                    else:
                        grid.place(cell.parts,location,columns=projected_span(source_column,cell.columns,visible_columns),
                                   rows=projected_span(source_row,cell.rows,visible_rows),role='column' if header else 'data')
                        # ODF serializes covered cells explicitly.
                        grid.column = visible_columns[source_column+1]
                grid.end_row()
        return grid.finish()
