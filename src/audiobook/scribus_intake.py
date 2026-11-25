"""Read saved Scribus text, linked stories and layout through shared markup."""

from collections import defaultdict
from dataclasses import dataclass
from gzip import GzipFile
from io import BytesIO
from math import isfinite
from posixpath import dirname, join, normpath
import re
from zlib import error as compression_error

from lxml import etree

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read, name, xml
from .publication_package import bind_member_block, capture_linked_members, member_bytes, member_location
from .source_bundle import safe_name, stamp
from .xml_projection import XMLProjection, append_text

TEXT_TYPES = frozenset(('4','8'))
OBJECTS = frozenset(('PAGEOBJECT','MASTEROBJECT','FRAMEOBJECT'))
HEADING = re.compile(r'(?:h|header|heading|chapter)[ _-]*([1-6])$',re.I)
CHARACTERS = {'tab':'\t','breakline':'\n','breakcol':' ','breakframe':' ',
              'nbspace':'\u00a0','nbhyphen':'\u2011','zwspace':'\u200b','zwnbspace':'\u2060'}
LEGACY = str.maketrans({'\x04':'\t','\x05':'\r','\x18':'\u2011','\x1a':' ',
                       '\x1b':' ','\x1c':'\u2028','\x1d':'\u00a0'})
UNPRINTED = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def scribus_file(filename):
    return filename.casefold().endswith(('.sla','.sla.gz'))


def scribus_root(node):
    return node is not None and node.tag=='SCRIBUSUTF8NEW' and bool(node.get('Version'))


def parse_document(raw):
    if raw.startswith(b'\x1f\x8b'):
        try:
            with GzipFile(fileobj=BytesIO(raw)) as stream:
                raw = stream.read(MAX_STRUCTURE+1)
        except (EOFError,OSError,compression_error) as exc:
            raise Error('source_scribus_compression','The compressed Scribus manuscript is incomplete or unreadable.',
                        'Open the original SLA or SLA.GZ again.') from exc
    if len(raw)>MAX_STRUCTURE:
        raise Error('source_structure_size','The Scribus manuscript exceeds the structured reader memory budget.',
                    'Save a smaller chapter or export a tagged PDF from Scribus.')
    root = xml(raw)
    document = root.find('DOCUMENT')
    if not scribus_root(root) or document is None:
        raise Error('source_scribus','The selected file has no saved Scribus document.',
                    'Open an SLA or SLA.GZ file saved by Scribus.')
    return root,document


def loose_document(path):
    with path.open('rb') as stream:
        return parse_document(stream.read(MAX_STRUCTURE+1))


def asset_target(part,reference):
    reference = reference.replace('\\','/')
    if not reference or reference.startswith('/') or ':' in reference:
        return None
    try:
        return safe_name(normpath(join(dirname(part),reference)))
    except Error:
        return None


def asset_targets(document,part):
    return {target for item in document.iter() if name(item) in OBJECTS and item.get('PTYPE')=='2'
            and (target:=asset_target(part,item.get('PFILE',''))) and target!=part}


def discover_loose_assets(path,scope):
    try:
        _,document = loose_document(path)
    except Error:
        return set()
    return asset_targets(document,path.relative_to(scope).as_posix())


def capture_scribus(path,area,source,*,scope=None):
    scope = scope if scope is not None else path.parent
    expected = stamp(path.stat())
    _,document = loose_document(path)
    part = path.relative_to(scope).as_posix()
    requested = {target for target in asset_targets(document,part) if not scribus_file(target)}
    return capture_linked_members(path,area,source,scope,expected,requested)


def archive_claims(archive,members):
    by_name = defaultdict(list)
    for member in members:
        by_name[member.name].append(member)
    claimed = set()
    for member in members:
        if not scribus_file(member.name):
            continue
        try:
            _,document = parse_document(member_bytes(archive,member,MAX_STRUCTURE))
        except Error:
            continue
        for target in asset_targets(document,member.name):
            if not scribus_file(target):
                claimed.update(candidate.index for candidate in by_name[target])
    return claimed


def integer(value,default=-1):
    try:
        return int(value)
    except (ValueError,TypeError):
        return default


def coordinate(value):
    try:
        result = float(value)
        return result if isfinite(result) else 0
    except (ValueError,TypeError):
        return 0


def frame_nodes(parent):
    for item in parent:
        if name(item) in OBJECTS:
            yield item
            if item.get('PTYPE')=='12':
                yield from frame_nodes(item)


@dataclass
class Placement:
    node: object
    ident: str
    order: tuple
    scope: str


def place(document):
    hidden = {layer.get('NUMMER') for layer in document.findall('LAYERS')
              if layer.get('SICHTBAR') in ('0','false') and layer.get('NUMMER')}
    for index,item in enumerate(frame_nodes(document)):
        page = integer(item.get('OwnPage'))
        scope = 'page'
        if item.tag=='MASTEROBJECT':
            scope = 'master'
        elif item.tag=='FRAMEOBJECT':
            scope = 'inline'
        elif any(node.get('LAYER') in hidden for node in (item,*item.iterancestors())):
            scope = 'hidden'
        elif page<0:
            scope = 'pasteboard'
        yield Placement(item,item.get('ItemID') or str(index),
                        (page,round(coordinate(item.get('YPOS')),2),coordinate(item.get('XPOS')),index),scope)


def chains(placements):
    """Yield each text chain once, with unresolved links attached to their frame."""
    saved_ids = defaultdict(list)
    positional = {}
    for frame in placements:
        if frame.node.tag=='PAGEOBJECT':
            positional[len(positional)] = frame
            if ident:=frame.node.get('ItemID'):
                saved_ids[ident].append(frame)
    text = [frame for frame in positional.values() if frame.node.get('PTYPE') in TEXT_TYPES]
    edges, incoming, problems = {},{},defaultdict(list)

    def reference(frame,attribute):
        value = frame.node.get(attribute,'-1')
        if value=='-1':
            return None
        if frame.node.get('ItemID'):
            matches = saved_ids[value]
            found = matches[0] if len(matches)==1 else None
        else:
            found = positional.get(integer(value))
        if found is None or found.node.get('PTYPE') not in TEXT_TYPES:
            problems[frame.node].append(f'The Scribus frame link {attribute}={value} is missing or ambiguous.')
            return None
        return found

    for frame in text:
        following = reference(frame,'NEXTITEM')
        previous = reference(frame,'BACKITEM')
        if following is not None:
            if following.node in incoming:
                problems[frame.node].append('More than one Scribus frame links to this continuation.')
            else:
                edges[frame.node] = following
                incoming[following.node] = frame
        if previous is not None and previous.node.get('NEXTITEM','-1')!=frame.ident:
            problems[frame.node].append('The previous Scribus frame does not link back to this frame.')
    seen = set()
    for frame in sorted(text,key=lambda item:(item.node in incoming,item.order)):
        if frame.node in seen:
            continue
        chain = []
        current = frame
        while current is not None and current.node not in seen:
            chain.append(current)
            seen.add(current.node)
            current = edges.get(current.node)
        if current is not None:
            problems[chain[-1].node].append('This Scribus text chain circles back or rejoins an earlier frame.')
        if visible:=[item for item in chain if item.scope=='page']:
            issues = [(item.node,detail) for item in chain for detail in problems[item.node]]
            yield visible[0].order,chain,issues


class Styles:
    def __init__(self,document):
        self.paragraphs = {entry.get('NAME'):entry for entry in document.findall('STYLE') if entry.get('NAME')}
        self.characters = {entry.get('CNAME'):entry for entry in document.findall('CHARSTYLE') if entry.get('CNAME')}

    def lineage(self,ident,styles,parent):
        seen = set()
        while ident in styles and ident not in seen:
            seen.add(ident)
            definition = styles[ident]
            yield definition
            ident = definition.get(parent)

    def paragraph(self,style):
        ident = style.get('PARENT') or style.get('PSTYLE','')
        family = [ident,*[item.get('NAME','') for item in self.lineage(ident,self.paragraphs,'PARENT')]]
        for label in family:
            alias = label.strip().casefold()
            if alias in ('title','book title','chapter title'):
                return 'h1'
            if match:=HEADING.fullmatch(alias):
                return 'h'+match[1]
            if alias in ('header','heading'):
                return 'h1'
            if alias in ('code','code block','preformatted'):
                return 'pre'
            if alias in ('block quote','blockquote','quotation'):
                return 'blockquote'
        return 'p'

    def character(self,run):
        ident = run.get('CPARENT','')
        overrides = [*reversed(list(self.lineage(ident,self.characters,'CPARENT'))),run]
        attributes = {}
        for style in overrides:
            attributes.update(style.attrib)
        face = attributes.get('FONT',attributes.get('CFONT','')).casefold()
        label = ident.casefold()
        effects = attributes.get('FEATURES','').casefold().split()
        tags = []
        if 'bold' in face or label in ('strong','bold'):
            tags.append('strong')
        if 'italic' in face or 'oblique' in face or label in ('emphasis','italic'):
            tags.append('em')
        if 'superscript' in effects:
            tags.append('sup')
        elif 'subscript' in effects:
            tags.append('sub')
        if label in ('code','inline code'):
            tags.append('code')
        return tags


class Projection(XMLProjection):
    def __init__(self,root,part,document):
        super().__init__(root,part)
        self.styles = Styles(document)
        self.folios = {}
        for group in document.findall('Sections/Section'):
            start,end = integer(group.get('From')),integer(group.get('To'))
            sequence = integer(group.get('Start'),1)
            if group.get('Type') in (None,'Type_1_2_3') and group.get('Active','1')!='0':
                for index in range(max(start,0),min(end+1,len(document.findall('PAGE')))):
                    self.folios[index] = str(sequence+index-start)
        self.page_count = len(document.findall('PAGE'))
        self.first_page = integer(document.get('FIRSTNUM'),1)
        self.notes = defaultdict(list)
        for node in document.findall('Notes/Note'):
            if ident:=node.get('Master'):
                self.notes[ident].append(node)
        self.note_ids = {}
        self.note_definitions = []
        self.variables = {(node.get('label'),node.get('type')):node.get('str')
                          for node in document.findall('Marks/Mark')}

    def note_reference(self,node):
        label = node.get('label','')
        choices = self.notes[label]
        if len(choices)!=1 or not choices[0].get('Text'):
            return self.review(node,'This Scribus note has no unique saved definition.')
        if label not in self.note_ids:
            ident = 'scribus-note-'+str(len(self.note_ids)+1)
            self.note_ids[label] = ident
            self.note_definitions.append((ident,choices[0]))
        return self.element(node,'a',attributes={'href':'#'+self.note_ids[label],'role':'doc-noteref'},
                            inline_location=self.location(node))

    def runs(self,frame,page):
        story = frame.find('StoryText')
        if story is None:
            story = frame
        ending = story.find('DefaultStyle')
        if ending is None:
            ending = frame
        segment = []
        items = iter(story)
        item = next(items,None)
        while item is not None:
            following = next(items,None)
            kind = name(item)
            if kind in ('para','trail'):
                if segment:
                    yield segment,item
                    segment = []
            elif kind=='ITEXT':
                value = item.get('CH','')
                if 'Unicode' in item.attrib:
                    code = integer(item.get('Unicode'))
                    if 0xd800<=code<=0xdbff and following is not None and name(following)=='ITEXT':
                        low = integer(following.get('Unicode'))
                        if 0xdc00<=low<=0xdfff:
                            code = 0x10000+((code-0xd800)<<10)+(low-0xdc00)
                            following = next(items,None)
                    if 0<=code<=0x10ffff and not 0xd800<=code<=0xdfff:
                        value = chr(code)
                    else:
                        segment.append((item,self.review(item,'This Scribus text run contains an invalid Unicode value.')))
                        item = following
                        continue
                if item.get('PSTYLE'):
                    ending = item
                for index,part in enumerate(re.split(r'[\r\n\u2029]',value.translate(LEGACY))):
                    if index and segment:
                        yield segment,ending
                        segment = []
                    if '\x19' in part:
                        segment.append((item,self.review(item,'This Scribus inline object needs a verified reading.')))
                        part = part.replace('\x19','')
                    if '\x1e' in part or '\x17' in part:
                        folio = self.folios.get(page,str(page+self.first_page)) if page>=0 else ''
                        part = part.replace('\x1e',folio).replace('\x17',str(self.page_count))
                    if UNPRINTED.search(part):
                        segment.append((item,self.review(item,'This Scribus text run contains an unresolved control character.')))
                        part = UNPRINTED.sub('',part)
                    if part:
                        segment.append((item,part.replace('\u00ad','')))
            elif kind in CHARACTERS:
                segment.append((item,CHARACTERS[kind]))
            elif kind=='var':
                if item.get('name')=='pgco':
                    segment.append((item,str(self.page_count)))
                elif page>=0:
                    segment.append((item,self.folios.get(page,str(page+self.first_page))))
            elif kind=='MARK':
                mark_type = item.get('type')
                if mark_type=='4':
                    segment.append((item,self.note_reference(item)))
                elif mark_type=='3':
                    value = self.variables.get((item.get('label'),mark_type))
                    segment.append((item,value if value is not None else
                                    self.review(item,'This Scribus text variable has no saved value.')))
                elif mark_type in ('1','2'):
                    segment.append((item,self.review(item,'This Scribus cross-reference has no stored reading.')))
            item = following
        if segment:
            yield segment,ending

    def text(self,frame,target,page,*,cell=False,chain=()):
        for segment,styling in self.runs(frame,page):
            tag = 'p' if cell else self.styles.paragraph(styling)
            origin = segment[0][0]
            element = self.element(origin,tag,scribus_frames=list(chain),scribus_page=page+1 if page>=0 else None,
                                   style_location=self.location(styling))
            for position,value in segment:
                if isinstance(value,etree._Element):
                    element.append(value)
                    continue
                parent = element
                for emphasis in self.styles.character(position):
                    parent = etree.SubElement(parent,emphasis)
                append_text(parent,value)
            target.append(element)

    def figure(self,node):
        label = node.get('ALT') or node.get('AltText')
        for attribute in node.findall('PageItemAttributes/ItemAttribute'):
            if attribute.get('Name','').casefold() in ('alt','alt text','alt-text','description'):
                label = label or attribute.get('Value')
        if not label:
            return self.review(node,'This Scribus image has no authored accessible description.')
        attributes = {'alt':label}
        if target:=asset_target(self.part,node.get('PFILE','')):
            attributes['src'] = target
        return self.element(node,'img',attributes=attributes)

    def table(self,node,page):
        rows = defaultdict(list)
        cols = integer(node.get('Columns'))
        height = integer(node.get('Rows'))
        spans = [integer(value) for value in node.get('CellAreas','').split()]
        if cols<=0 or height<=0 or len(spans)%4:
            return self.review(node,'The Scribus table has no valid saved cell layout.')
        merge = {(r,c):(h,w) for r,c,h,w in zip(spans[::4],spans[1::4],spans[2::4],spans[3::4])}
        for cell in node.findall('TableData/Cell'):
            row,column = integer(cell.get('Row')),integer(cell.get('Column'))
            if not (0<=row<height and 0<=column<cols):
                return self.review(cell,'A Scribus table cell is outside its saved layout.')
            rows[row].append((column,cell))
        if not rows:
            return self.review(node,'The Scribus table has no saved cells.')
        table = self.element(node,'table',scribus_page=page+1)
        for row,items in sorted(rows.items()):
            line = etree.SubElement(table,'tr')
            for col,cell in sorted(items,key=lambda pair:pair[0]):
                h,w = merge.get((row,col),(1,1))
                attributes = {key:str(value) for key,value in (('colspan',w),('rowspan',h)) if value>1}
                item = self.element(cell,'td',attributes=attributes)
                self.text(cell,item,page,cell=True)
                line.append(item)
        return table

    def append_notes(self,target):
        for ident,definition in self.note_definitions:
            aside = self.element(definition,'aside',attributes={'id':ident,'role':'doc-footnote'})
            try:
                original = xml(definition.get('Text').encode('utf-8'))
                story = original if name(original)=='StoryText' else original.find('.//StoryText')
                if story is None:
                    raise Error('source_scribus_note','No saved story in this note.')
                carrier = etree.Element('NoteText')
                carrier.append(story)
                first = len(self.annotations)
                self.text(carrier,aside,-1,cell=True)
                # The note body is serialized inside this one physical attribute.
                origin = self.location(definition)
                for serial in range(first+1,len(self.annotations)+1):
                    self.annotations[str(serial)].update(origin,source_element=self.part+':'+origin['xml_path'])
            except Error:
                aside.append(self.review(definition,'The saved Scribus note text is unreadable.'))
            target.append(aside)


def build(root,document,part,sha,title):
    builder = DocumentBuilder('scribus',document.get('TITLE') or title,sha)
    builder.doc.metadata['scribus_version'] = root.get('Version')
    if lang:=document.get('DOCLANGINFO'):
        builder.doc.language = lang.replace('_','-')
    projector = Projection(root,part,document)
    placements = list(place(document))
    tasks = [(order,chain,issues) for order,chain,issues in chains(placements)]
    for frame in placements:
        if frame.scope=='page' and (frame.node.get('PTYPE')=='16' or
                                     frame.node.get('PTYPE')=='2' and frame.node.get('PFILE')):
            tasks.append((frame.order,[frame],[]))
        if frame.node.get('PTYPE') in TEXT_TYPES|{'2','16','17'}:
            builder.doc.inventory.append({'kind':'scribus_frame','frame':frame.ident,
                                          'page':frame.order[0]+1,'status':frame.scope,
                                          'source_file':part,'xml_path':frame.node.getroottree().getpath(frame.node)})
    target = etree.Element('div')
    for order,thread,issues in sorted(tasks,key=lambda item:item[0]):
        ids = [frame.ident for frame in thread]
        for frame in thread:
            kind = frame.node.get('PTYPE')
            if kind in TEXT_TYPES:
                projector.text(frame.node,target,frame.order[0],chain=ids)
            elif kind=='2':
                target.append(projector.figure(frame.node))
            elif kind=='16':
                target.append(projector.table(frame.node,frame.order[0]))
        for frame,issue in issues:
            target.append(projector.review(frame,issue))
    projector.append_notes(target)
    html_read(etree.tostring(target,method='html',encoding='unicode'),builder,where=part,metadata=False,
              collect_resources=True,source_annotations=projector.annotations)
    return builder.doc


def read_scribus(path,source,sha,title):
    from .archive_intake import containers

    if source.kind=='file':
        root,document = loose_document(path)
        return build(root,document,source.member_name or path.name,sha,title)
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        selected = next((item for item in members if item.index==source.entry),None)
        if selected is None:
            raise Error('source_member_missing','The saved Scribus manuscript is missing.')
        root,document = parse_document(member_bytes(archive,selected,MAX_STRUCTURE))
        doc = build(root,document,selected.name,sha,title)
        requested = asset_targets(document,selected.name)
        assets = defaultdict(list)
        for member in members:
            if member.name in requested:
                assets[member.name].append(member)
        records = {selected.name:member_location(sha,selected,'text')|{'status':'represented'}}
        for target in sorted(requested):
            owners = assets[target]
            if len(owners)==1 and not scribus_file(target):
                records[target] = member_location(sha,owners[0],'asset')|{'status':'preserved'}
            else:
                doc.inventory.append({'kind':'scribus_image','member':target,
                                      'status':'ambiguous' if len(owners)>1 else 'unavailable'})
        for serial,block in enumerate(doc.blocks,1):
            bind_member_block(block,selected,serial,sha,records,lambda _part,target:target)
        doc.metadata['source_resources'] = list(records.values())
        doc.inventory.extend(records.values())
        return doc
