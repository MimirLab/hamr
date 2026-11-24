"""Read PresentationML in declared slide and shape order."""

from pathlib import PurePosixPath

from . import drawing_text, table_grid
from .archive_intake import containers
from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, name, xml
from .epub_semantics import uncertainty
from .opc_source import Parts, TYPES, TYPES_NAME, manifest_xml
from .presentation_common import canonical, observe_picture, speaker_note, visual_label
from .publication_package import bind_member_block, member_bytes, member_location


PRESENTATION = frozenset(('http://schemas.openxmlformats.org/presentationml/2006/main',
                          'http://purl.oclc.org/ooxml/presentationml/main'))
REL = frozenset(('http://schemas.openxmlformats.org/officeDocument/2006/relationships',
                 'http://purl.oclc.org/ooxml/officeDocument/relationships'))
MANIFEST_TYPES = frozenset(('application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml',))
COMPATIBILITY = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
DECORATIVE = '{http://schemas.microsoft.com/office/drawing/2017/decorative}decorative'
SHAPES = frozenset(('sp','pic','grpSp','graphicFrame','cxnSp'))
POSITIONAL = frozenset(('dt','ftr','hdr','sldNum','sldImg'))
TITLES = frozenset(('title','ctrTitle'))
LIMIT = 1024*1024
CORE_PROPERTIES = 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties'


def identifies(manifest):
    return manifest is not None and any(node.tag==TYPES+'Override'
                and node.get('ContentType') in MANIFEST_TYPES for node in manifest)


def folder_marker(root):
    path = root/TYPES_NAME
    if path.is_symlink() or not path.is_file():
        return False
    with path.open('rb') as stream:
        data = stream.read(LIMIT+1)
    return len(data)<=LIMIT and identifies(manifest_xml(data))


def archive_books(archive,members):
    publications,claimed = [],set()
    for item in sorted(members,key=lambda member:member.name):
        if (item.index in claimed or PurePosixPath(item.name).name.casefold()!=TYPES_NAME.casefold()
                or item.size is not None and item.size>LIMIT):
            continue
        if identifies(manifest_xml(member_bytes(archive,item,LIMIT))):
            prefix = item.name.rpartition('/')[0]
            claimed.update(part.index for part in members if not prefix or part.name.startswith(prefix+'/'))
            publications.append((item,'PowerPoint presentation'+(f' ({PurePosixPath(prefix).name})' if prefix else '')))
    return publications,claimed


def qualified(node,local):
    return node is not None and node.tag in {'{'+ns+'}'+local for ns in PRESENTATION}


def child(node,local):
    return next((part for part in node if qualified(part,local)),None) if node is not None else None


def related_id(node,attribute='id'):
    return next((node.get('{'+ns+'}'+attribute) for ns in sorted(REL) if node.get('{'+ns+'}'+attribute)),None)


def nonvisual(shape,kind):
    return next((node for item in shape if name(item).startswith('nv') and (node:=child(item,kind)) is not None),None)


def placeholder(shape):
    return child(nonvisual(shape,'nvPr'),'ph')


def description(identity,kind):
    if identity is None:
        return ''
    desc,title = identity.get('descr'),identity.get('title')
    return visual_label(desc,title) if kind=='pic' else (desc or title or '').strip()


def where(node,number,shape=None,groups=()):
    location = {'slide':number,'xml_path':node.getroottree().getpath(node)}
    if shape:
        location['shape_id'] = shape
    if groups:
        location['shape_groups'] = list(groups)
    return location


class Presentation:
    def __init__(self,archive,manifest,members,sha,title,cache):
        from .pdf_visual import Observer

        root = manifest.name.rpartition('/')[0]
        self.archive = archive
        self.parts = Parts(root+'/' if root else '',members)
        self.builder = DocumentBuilder('pptx',title,sha)
        self.doc = self.builder.doc
        self.observer = Observer(cache/'presentation-observations' if cache else None)
        self.layouts = {}
        self.links = {}
        self.size = None
        self.resources = {item.index:member_location(sha,item,'package_member')
                          for options in self.parts.index.values() for item in options}

    def read(self,member,limit=MAX_STRUCTURE):
        return member_bytes(self.archive,member,limit)

    def links_for(self,member):
        if member not in self.links:
            self.links[member] = self.parts.relationships(member,lambda target:self.read(target,LIMIT))[1]
        return self.links[member]

    def relationships(self,member,kind,ident=None):
        types = {prefix+'/'+kind for prefix in REL}
        return [link for link in self.links_for(member) if link.kind in types and (ident is None or link.id==ident)]

    def target(self,member,kind,ident=None,*,required=False):
        if required and ident=='':
            raise Error('source_presentation_relationship',f'The source lacks a {kind} relationship identifier.')
        if ident is not None and sum(link.id==ident for link in self.links_for(member))>1:
            raise Error('source_presentation_relationship',f'The source repeats a {kind} relationship identifier.')
        links = self.relationships(member,kind,ident)
        if len(links)==1 and links[0].member is not None:
            return links[0].member
        if links or required:
            raise Error('source_presentation_relationship',f'The source has no unique local {kind} part.')
        return None

    def node(self,member,kind):
        node = xml(self.read(member))
        if not qualified(node,kind):
            raise Error('source_presentation_part',f'{member.name} is not a readable {kind} part.')
        return node

    def bind(self,item,member,number):
        if item:
            bind_member_block(item,member,number,self.doc.source_sha256,{},self.parts.resolve)
            self.resources[member.index]['status'] = 'represented'
        return item

    def add(self,text,role,member,location,*,issue=None):
        item = self.builder.add(text,role,member.name,loc=location,issue=issue)
        return self.bind(item,member,location.get('slide',0))

    def paragraph(self,node,text,spans,member,location,role='body'):
        place = location|{'xml_path':node.getroottree().getpath(node),'inline_semantics':spans}
        properties = drawing_text.child(node,'pPr')
        if properties is not None and properties.get('lvl') is not None:
            place['list_level'] = properties.get('lvl')
        return self.add(text,role,member,place,issue=uncertainty(spans))

    def layout(self,member):
        target = self.target(member,'slideLayout')
        if target is None:
            return {}
        if target not in self.layouts:
            entries = {}
            tree = child(child(self.node(target,'sldLayout'),'cSld'),'spTree')
            for element in tree if tree is not None else ():
                if (ph:=placeholder(element)) is not None:
                    entries.setdefault(ph.get('idx','0'),set()).add(ph.get('type','obj'))
            self.layouts[target] = {index:next(iter(types)) for index,types in entries.items() if len(types)==1}
        return self.layouts[target]

    def shapes(self,tree,groups=()):
        for shape in tree:
            kind = name(shape)
            if shape.tag==COMPATIBILITY+'AlternateContent':
                fallback = next((node for node in shape if node.tag==COMPATIBILITY+'Fallback'),None)
                if fallback is not None:
                    yield from self.shapes(fallback,groups)
                else:
                    yield shape,None,groups,'unresolved'
            elif kind in SHAPES and qualified(shape,kind):
                identity = nonvisual(shape,'cNvPr')
                if identity is not None and (identity.get('hidden') in drawing_text.TRUE or
                        any(node.get('val','1') in drawing_text.TRUE for node in identity.iter(DECORATIVE))):
                    continue
                yield shape,identity,groups,kind
                if kind=='grpSp':
                    label = (identity.get('id') if identity is not None else None) or shape.getroottree().getpath(shape)
                    yield from self.shapes(shape,(*groups,label))

    def picture(self,shape,member,location,label,raster_only,record):
        fill = child(shape,'blipFill')
        blip = drawing_text.child(fill,'blip')
        target = self.target(member,'image',(related_id(blip,'embed') or '') if blip is not None else '',
                             required=not label)
        if target is not None:
            asset = self.resources[target.index]
            asset['status'] = 'referenced'
            location['image_member'] = target.name
        if label:
            item = self.add(label,'caption',member,location)
            if item and target:
                item.links.append({'kind':'source_resource','label':target.name,
                                   'destinations':['source.html#resource-'+asset['location_id']]})
            return
        if not raster_only:
            raise Error('source_slide_image','A slide image needs an authored description or a verified visual reading.')
        transform = drawing_text.child(child(shape,'spPr'),'xfrm')
        if (transform is None or location.get('shape_groups')
                or any(transform.get(flag) in drawing_text.TRUE for flag in ('flipH','flipV')) or transform.get('rot','0')!='0'):
            raise Error('source_slide_image','A transformed slide image needs a verified visual reading.')
        properties = child(shape,'spPr')
        geometry = drawing_text.child(properties,'prstGeom')
        if (drawing_text.child(properties,'custGeom') is not None
                or geometry is not None and geometry.get('prst')!='rect'
                or drawing_text.child(fill,'tile') is not None):
            raise Error('source_slide_image','The image uses a mask or repeated fill that needs a verified visual reading.')
        offset,extent = (drawing_text.child(transform,kind) for kind in ('off','ext'))
        try:
            x,y,w,h = (int(node.get(field)) for node,field in ((offset,'x'),(offset,'y'),(extent,'cx'),(extent,'cy')))
        except (AttributeError,TypeError,ValueError):
            raise Error('source_slide_image','The embedded image has no valid slide rectangle.') from None
        if self.size is None or not (0<=x<x+w<=self.size[0] and 0<=y<y+h<=self.size[1]):
            raise Error('source_slide_image','An image clipped by the slide edges needs a verified visual reading.')
        location['slide_rectangle'] = [x,y,x+w,y+h]
        if blip is not None and any(drawing_text.is_drawing(effect) and name(effect)!='extLst' for effect in blip):
            raise Error('source_slide_image','An image with slide effects needs a verified visual reading.')
        crop = drawing_text.child(fill,'srcRect')
        try:
            bounds = [int(crop.get(side,'0'))/100000 for side in ('l','t','r','b')] if crop is not None else [0,0,0,0]
        except ValueError as error:
            raise Error('source_slide_image','The embedded slide image uses an invalid crop.') from error
        bounds[2:] = [1-value for value in bounds[2:]]
        entries,receipt = observe_picture(self.observer,self.read(target),location,bounds)
        receipt.update(member=target.name,archive_entry=target.index,shape_id=location.get('shape_id'))
        record.setdefault('visual_observations',[]).append(receipt)
        for row,mapped in entries:
            self.add(row['text'],row.get('role','body'),target,mapped,issue=row.get('issue'))
        if not entries:
            raise Error('source_slide_image','The embedded slide image has no verified readable text.')

    def frame(self,shape,member,location,label):
        from .presentation_charts import chart_grid, chart_name

        data = drawing_text.child(drawing_text.child(shape,'graphic'),'graphicData')
        table = drawing_text.child(data,'tbl')
        if table is not None:
            grid = drawing_text.table(table,location|{'member':member.name,'archive_entry':member.index})
            if label:
                grid['caption'] = table_grid.passage(label)
            return self.bind(self.builder.grid(grid,member.name,loc=location),member,location['slide'])
        chart = next((item for item in data if chart_name(item)=='chart'),None) if data is not None else None
        if chart is None:
            if label:
                return self.add(label,'caption',member,location)
            raise Error('source_slide_frame','The slide graphic needs an authored description or a verified reading.')
        target = self.target(member,'chart',related_id(chart) or '',required=True)
        chart_location = location|{'source_shape_member':member.name,'source_shape_path':location['xml_path']}
        try:
            root = xml(self.read(target))
            chart_location['xml_path'] = root.getroottree().getpath(root)
            grid = chart_grid(root,chart_location|{'member':target.name,'archive_entry':target.index})
        except Error as error:
            if label:
                self.add(label,'caption',member,location)
            return self.add('','unreadable',target,chart_location,issue=error.message)
        if label and label!=(grid.get('caption') or {}).get('source'):
            self.add(label,'caption',member,location)
        return self.bind(self.builder.grid(grid,target.name,loc=chart_location),
                         target,location['slide'])

    def notes(self,member,number,known):
        target = self.target(member,'notesSlide')
        if target is None:
            return None
        root = self.node(target,'notes')
        tree = child(child(root,'cSld'),'spTree')
        if tree is None:
            raise Error('source_presentation_notes','The notes part has no readable speaker-note body.')
        for item in tree:
            ph = placeholder(item)
            if ph is None or ph.get('type')!='body':
                continue
            for node,text,spans in drawing_text.paragraphs(child(item,'txBody')):
                role,place = speaker_note(text,where(node,number),known,slide_member=member.name)
                self.paragraph(node,text,spans,target,place,role)
        return target.name

    def slide(self,member,number):
        root = self.node(member,'sld')
        record = {'kind':'slide','slide':number,'member':member.name}
        self.doc.inventory.append(record)
        if root.get('show') in ('0','false','off'):
            record['status'] = 'hidden by source'
            self.resources[member.index]['status'] = 'hidden by source'
            try:
                target = self.target(member,'notesSlide')
                if target:
                    record['notes'] = target.name
                    self.resources[target.index]['status'] = 'hidden by source'
            except Error as error:
                record['notes_issue'] = error.message
            return
        tree = child(child(root,'cSld'),'spTree')
        if tree is None:
            raise Error('source_presentation_part','The slide has no readable shape tree.')
        layout_issue = None
        try:
            layout = self.layout(member)
        except Error as error:
            layout,layout_issue = {},error.message

        def ptype(shape):
            marker = placeholder(shape)
            return None if marker is None else marker.get('type') or layout.get(marker.get('idx','0'),'obj')

        shapes = list(self.shapes(tree))
        position = next((i for i,(shape,_,_,kind) in enumerate(shapes) if kind=='sp' and ptype(shape) in TITLES
                         and list(drawing_text.paragraphs(child(shape,'txBody')))),None)
        title_place = where(root,number)|{'derived_slide_label':True}
        title = f'Slide {number}'
        title_issue = None
        if position is not None:
            shape,identity,groups,_ = shapes[position]
            pieces = list(drawing_text.paragraphs(child(shape,'txBody')))
            passage = table_grid.join_passages((text,spans,[]) for _,text,spans in pieces)
            title = passage['source']
            title_place = where(shape,number,identity.get('id') if identity is not None else None,groups)
            title_place['inline_semantics'] = passage['spans']
            title_issue = uncertainty(passage['spans'])
        begin = len(self.doc.blocks)
        self.add(title,'heading',member,title_place,issue=title_issue)
        if layout_issue:
            self.add('','unreadable',member,where(root,number),issue=layout_issue)
        active = [entry for entry in shapes if entry[3]!='grpSp' and ptype(entry[0]) not in POSITIONAL]
        raster_only = len(active)==1 and active[0][3]=='pic'
        unlabelled = []
        for index,(shape,identity,groups,kind) in enumerate(shapes):
            if index==position or ptype(shape) in POSITIONAL:
                continue
            shape_id = identity.get('id') if identity is not None else None
            loc = where(shape,number,shape_id,groups)
            label = description(identity,kind)
            if kind=='grpSp' and not label:
                continue
            try:
                if kind=='graphicFrame':
                    self.frame(shape,member,loc,label)
                elif kind=='pic':
                    self.picture(shape,member,loc,label,raster_only,record)
                elif kind=='unresolved':
                    raise Error('source_slide_shape','The slide has no reading for this alternate content.')
                else:
                    paragraphs = list(drawing_text.paragraphs(child(shape,'txBody')))
                    if label and canonical(label) not in {canonical(text) for _,text,_ in paragraphs}:
                        self.add(label,'caption',member,loc)
                    elif kind in ('sp','cxnSp') and not paragraphs and not label and ptype(shape) is None:
                        unlabelled.append(loc)
                    for node,text,spans in paragraphs:
                        self.paragraph(node,text,spans,member,loc)
            except Error as error:
                self.add('','unreadable',member,loc,issue=error.message)
        known = {canonical(item.source):item.id for item in self.doc.blocks[begin:] if item.source and not item.issue}
        try:
            record['notes'] = self.notes(member,number,known)
        except Error as error:
            self.add('','unreadable',member,where(root,number),issue=error.message)
        if unlabelled:
            record['unlabelled_shapes'] = unlabelled
            if len(self.doc.blocks[begin:])==1 and position is None:
                self.add('','unreadable',member,unlabelled[0],
                         issue='The slide contains graphics without authored words or descriptions.')
        record['status'] = 'needs_review' if any(block.issue for block in self.doc.blocks[begin:]) else 'represented'

    def read_deck(self):
        member = self.target(None,'officeDocument',required=True)
        root = self.node(member,'presentation')
        metadata = [link.member for link in self.links_for(None) if link.kind==CORE_PROPERTIES and link.member]
        if len(metadata)==1:
            try:
                title = xml(self.read(metadata[0],LIMIT)).findtext('{http://purl.org/dc/elements/1.1/}title','').strip()
            except Error:
                title = ''
            if title:
                self.doc.title = title
        if (size:=child(root,'sldSz')) is not None:
            try:
                self.size = int(size.get('cx')),int(size.get('cy'))
            except (TypeError,ValueError):
                pass
        ordered = child(root,'sldIdLst')
        if ordered is None or not len(ordered):
            raise Error('source_presentation_part','The presentation has no declared slides.')
        for number,entry in enumerate((item for item in ordered if qualified(item,'sldId')),1):
            if number>10000:
                raise Error('source_structure_size','The presentation exceeds its slide budget.')
            part = member
            try:
                part = self.target(member,'slide',related_id(entry) or '',required=True)
                self.slide(part,number)
            except Error as error:
                self.add('','unreadable',part,{'slide':number,'slide_reference':related_id(entry)},issue=error.message)
        self.doc.metadata['source_resources'] = list(self.resources.values())
        return self.doc


def read_pptx(path,source,sha,title,cache):
    kind = 'zip' if source.kind=='file' else source.kind
    with containers(path,kind) as archive:
        members = list(archive.members())
        editions,_ = archive_books(archive,members)
        matching = [item for item,_ in editions if source.entry is None or item.index==source.entry]
        if len(matching)!=1:
            raise Error('source_presentation_part','The archive has no unique PowerPoint package manifest.')
        doc = Presentation(archive,matching[0],members,sha,title,cache).read_deck()
        if source.kind=='file':
            doc.metadata['source_archive_kind'] = kind
        return doc
