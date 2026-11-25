"""Project saved FrameMaker body flows into Hamr's common document reader."""

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from posixpath import dirname, join, normpath
import re

from lxml import etree

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read
from .mif_syntax import loose_mif, parse_mif
from .publication_package import bind_member_block, capture_linked_members, member_bytes, member_location
from .source_bundle import safe_name, stamp
from .xml_projection import append_text

CHARS = {'Tab':'\t','HardSpace':'\u00a0','NumberSpace':'\u2007','ThinSpace':'\u2009',
         'EnSpace':'\u2002','EmSpace':'\u2003','HardHyphen':'\u2011','EnDash':'–','EmDash':'—',
         'SoftHyphen':'','DiscHyphen':'','NoHyphen':'','Cent':'¢','Pound':'£','Yen':'¥',
         'Dagger':'†','DoubleDagger':'‡','Bullet':'•'}
HEADING = re.compile(r'(?:h|head|heading)[ _-]*([1-6])(?:\b|(?=heading))', re.I)
DEVICE_PART = re.compile(r'<([rchvu])>', re.I)
FORMAT_CODE = re.compile(r'<[^<>]*>')
VARIABLES = {'<$lastpagenum>':'last_page','<$curpagenum>':'page','<$chapnum>':'chapter_number'}
NON_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')
CALLOUT = re.compile(r'\d{1,3}')


def mif_file(name):
    return PurePosixPath(name).suffix.casefold()=='.mif'


def asset_target(part,reference):
    if not reference:
        return None
    if reference.startswith('<'):
        pieces = DEVICE_PART.split(reference)
        if pieces[0] or len(pieces)<3 or any(code not in ('c','u') for code in pieces[1::2]):
            return None
        reference = '/'.join('..' if code=='u' else entry
                             for code,entry in zip(pieces[1::2],pieces[2::2]))
    reference = reference.replace('\\','/')
    if reference.startswith('/') or ':' in reference:
        return None
    try:
        return safe_name(normpath(join(dirname(part),reference)))
    except Error:
        return None


def image_target(node,part):
    return asset_target(part,node.get('ImportObFileDI') or node.get('ImportObFile'))


def asset_targets(root,part):
    return {target for node in root.walk('ImportObject') if (target:=image_target(node,part))
            and target!=part and not mif_file(target)}


def discover_loose_assets(path,scope):
    try:
        root = loose_mif(path)
    except Error:
        return set()
    return asset_targets(root,path.relative_to(scope).as_posix())


def capture_mif(path,area,source,*,scope=None):
    scope = scope if scope is not None else path.parent
    expected = stamp(path.stat())
    root = loose_mif(path)
    requested = asset_targets(root,path.relative_to(scope).as_posix())
    return capture_linked_members(path,area,source,scope,expected,requested)


def archive_claims(archive,members):
    names = defaultdict(list)
    for member in members:
        names[member.name].append(member)
    claimed = set()
    for member in members:
        if not mif_file(member.name):
            continue
        try:
            root = parse_mif(member_bytes(archive,member,MAX_STRUCTURE))
        except Error:
            continue
        for target in asset_targets(root,member.name):
            claimed.update(item.index for item in names[target])
    return claimed


def index(nodes,identity):
    result = defaultdict(list)
    for node in nodes:
        if ident:=node.get(identity):
            result[ident].append(node)
    return result


@dataclass(frozen=True)
class Page:
    number: int
    label: str
    kind: str


@dataclass(frozen=True)
class Passage:
    paragraph: object
    page: Page | None
    frame: str
    lines: tuple


def paragraph_lines(para):
    return (*para.all('ParaLine'),) or (para,)


def rectangle_references(para):
    return [item.value for item in para.walk('TextRectID')]


def flows(root,pages,anchors):
    """Assign the saved flow to physical pages or its anchored owner."""
    frames = defaultdict(list)
    for source,page in pages:
        for frame in source.walk('TextRect'):
            frames[frame.get('ID')].append((page,''))
    for anchor,owners in anchors.items():
        for owner in owners:
            for frame in owner.walk('TextRect'):
                frames[frame.get('ID')].append((None,anchor))
    body = [page for _,page in pages if page.kind=='BodyPage']
    for flow in (*root.all('TextFlow'),root):
        paragraphs = tuple(flow.all('Para'))
        if not paragraphs:
            continue
        explicit = {ident for para in paragraphs for ident in rectangle_references(para)}
        if not explicit and pages and not (len(body)==1 and not frames):
            yield flow,(),()
            continue
        page = body[0] if len(body)==1 and not explicit else None
        passages = []
        issues = []
        attached = ''
        for para in paragraphs:
            permitted = []
            first_page = None
            first_frame = ''
            for line in paragraph_lines(para):
                anchor = line.get('TextRectID') or (para.get('TextRectID') if line is para.first('ParaLine') else '')
                if anchor:
                    matches = frames[anchor]
                    if len(matches)==1:
                        page,attached = matches[0]
                    else:
                        issues.append((line,f'The FrameMaker text rectangle {anchor} is missing or ambiguous.'))
                        page,attached = None,''
                        continue
                if attached or (page is not None and page.kind=='BodyPage') or not pages:
                    if not permitted:
                        first_page,first_frame = page,attached
                    elif first_frame!=attached:
                        passages.append(Passage(para,first_page,first_frame,tuple(permitted)))
                        first_page,first_frame = page,attached
                        permitted = []
                    permitted.append(line)
            if permitted:
                passages.append(Passage(para,first_page,first_frame,tuple(permitted)))
        yield flow,passages,issues


def para_tag(label):
    label = label.strip().casefold()
    if label in ('title','chapter','chapter title') or label.startswith('chapter head'):
        return 'h1'
    if match:=HEADING.match(label):
        return 'h'+match[1]
    if label in ('code','code block','preformatted','computer code'):
        return 'pre'
    if label in ('blockquote','block quote','quotation'):
        return 'blockquote'
    if 'caption' in label:
        return 'figcaption'
    return 'p'


class Projection:
    def __init__(self,root,part,pages):
        self.part = part
        self.annotations = {}
        self.tables = index(root.walk('Tbl'),'TblID')
        self.frames = index((frame for owner in root.all('AFrames') for frame in owner.all('Frame')),'ID')
        self.frame_passages = defaultdict(list)
        self.notes = index(root.walk('FNote'),'ID')
        self.definitions = {}
        self.pending_notes = deque()
        self.active_objects = set()
        self.variables = {node.get('VariableName'):node.get('VariableDef') for node in root.walk('VariableFormat')}
        self.fonts = {node.get('FTag'):node for catalog in root.all('FontCatalog')
                      for node in catalog.all('Font') if node.get('FTag')}
        self.visible_conditions = {node.get('CTag'):node.get('CState')=='CShown' for node in root.walk('Condition')}
        self.last_page = next((page.label for _,page in reversed(pages) if page.kind=='BodyPage'),'')
        self.document = root.first('Document')
        self.chapter_number = ''
        if self.document and self.document.get('ChapterNumComputeMethod')=='StartNumbering':
            style = self.document.get('ChapterNumStyle')
            if style=='Arabic':
                self.chapter_number = self.document.get('ChapterNumStart')
            elif style=='Text':
                self.chapter_number = self.document.get('ChapterNumText')

    def location(self,node):
        return {'source_file':self.part,'lines':[node.line,node.end],'mif_statement':node.name}

    def element(self,node,tag,attributes=None,**evidence):
        result = etree.Element(tag,attrib=attributes or {})
        label = str(len(self.annotations)+1)
        result.set('data-hamr-node',label)
        self.annotations[label] = {**self.location(node),'source_element':f'{self.part}:line {node.line}',**evidence}
        return result

    def review(self,node,message,attributes=None):
        return self.element(node,'span',attributes,source_issue=message,
                            reading_span={'kind':'source_review','source':'','issue':message,
                                          'source_locator':self.location(node)})

    def note(self,node):
        choices = self.notes[node.value]
        if len(choices)!=1:
            return self.review(node,'This FrameMaker footnote has no unique saved definition.')
        if node.value not in self.definitions:
            ident = 'mif-note-'+str(len(self.definitions)+1)
            self.definitions[node.value] = ident
            self.pending_notes.append((ident,choices[0]))
        ident = self.definitions[node.value]
        return self.element(node,'a',{'href':'#'+ident,'role':'doc-noteref'},inline_location=self.location(node))

    def variable(self,node,page,parent):
        name = node.get('VariableName')
        definition = self.variables.get(name,'')
        saved = node.get('VariableValue')
        if saved:
            return saved
        values = {'last_page':self.last_page,'page':page.label if page else '',
                  'chapter_number':self.chapter_number}

        def resolve(match):
            field = VARIABLES.get(match[0])
            if field and values[field]:
                return values[field]
            if match[0].startswith('<$'):
                return match[0]
            return ''

        value = FORMAT_CODE.sub(resolve,definition)
        if value and '<$' not in value:
            return value
        parent.append(self.review(node,f'The FrameMaker variable “{name}” has no verified saved value.'))
        return ''

    def font_tags(self,node):
        base = self.fonts.get(node.get('FTag'))

        def field(name):
            return node.get(name,base.get(name) if base else '').casefold()

        tags = []
        if 'bold' in field('FWeight'):
            tags.append('strong')
        if 'italic' in field('FAngle') or 'oblique' in field('FAngle'):
            tags.append('em')
        if field('FPosition')=='fsuperscript':
            tags.append('sup')
        elif field('FPosition')=='fsubscript':
            tags.append('sub')
        return tags

    def place_image(self,node):
        description = node.get('AltText') or node.get('ActualText')
        target = image_target(node,self.part)
        if description:
            return self.element(node,'img',{'alt':description,**({'src':target} if target else {})})
        result = self.review(node,'This FrameMaker illustration has no saved accessible description.')
        if target:
            etree.SubElement(result,'img',src=target)
        return result

    def placed_frame(self,node,parent,page,*,illustrated=False):
        illustrated = illustrated or node.name=='Frame' and any(node.walk('ImportObject'))
        for child in node.children:
            if child.name=='TextLine':
                text = ''.join(run.value for run in child.all('String')).strip()
                callout = illustrated and bool(CALLOUT.fullmatch(text))
                element = self.element(child,'p',mif_page=page.number if page else None,source_control=callout)
                self.inline((child,),element,page)
                if len(element) or element.text:
                    parent.append(element)
            elif child.name=='ImportObject':
                parent.append(self.place_image(child))
            elif child.name=='Frame':
                self.placed_frame(child,parent,page,illustrated=illustrated)

    def anchored(self,node,parent,page):
        table = node.name=='ATbl'
        matches = (self.tables if table else self.frames)[node.value]
        key = (node.name,node.value)
        if len(self.active_objects)>=48:
            parent.append(self.review(node,'This FrameMaker object exceeds the anchor nesting budget.'))
            return
        if len(matches)!=1 or key in self.active_objects:
            parent.append(self.review(node,'This FrameMaker object reference is missing, ambiguous or circular.'))
            return
        self.active_objects.add(key)
        owner = matches[0]
        if table:
            parent.append(self.table(owner,page))
        else:
            self.placed_frame(owner,parent,page)
            for para,lines,tag in self.frame_passages[node.value]:
                self.paragraph(para,parent,page,lines,tag)
        self.active_objects.remove(key)

    def table(self,node,page):
        table = self.element(node,'table')
        title = node.first('TblTitle')
        if title and (content:=title.first('TblTitleContent')):
            caption = etree.SubElement(table,'caption')
            self.container(content,caption,page,plain=True)
        for group in ('TblH','TblBody','TblF'):
            for section in node.all(group):
                for row in section.all('Row'):
                    output = etree.SubElement(table,'tr')
                    for cell in row.all('Cell'):
                        attributes = {attr:value for key,attr in (('CellRows','rowspan'),('CellColumns','colspan'))
                                      if (value:=cell.get(key)).isascii() and value.isdigit() and 1<int(value)<=1000}
                        tag = 'th' if group=='TblH' else 'td'
                        semantic = self.element(cell,tag,attributes)
                        if content:=cell.first('CellContent'):
                            self.container(content,semantic,page,plain=True)
                        output.append(semantic)
        return table

    def inline(self,lines,output,page):
        font = ()
        visible = True
        reference = None
        anchored = []
        for line in lines:
            for item in line.children:
                kind = item.name
                if kind=='Conditional':
                    tags = [child.value for child in item.all('InCondition')]
                    visible = any(self.visible_conditions.get(tag,True) for tag in tags) if tags else True
                elif kind=='Unconditional':
                    visible = True
                elif not visible:
                    continue
                elif kind=='Font':
                    font = self.font_tags(item)
                elif kind in ('ATbl','AFrame'):
                    anchored.append(item)
                elif kind=='FNote':
                    output.append(self.note(item))
                elif kind=='XRef':
                    reference = item,len(output),len(''.join(output.itertext()))
                elif kind=='XRefEnd':
                    if reference and reference[1:]==(len(output),len(''.join(output.itertext()))):
                        saved = reference[0].get('XRefAltText')
                        if saved:
                            append_text(output,saved)
                        else:
                            output.append(self.review(reference[0],'This FrameMaker cross-reference has no saved reading.'))
                    reference = None
                elif kind=='Char' and item.value=='HardReturn':
                    etree.SubElement(output,'br')
                elif kind in ('String','Char','Variable'):
                    if kind=='String':
                        value = item.value
                    elif kind=='Variable':
                        value = self.variable(item,page,output)
                    elif item.value in CHARS:
                        value = CHARS[item.value]
                    else:
                        output.append(self.review(item,'This FrameMaker character has no verified reading.'))
                        continue
                    if NON_TEXT.search(value):
                        output.append(self.review(item,'This FrameMaker text contains an unresolved control character.'))
                        value = NON_TEXT.sub('',value)
                    if value:
                        parent = output
                        for emphasis in font:
                            parent = etree.SubElement(parent,emphasis)
                        append_text(parent,value)
        if reference:
            output.append(self.review(reference[0],'This FrameMaker cross-reference has no saved end marker.'))
        return anchored

    def paragraph(self,para,parent,page,lines,tag):
        output = self.element(para,tag,mif_page=page.number if page else None,
                              mif_paragraph_style=para.get('PgfTag'))
        label = para.get('PgfNumString').strip()
        if label and label!='•':
            append_text(output,label+' ')
        anchors = self.inline(lines,output,page)
        if len(output) or (output.text or '').strip():
            parent.append(output)
        for item in anchors:
            self.anchored(item,parent,page)

    def container(self,node,parent,page,plain=False):
        tag = ''
        for para in node.all('Para'):
            tag = para.get('PgfTag',tag)
            self.paragraph(para,parent,page,paragraph_lines(para),'p' if plain else para_tag(tag))

    def append_notes(self,parent):
        while self.pending_notes:
            ident,node = self.pending_notes.popleft()
            output = self.element(node,'aside',{'id':ident,'role':'doc-footnote'})
            self.container(node,output,None,plain=True)
            parent.append(output)


def build(root,part,sha,title):
    builder = DocumentBuilder('mif',title,sha)
    pages = [(node,Page(n,node.get('PageNum'),node.get('PageType')))
             for n,node in enumerate(root.all('Page'),1)]
    projector = Projection(root,part,pages)
    target = etree.Element('div')
    builder.doc.metadata['mif_version'] = root.get('MIFFile')
    pending = []
    for node,page in pages:
        if page.kind=='BodyPage':
            pending.append((page.number,0,node.line,node,page))
    for flow,passages,issues in flows(root,pages,projector.frames):
        if flow.name=='TextFlow':
            scope = 'body' if any(not passage.frame for passage in passages) else 'anchored' if passages else 'excluded'
            builder.doc.inventory.append({'kind':'mif_flow','tag':flow.get('TFTag'),
                                          'status':scope,**projector.location(flow)})
        for node,reason in issues:
            page = next((passage.page for passage in passages if passage.paragraph.line>=node.line and passage.page),None)
            pending.append((page.number if page else 0,1,node.line,projector.review(node,reason),page))
        style = ''
        for passage in passages:
            para,page,lines = passage.paragraph,passage.page,passage.lines
            style = para.get('PgfTag',style)
            content = para,lines,para_tag(style)
            if passage.frame:
                projector.frame_passages[passage.frame].append(content)
            else:
                pending.append((page.number if page else 0,1,para.line,content,page))
    for _,kind,_,node,page in sorted(pending,key=lambda item:item[:3]):
        if kind==0:
            projector.placed_frame(node,target,page)
        elif isinstance(node,etree._Element):
            target.append(node)
        else:
            para,lines,tag = node
            projector.paragraph(para,target,page,lines,tag)
    projector.append_notes(target)
    html_read(etree.tostring(target,method='html',encoding='unicode'),builder,where=part,metadata=False,
              collect_resources=True,source_annotations=projector.annotations)
    return builder.doc


def read_mif(path,source,sha,title):
    from .archive_intake import containers

    if source.kind=='file':
        return build(loose_mif(path),source.member_name or path.name,sha,title)
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        selected = next((item for item in members if item.index==source.entry),None)
        if selected is None:
            raise Error('source_member_missing','The saved FrameMaker manuscript is missing.')
        root = parse_mif(member_bytes(archive,selected,MAX_STRUCTURE))
        doc = build(root,selected.name,sha,title)
        requested = asset_targets(root,selected.name)
        owners = defaultdict(list)
        for member in members:
            if member.name in requested:
                owners[member.name].append(member)
        records = {selected.name:member_location(sha,selected,'text')|{'status':'represented'}}
        for name in sorted(requested):
            matches = owners[name]
            if len(matches)==1:
                records[name] = member_location(sha,matches[0],'asset')|{'status':'preserved'}
            else:
                doc.inventory.append({'kind':'mif_image','member':name,
                                      'status':'ambiguous' if matches else 'unavailable'})
        for ordinal,block in enumerate(doc.blocks,1):
            bind_member_block(block,selected,ordinal,sha,records,lambda _,href:href)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(records.values())
        return doc
