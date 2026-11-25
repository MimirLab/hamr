"""Discover IDML publications and follow their declared reading structure."""

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from pathlib import PurePosixPath
from posixpath import dirname
from urllib.parse import urlsplit

from lxml import etree

from .archive_intake import containers
from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read, name, xml, xml_start
from .indesign_intake import FRAME, GRAPHICS, PACKAGE, SHAPES, StoryProjection, Styles, story_nodes
from .publication_package import bind_member_block, local_target, member_bytes, member_location

MIME = b'application/vnd.adobe.indesign-idml-package'
DESIGN = 'designmap.xml'
IDENTITY = (1,0,0,1,0,0)


def design_root(node):
    return node is not None and node.tag=='Document' and PACKAGE[1:-1] in node.nsmap.values()


def folder_marker(path):
    marker = path/DESIGN
    if marker.is_symlink() or not marker.is_file():
        return False
    with marker.open('rb') as stream:
        return design_root(xml_start(stream))


class Edition:
    def __init__(self,manifest,members,root):
        self.manifest = manifest
        self.root = root
        directory = dirname(manifest.name)
        self.prefix = directory+'/' if directory else ''
        self.members = defaultdict(list)
        for member in members:
            if member.name.startswith(self.prefix):
                self.members[member.name[len(self.prefix):]].append(member)
        self.resources = {manifest.name: 'manifest'}
        self.declarations = defaultdict(list)
        self.missing = []
        for entry in root:
            if not (isinstance(entry.tag,str) and entry.tag.startswith(PACKAGE) and entry.get('src')):
                continue
            kind,address = name(entry),entry.get('src')
            item = self.part(address)
            if item is None:
                if kind in ('Spread','Story','Styles'):
                    self.missing.append(f'The declared InDesign {kind.lower()} is missing or ambiguous: {address}.')
                continue
            self.declarations[kind].append(item)
            self.resources[item.name] = 'text' if kind=='Story' else 'package'
        for filename,choices in self.members.items():
            if len(choices)!=1:
                continue
            if filename in ('mimetype','META-INF/container.xml'):
                self.resources[choices[0].name] = 'manifest'
            elif filename.startswith(('Links/','META-INF/')):
                self.resources[choices[0].name] = 'asset' if filename.startswith('Links/') else 'package'

    def part(self,address):
        target = local_target(address,DESIGN)
        if target and len(choices:=self.members.get(target[0],()))==1:
            return choices[0]

    def graphic(self,address):
        try:
            uri = urlsplit(address.replace('\\','/'))
        except ValueError:
            return None
        if uri.netloc or uri.query or uri.fragment or uri.scheme not in ('','file') or uri.path.startswith('/'):
            return None
        if (item:=self.part(uri.path)) is not None:
            self.resources[item.name] = 'asset'
            return item.name


def identify(archive,manifest,members):
    with archive.open_member(manifest) as stream:
        if not design_root(xml_start(stream)):
            return None
    root = xml(member_bytes(archive,manifest,MAX_STRUCTURE))
    return Edition(manifest,members,root)


def archive_books(archive,members):
    editions = []
    claimed = set()
    for manifest in members:
        if PurePosixPath(manifest.name).name!=DESIGN:
            continue
        if (edition:=identify(archive,manifest,members)) is not None:
            editions.append((manifest,edition.root.get('Name') or dirname(manifest.name) or 'InDesign publication'))
            claimed.update(item.index for item in members if item.name in edition.resources)
    return editions,claimed


def numbers(value,count):
    try:
        result = tuple(map(float,(value or '').split()))
    except ValueError:
        return None
    return result if len(result)==count and all(map(isfinite,result)) else None


def transformed(node,stop,x,y):
    while node is not None and node is not stop:
        a,b,c,d,dx,dy = numbers(node.get('ItemTransform'),6) or IDENTITY
        x,y = a*x+c*y+dx,b*x+d*y+dy
        node = node.getparent()
    return x,y


def bounds(node,spread):
    if name(node)=='TextPath':
        node = node.getparent()
    if geometry:=numbers(node.get('GeometricBounds'),4):
        top,left,bottom,right = geometry
        points = [(x,y) for x in (left,right) for y in (top,bottom)]
    else:
        points = [position for item in node.findall('./Properties/PathGeometry//PathPointType')
                  if (position:=numbers(item.get('Anchor'),2))]
    if points:
        xs,ys = zip(*(transformed(node,spread,x,y) for x,y in points))
        return min(xs),min(ys),max(xs),max(ys)


def overlap(a,b):
    # A horizontal or vertical text path can have no area, yet still
    # intersect a page along its baseline.
    def dimension(start,end,lower,upper):
        if start==end:
            return int(lower<=start<=upper)
        return max(0,min(end,upper)-max(start,lower))

    return dimension(a[0],a[2],b[0],b[2])*dimension(a[1],a[3],b[1],b[3])


@dataclass
class Placement:
    node: object
    member: str
    page: str | None
    order: tuple
    status: str

    @property
    def story(self):
        return self.node.get('ParentStory')

    def evidence(self):
        return {'part':self.member,'frame':self.node.get('Self'),'page':self.page,
                'previous_frame':self.node.get('PreviousTextFrame'),'next_frame':self.node.get('NextTextFrame'),
                'status':self.status}


def placements(root,member,hidden_layers):
    spread = root.find('Spread')
    if spread is None:
        spread = root.find('MasterSpread')
    if spread is None:
        return {}
    pages = [(page.get('Name'),rectangle) for page in spread.findall('Page') if (rectangle:=bounds(page,spread))]
    result = {}
    for serial,node in enumerate(spread.iter()):
        kind = name(node)
        is_frame = kind in FRAME and node.get('ParentStory') not in (None,'n')
        is_figure = kind in SHAPES-{'Group'} and any(name(child) in GRAPHICS for child in node)
        if not (is_frame or is_figure):
            continue
        rectangle = bounds(node,spread)
        hidden = any(part.get('Visible')=='false' or part.get('ItemLayer') in hidden_layers
                     for part in (node,*node.iterancestors()) if part is not spread)
        page = None
        index = len(pages)
        status = 'hidden' if hidden else 'visible'
        if rectangle and pages:
            index,area = max(((i,overlap(rectangle,rect)) for i,(_,rect) in enumerate(pages)),key=lambda pair:pair[1])
            if area>0:
                page = pages[index][0]
            elif status=='visible':
                status = 'pasteboard'
        x,y = rectangle[:2] if rectangle else (0,float('inf'))
        placement = Placement(node,member,page,(index,round(y,1),x,serial),status)
        result[node] = placement
    return result


def read_idml(path,source,sha,title):
    with containers(path,'zip' if source.kind=='file' else source.kind) as archive:
        members = list(archive.members())
        editions = [edition for item in members if PurePosixPath(item.name).name==DESIGN
                    and (source.entry is None or source.entry==item.index)
                    and (edition:=identify(archive,item,members)) is not None]
        if len(editions)!=1:
            raise Error('source_idml_package','Select an IDML publication with one InDesign design map.')
        return read_edition(archive,editions[0],sha,title)


def read_edition(archive,edition,sha,title):
    root = edition.root
    builder = DocumentBuilder('idml',root.get('Name') or title,sha)
    doc = builder.doc

    def read(member):
        try:
            return xml(member_bytes(archive,member,MAX_STRUCTURE))
        except Error as error:
            builder.issue(f'Could not read {member.name}. {error}',member.name)
            return None

    loaded = defaultdict(list)
    inventory = []
    for member in edition.declarations['Story']:
        stored = read(member)
        if stored is None:
            continue
        stories = story_nodes(stored)
        for node in stories:
            if ident:=node.get('Self'):
                loaded[ident].append(member)
        if not stories:
            builder.issue(f'This declared IDML story part has no story: {member.name}.',member.name)
    styles = Styles(read(parts[0]) if (parts:=edition.declarations['Styles']) else None)
    for issue in edition.missing:
        builder.issue(issue,edition.manifest.name)

    indexed = defaultdict(list)
    placement_groups = []
    hidden = {ident for layer in root.findall('Layer') if layer.get('Visible')=='false' and (ident:=layer.get('Self'))}
    encountered = set()
    for part_type in ('Spread','MasterSpread'):
        for member in edition.declarations[part_type]:
            stored = read(member)
            if stored is None:
                continue
            places = placements(stored,member.name,hidden)
            for node,placement in places.items():
                if placement.story:
                    encountered.add(placement.story)
                inventory.append({'kind':'idml_placement',**placement.evidence(),'story':placement.story,
                                  'status':'master' if part_type=='MasterSpread' else placement.status})
                if part_type=='Spread':
                    for item in (node,*node.iterancestors('Group')):
                        if ident:=item.get('Self'):
                            indexed[ident].append(placement)
            if part_type=='Spread':
                placement_groups.append(places)

    used = set()
    layout_by_story = defaultdict(list)
    for places in placement_groups:
        for place in places.values():
            if place.story:
                layout_by_story[place.story].append(place.evidence())
    frames_by_part = defaultdict(list)
    for ident,choices in loaded.items():
        for member in choices:
            frames_by_part[member.name].extend(layout_by_story[ident])

    def find_story(ident):
        if ident in used:
            return ()
        choices = loaded.get(ident,())
        if len(choices)!=1:
            return None
        member = choices[0]
        for node in story_nodes(read(member)):
            if node.get('Self')==ident:
                used.add(ident)
                return member.name,node
        return None

    occurrence = 0
    seen_graphics = set()

    def add_story(ident,context):
        nonlocal occurrence
        resolved = find_story(ident)
        if resolved is None:
            builder.issue(f'The placed InDesign story {ident} is missing or ambiguous.',context)
        elif resolved:
            member,node = resolved
            occurrence += 1
            start = len(doc.blocks)
            StoryProjection(node,member,styles,find_story,edition.graphic).write(node,builder)
            for block in doc.blocks[start:]:
                containing = block.locator.get('source_file',member)
                block.locator['idml_origin'] = context
                block.locator['idml_frames'] = frames_by_part[containing]
                block.locator['idml_occurrence'] = occurrence

    def add_placement(place):
        if place.story:
            add_story(place.story,place.member)
        elif place.node not in seen_graphics:
            seen_graphics.add(place.node)
            projector = StoryProjection(place.node,place.member,styles,resolve_asset=edition.graphic)
            visual = projector.figure(place.node)
            if visual is not None:
                body = etree.Element('div')
                body.append(visual)
                html_read(etree.tostring(body,method='html',encoding='unicode'),builder,where=place.member,
                          metadata=False,collect_resources=True,source_annotations=projector.annotations)

    for article in root.findall('Article'):
        if article.get('ArticleExportStatus','true')=='false':
            continue
        for entry in article.findall('ArticleMember'):
            ident = entry.get('ItemRef')
            if ident not in indexed:
                # Non-text page items can legitimately occur in an article.
                inventory.append({'kind':'idml_article_member','item':ident,'status':'no readable placement'})
                continue
            for place in indexed[ident]:
                if place.status!='hidden':
                    add_placement(place)
    for places in placement_groups:
        for place in sorted(places.values(),key=lambda item:item.order):
            if place.status=='visible':
                add_placement(place)

    # Without a placed layout, the saved story order is the only local order.
    order = list(dict.fromkeys(root.get('StoryList','').split()+list(loaded)))
    if not placement_groups:
        for ident in order:
            if ident in loaded:
                add_story(ident,edition.manifest.name)
    for ident in order:
        if ident in loaded and ident not in used:
            inventory.append({'kind':'idml_story','story':ident,
                              'status':'not in visible reading order' if ident in encountered else 'unplaced'})
    if not doc.blocks and loaded:
        builder.issue('The saved InDesign layout has stories but no readable placement on its pages.',
                      edition.manifest.name)

    by_name = {member.name:member for choices in edition.members.values() if len(choices)==1 for member in choices}
    records = {filename:{**member_location(sha,by_name[filename],role),'status':'preserved'}
               for filename,role in edition.resources.items() if filename in by_name}
    for serial,block in enumerate(doc.blocks,1):
        filename = block.locator.get('source_file',block.locator['element'])
        if member:=by_name.get(filename):
            bind_member_block(block,member,serial,sha,records,lambda _part,reference:reference)
    doc.inventory.extend(inventory)
    doc.inventory.extend(records.values())
    doc.metadata['source_resources'] = list(records.values())
    return doc
