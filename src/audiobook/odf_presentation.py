"""Read OpenDocument slide stories and keep their physical package locations."""

from collections import defaultdict
from decimal import Decimal
from pathlib import PurePosixPath
import re

from . import odf_content, table_grid, xml_source
from .archive_intake import containers
from .core import Error, key
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read, name, plain_xml_text, xml
from .markdown_publication import local_path
from .odf_content import DRAW, FO, MANIFEST, OFFICE, STYLE, TABLE, TEXT, XLINK_HREF
from .presentation_common import canonical, observe_picture, speaker_note, visual_label
from .publication_package import bind_member_block, member_bytes, member_location


PRESENT = '{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}'
SVG = '{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}'
MANIFEST_FILE = 'META-INF/manifest.xml'
CONTENT_FILE = 'content.xml'
MEDIA = frozenset((b'application/vnd.oasis.opendocument.presentation',
                   b'application/vnd.oasis.opendocument.presentation-template'))
LENGTH = re.compile(r'([+-]?(?:\d{1,8}(?:\.\d{1,10})?|\.\d{1,10}))(cm|mm|in|pt|pc)')
SHAPES = frozenset(DRAW+tag for tag in (
    'frame','custom-shape','rect','ellipse','circle','line','connector','polygon','polyline','path','caption'))
POSITIONAL = frozenset(('date-time','footer','header','page-number','page'))
FRAME_CONTENT = frozenset((TABLE+'table',DRAW+'text-box',DRAW+'object',DRAW+'image'))


def flat_presentation(root):
    return bool(root is not None and root.tag==OFFICE+'document'
                and root.get(OFFICE+'mimetype','').encode() in MEDIA)


def folder_marker(root):
    path = root/'mimetype'
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as stream:
        return stream.read(257).strip() in MEDIA


def archive_books(archive,members):
    publications,claimed = [],set()
    markers = (member for member in members if PurePosixPath(member.name).name=='mimetype'
               and (member.size is None or member.size<=256))
    for item in sorted(markers,key=lambda member:member.name):
        if item.index in claimed or member_bytes(archive,item,256).strip() not in MEDIA:
            continue
        prefix = item.name.removesuffix('mimetype')
        claimed.update(part.index for part in members if part.name.startswith(prefix))
        label = PurePosixPath(prefix).name
        publications.append((item,'OpenDocument presentation'+(f' ({label})' if label else '')))
    return publications,claimed


def location(node,slide,groups=()):
    loc = {'slide':slide,'xml_path':node.getroottree().getpath(node)}
    if groups:
        loc['shape_groups'] = list(groups)
    return loc


def length(value):
    match = LENGTH.fullmatch(value or '')
    if not match:
        raise Error('source_slide_geometry','The slide image has a relative or unavailable size.')
    scale = {'cm':1,'mm':Decimal('.1'),'in':Decimal('2.54'),'pt':Decimal('2.54')/72,
             'pc':Decimal('2.54')/6}
    return Decimal(match[1])*scale[match[2]]


def descriptions(node):
    return [plain_xml_text(item) for field in ('desc','title')
            for item in node.findall(SVG+field)]


def selected_shape(frame):
    return next((node for node in frame if node.tag in FRAME_CONTENT),None)


class SlideStyles(odf_content.Styles):
    def page_size(self,page):
        master = self.nodes.get((STYLE+'master-page',None,page.get(DRAW+'master-page-name')))
        layout = self.nodes.get((STYLE+'page-layout',None,master.get(STYLE+'page-layout-name'))) if master is not None else None
        properties = layout.find(STYLE+'page-layout-properties') if layout is not None else None
        if properties is None:
            raise Error('source_slide_geometry','The image needs a verified presentation page size.')
        return length(properties.get(FO+'page-width')),length(properties.get(FO+'page-height'))


class Presentation:
    def __init__(self,sha,title,cache,*,archive=None,members=(),prefix='',flat=None,source_file=CONTENT_FILE):
        from .pdf_visual import Observer

        self.archive = archive
        self.members = defaultdict(list)
        self.resources = {}
        for member in members:
            if member.name.startswith(prefix):
                self.members[member.name[len(prefix):]].append(member)
                record = member_location(sha,member,'package_member')
                self.resources[record['location_id']] = record
        self.protected = set()
        if manifest:=self.read(MANIFEST_FILE,required=False):
            self.protected = {node.get(MANIFEST+'full-path') for node in xml(manifest).iter(MANIFEST+'file-entry')
                              if node.find(MANIFEST+'encryption-data') is not None}
        self.main = self.member(CONTENT_FILE) if flat is None else None
        self.root = xml(self.read(CONTENT_FILE)) if flat is None else flat
        external = self.read('styles.xml',required=False)
        self.styles = SlideStyles(self.root,xml(external) if external else None,source_file=source_file)
        metadata = self.read('meta.xml',required=False)
        title_node = (xml(metadata) if metadata else self.root).find('.//{http://purl.org/dc/elements/1.1/}title')
        published_title = plain_xml_text(title_node) if title_node is not None else ''
        self.builder = DocumentBuilder('odp',published_title or title,sha)
        self.doc = self.builder.doc
        self.text = odf_content.Content(self.read,source_file,styles=self.styles)
        self.observer = Observer(cache/'presentation-observations' if cache else None)

    def member(self,path,required=True):
        items = self.members.get(path,[])
        if len(items)==1:
            return items[0]
        if required or items:
            raise Error('source_member_missing',f'The presentation has no unique part named {path}.')
        return None

    def read(self,path,required=True):
        if path in self.protected:
            raise Error('protected_source',f'The presentation part {path} is encrypted. Open a readable local copy.')
        item = self.member(path,required)
        return member_bytes(self.archive,item,MAX_STRUCTURE) if item is not None else None

    def bind(self,block,member,slide):
        if block and member:
            bind_member_block(block,member,slide,self.doc.source_sha256,{},None)
        return block

    def add(self,text,role,place,*,member=None,issue=None,links=None):
        member = member or self.main
        item = self.builder.add(text,role,member.name if member else 'OpenDocument XML',
                                loc=place,issue=issue,links=links)
        return self.bind(item,member,place['slide'])

    def paragraph(self,node,text,place,role='body'):
        source,spans,links,issue = text.paragraph(node)
        authored = any(span['kind']=='line_break' and span['source_end']>span['source_start'] for span in spans)
        place = place|{'inline_semantics':[span for span in spans if span['kind']!='line_break']}
        if authored:
            place['authored_line_breaks'] = True
        return self.add(source,role,place,links=links,issue=issue)

    def shapes(self,parent,groups=()):
        for node in parent:
            if node.get(PRESENT+'class') in POSITIONAL or node.get(PRESENT+'placeholder')=='true':
                continue
            if node.tag==DRAW+'g':
                label = node.get(DRAW+'name') or node.getroottree().getpath(node)
                yield from self.shapes(node,(*groups,label))
            elif node.tag in SHAPES:
                yield node,groups

    def paragraphs(self,node):
        if node.tag==DRAW+'frame':
            main = selected_shape(node)
            if main is not None and main.tag==DRAW+'text-box':
                yield from self.paragraphs(main)
            return
        for child in node:
            if child.tag in (TEXT+'p',TEXT+'h'):
                yield child
            elif child.tag in (TEXT+'list',TEXT+'list-item',TEXT+'list-header',DRAW+'text-box'):
                yield from self.paragraphs(child)

    def geometry(self,shape,page,loc):
        properties = self.styles.properties('graphic',shape.get(DRAW+'style-name'),'graphic-properties')
        if (loc.get('shape_groups') or shape.get(DRAW+'transform') or
                properties.get(STYLE+'mirror','none')!='none'):
            raise Error('source_slide_geometry','A transformed slide image needs a verified visual reading.')
        clip = properties.get(FO+'clip','auto')
        if clip!='auto':
            match = re.fullmatch(r'rect\((.*)\)',clip)
            fields = re.split(r'\s*,\s*|\s+',match[1].strip()) if match else ()
            if len(fields)!=4 or any(value!='auto' and length(value)!=0 for value in fields):
                raise Error('source_slide_geometry','An explicitly clipped slide image needs a verified visual reading.')
        width,height = self.styles.page_size(page)
        left,top,right,bottom = (length(shape.get(SVG+axis,'0cm')) for axis in ('x','y','width','height'))
        right += left
        bottom += top
        if not (0<=left<right<=width and 0<=top<bottom<=height):
            raise Error('source_slide_geometry','A slide-edge image needs a verified visual reading.')
        return [str(n) for n in (left,top,right,bottom)]

    def image_source(self,image,loc):
        binary = image.find(OFFICE+'binary-data')
        if binary is not None:
            data = xml_source.payload(binary)
            asset = xml_source.describe(binary,data)
            ident = key([self.doc.source_sha256,self.main.name if self.main else '',asset['indices']])[:20]
            record = {**loc,'role':'asset','size':len(data),'location_id':ident,'xml_asset':asset,
                      'resource_label':f"Slide {loc['slide']} image"}
            if self.main:
                record.update(member=self.main.name,archive_entry=self.main.index)
            self.resources[ident] = record
            return data,self.main,record
        path = local_path(image.get(XLINK_HREF,''),CONTENT_FILE,'')
        if path in (None,CONTENT_FILE):
            raise Error('source_slide_image','The slide image has no unique local image data.')
        member = self.member(path)
        record = member_location(self.doc.source_sha256,member,'asset')
        self.resources[record['location_id']] = record
        return self.read(path),member,record

    def image(self,node,frame,page,loc,record,raster_only):
        label = visual_label(*descriptions(frame),*descriptions(node))
        if label:
            self.add(label,'caption',loc)
            return
        if not raster_only:
            raise Error('source_slide_image','A slide image needs an authored description or verified visual reading.')
        loc['slide_rectangle_cm'] = self.geometry(frame,page,loc)
        failure = None
        alternatives = [node,*[item for item in node.itersiblings() if item.tag==DRAW+'image']]
        for candidate in alternatives:
            try:
                data,member,asset = self.image_source(candidate,loc)
                place = loc|({'xml_asset':asset['xml_asset']} if 'xml_asset' in asset else {})
                entries,receipt = observe_picture(self.observer,data,place)
                break
            except Error as error:
                if error.code not in ('source_slide_image','source_xml_asset','source_member_missing'):
                    raise
                failure = failure or error
        else:
            raise failure
        receipt['source_location'] = asset['location_id']
        record.setdefault('visual_observations',[]).append(receipt)
        for row,place in entries:
            self.add(row['text'],row.get('role','body'),place,member=member,issue=row.get('issue'))
        if not entries:
            raise Error('source_slide_image','The embedded slide image has no verified readable text.')

    def emit_table(self,table,loc,member=None):
        member = member or self.main
        item = self.builder.grid(table,member.name if member else 'OpenDocument XML',loc=loc)
        return self.bind(item,member,loc['slide'])

    def object(self,node,loc):
        from .odf_charts import chart_grid

        href = node.get(XLINK_HREF)
        member,root,content = self.main,node,self.text
        if href:
            directory = local_path(href,CONTENT_FILE,'')
            if directory in (None,CONTENT_FILE):
                raise Error('source_slide_frame','The slide object does not have a local data part.')
            path = directory.rstrip('/')+'/'+CONTENT_FILE
            member = self.member(path)
            root = xml(self.read(path))
            content = odf_content.Content(self.read,path,styles=odf_content.Styles(root,source_file=path))
        place = loc|{'source_shape_path':loc['xml_path'],'xml_path':root.getroottree().getpath(root)}
        try:
            grid = chart_grid(root,content,place)
        except Error as error:
            self.add('','unreadable',place,member=member,issue=error.message)
        else:
            self.emit_table(grid,place,member)

    def slide(self,page,number):
        base = location(page,number)
        record = {'kind':'slide',**base}
        if self.main:
            record['member'] = self.main.name
        self.doc.inventory.append(record)
        properties = self.styles.properties('drawing-page',page.get(DRAW+'style-name'),'drawing-page-properties')
        if properties.get(PRESENT+'visibility')=='hidden' or page.get(PRESENT+'visibility')=='hidden':
            record['status'] = 'hidden by source'
            return
        shapes = list(self.shapes(page))
        title_frame = next((node for node,_ in shapes if node.get(PRESENT+'class')=='title'
                            and any(self.text.paragraph(part)[0] for part in self.paragraphs(node))),None)
        if title_frame is not None:
            values = [self.text.paragraph(node) for node in self.paragraphs(title_frame)]
            combined = table_grid.join_passages((source,spans,links) for source,spans,links,_ in values)
            title = combined['source']
            title_loc = location(title_frame,number)|{'inline_semantics':combined['spans']}
            issue = next((p for *_,p in values if p),None)
            title_links = combined['notes']
        else:
            title = page.get(DRAW+'name') or f'Slide {number}'
            if re.fullmatch(r'page\s*\d+',title,re.I):
                title = f'Slide {number}'
            title_loc,issue,title_links = base|{'derived_slide_label':True},None,[]
        begin = len(self.doc.blocks)
        self.add(title,'heading',title_loc,issue=issue,links=title_links)
        active = [(node,groups) for node,groups in shapes if node is not title_frame]
        for node,groups in active:
            loc = location(node,number,groups)
            try:
                main = selected_shape(node) if node.tag==DRAW+'frame' else node
                if main is None:
                    self.add(visual_label(*descriptions(node)),'caption',loc)
                elif main.tag==TABLE+'table':
                    grid = self.text.table(main)
                    if label:=visual_label(*descriptions(node)):
                        grid['caption'] = table_grid.passage(label)
                    self.emit_table(grid,loc)
                elif main.tag==DRAW+'image':
                    self.image(main,node,page,loc,record,len(active)==1 and title_frame is None)
                elif main.tag==DRAW+'object':
                    self.object(main,loc)
                else:
                    parts = list(self.paragraphs(main))
                    label = visual_label(*descriptions(node))
                    if label and canonical(label) not in {canonical(self.text.paragraph(part)[0]) for part in parts}:
                        self.add(label,'caption',loc)
                    for part in parts:
                        self.paragraph(part,self.text,location(part,number,groups))
            except Error as error:
                self.add('','unreadable',loc,issue=error.message)
        known = {canonical(item.source):item.id for item in self.doc.blocks[begin:] if item.source and not item.issue}
        for note in page.findall(PRESENT+'notes'):
            for frame,groups in self.shapes(note):
                for part in self.paragraphs(frame):
                    source = self.text.paragraph(part)[0]
                    role,loc = speaker_note(source,location(part,number,groups),known)
                    self.paragraph(part,self.text,loc,role)
        if len(self.doc.blocks[begin:])==1 and active and title_frame is None:
            self.add('','unreadable',base,issue='The slide contains graphics without authored words or descriptions.')
        record['status'] = 'needs_review' if any(block.issue for block in self.doc.blocks[begin:]) else 'represented'

    def read_deck(self):
        body = self.root.find(OFFICE+'body')
        presentation = body.find(OFFICE+'presentation') if body is not None else None
        if presentation is None:
            raise Error('source_presentation_part','The OpenDocument source has no presentation body.')
        slides = presentation.findall(DRAW+'page')
        if not slides or len(slides)>10000:
            raise Error('source_structure_size','The OpenDocument presentation has an invalid slide count.')
        for number,slide in enumerate(slides,1):
            try:
                self.slide(slide,number)
            except Error as error:
                self.add('','unreadable',location(slide,number),issue=error.message)
        if self.resources:
            self.doc.metadata['source_resources'] = list(self.resources.values())
        return self.doc


def read_odp(path,source,sha,title,cache):
    if source.kind=='file':
        root = xml(bounded_read(path))
        if not flat_presentation(root):
            raise Error('source_presentation_part','The selected file is not a flat OpenDocument presentation.')
        return Presentation(sha,title,cache,flat=root,source_file=source.member_name or path.name).read_deck()
    with containers(path,source.kind) as archive:
        members = list(archive.members())
        editions,_ = archive_books(archive,members)
        matches = [item for item,_ in editions if source.entry is None or item.index==source.entry]
        if len(matches)!=1:
            raise Error('source_presentation_part','The source has no unique OpenDocument presentation package.')
        if sum(member.name==matches[0].name for member in members)!=1:
            raise Error('source_presentation_part','The source repeats its OpenDocument media-type marker.')
        prefix = matches[0].name.removesuffix('mimetype')
        return Presentation(sha,title,cache,archive=archive,members=members,prefix=prefix).read_deck()
