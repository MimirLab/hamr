"""Read visible PageMaker stories through the common annotated markup reader."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
import re

from lxml import etree

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read
from .pagemaker_binary import FONTS, FRAMES, LINES, PAGE, RUNS, SHAPES, STORIES, Publication
from .xml_projection import append_text

BREAKS = re.compile(rb'\r\n|[\r\n]')
DISCRETIONARY = {0x15:None,0x16:None,0x17:None}
CONTROLS = {**dict.fromkeys(range(32),' '),9:'\t',4:'•',5:'–',6:'—',
            0x19:'\u2003',0x1a:'\u2002',0x1b:'\u2009',**DISCRETIONARY}
GLYPH_FONTS = {'ml-karthika','ml-ttaathira','ml-ttrevathi','symbol','wingdings','webdings'}
STYLE = ((0x001,'strong'),(0x002,'em'),(0x200,'sup'),(0x400,'sub'))
FIGURES = {6,10,11}


@dataclass(frozen=True)
class Placement:
    page: int
    offset: int
    story: int
    frame: int
    top: int
    left: int

    def order(self):
        return self.page,self.top,self.left,self.offset


@dataclass(frozen=True)
class Slice:
    ident: int
    start: int
    end: int
    source: int
    placement: Placement | None


def indexes(publication):
    stories = defaultdict(list)
    for row in publication.records(STORIES):
        stories[publication.u32(row+32)].append(row)
    placements = defaultdict(list)
    figures = []
    for page,row in enumerate(publication.records(PAGE),1):
        shapes = publication.u16(row+2)
        if shapes>=publication.slot_count and shapes!=0xffff:
            raise Error('source_pagemaker_pages',f'Saved PageMaker page {page} has an unresolved shape array.',
                        'Open the complete original publication or export a readable PDF.')
        for shape in publication.records(SHAPES,shapes):
            kind = publication.data[shape]
            a,b,_,_ = publication.point.unpack_from(publication.data,shape+6)
            left,top = (a,b) if publication.order=='<' else (b,a)
            if kind==1:
                story,frame = publication.u32(shape+32),publication.u32(shape+36)
                placements[story].append(Placement(page,shape,story,frame,top,left))
            elif kind in FIGURES:
                figures.append((page,top,left,shape))
    return stories,placements,figures


def ranges(publication,row,placed):
    frames = list(publication.records(FRAMES,publication.u16(row)))
    lines = iter(publication.records(LINES,publication.u16(row+2)))
    positions = defaultdict(list)
    for frame in placed:
        positions[frame.frame].append(frame)
    offset = 0
    unique = set()
    result = []
    for frame in frames:
        ident,length = publication.u32(frame+20),publication.u32(frame)
        line_count = publication.u16(frame+4)
        spans = []
        for _ in range(line_count):
            current = next(lines,None)
            if current is None:
                return None
            spans.append(publication.u16(current))
        if ident in unique or length>MAX_STRUCTURE:
            return None
        # Some overflowing stories store additional lines in their final frame.
        if sum(spans)<length or not spans and length:
            return None
        unique.add(ident)
        owners = positions[ident]
        result.append(Slice(ident,offset,offset+length,frame,min(owners,key=Placement.order) if owners else None))
        offset += length
    if not frames or next(lines,None) is not None or set(positions)-unique:
        return None
    return result


def backed_ranges(frames):
    selected = []
    for frame in frames:
        if frame.placement is None or frame.start==frame.end:
            continue
        if selected and selected[-1][-1].end==frame.start:
            selected[-1].append(frame)
        else:
            selected.append([frame])
    return selected


def paragraphs(data,start,end):
    position = start
    for boundary in BREAKS.finditer(data,start,end):
        yield position,boundary.start()
        position = boundary.end()
    if position<end:
        yield position,end


class Projection:
    def __init__(self,publication,part):
        self.pub,self.part = publication,part
        self.annotations = {}
        self.remaining = MAX_STRUCTURE
        self.encoding = 'cp1252' if publication.order=='<' else 'mac_roman'
        self.fonts = [bytes(publication.extent(font,64)).split(b'\0',1)[0].decode(self.encoding,'replace')
                      for font in publication.records(FONTS)]

    def element(self,tag,origin,**metadata):
        markup = etree.Element(tag)
        label = str(len(self.annotations)+1)
        markup.set('data-hamr-node',label)
        self.annotations[label] = {'source_file':self.part,'source_element':f'{self.part}:PageMaker@{origin}',
                                   'stream_byte_offset':origin,**metadata}
        return markup

    def issue(self,target,origin,text,*,page=None,visible='',**metadata):
        locator = {'source_file':self.part,'stream_byte_offset':origin,**metadata}
        node = self.element('span',origin,source_issue=text,pagemaker_page=page,
                            reading_span={'kind':'source_review','source':visible,'issue':text,'source_locator':locator},**metadata)
        node.text = visible or None
        target.append(node)

    def story(self,builder,target,story,offsets,placements):
        primary = min(placements,key=Placement.order)
        origin = primary.offset
        details = {'pagemaker_story':story}
        if len(offsets)!=1:
            self.issue(target,origin,'This PageMaker text box has no unique saved story.',page=primary.page,**details)
            return
        row = offsets[0]
        frames = ranges(self.pub,row,placements)
        data,segments = self.pub.story_bytes(self.pub.u16(row+4),self.remaining)
        self.remaining -= len(data)
        if not frames or frames[-1].end>len(data):
            self.issue(target,origin,'This PageMaker story has no verified set of saved frame ranges.',page=primary.page,**details)
            return
        scopes = backed_ranges(frames)
        builder.doc.inventory.append({'kind':'pagemaker_story','story':story,'stream_byte_offset':row,
                                      'stored_bytes':len(data),'frames':[
                                          {'frame':frame.ident,'byte_range':[frame.start,frame.end],
                                           'page':frame.placement.page if frame.placement else None} for frame in frames]})
        runs = []
        position = 0
        for run in self.pub.records(RUNS,self.pub.u16(row+6)):
            length,index,flags = self.pub.u16(run),self.pub.u16(run+2),self.pub.u16(run+10)
            name = self.fonts[index] if index<len(self.fonts) else ''
            if length:
                runs.append((position,position+length,name,flags))
            position += length
        offset_map = []
        position = 0
        for fragment in segments:
            offset_map.append((position,position+fragment.count,fragment.offset))
            position += fragment.count
        run_ends = [stop for _,stop,_,_ in runs]
        fragment_ends = [stop for _,stop,_ in offset_map]
        last_end = None
        for scope in scopes:
            begin,end = scope[0].start,scope[-1].end
            if last_end is not None and data[last_end:begin].strip(b' \t\r\n\0'):
                self.issue(target,scope[0].placement.offset,
                           'A PageMaker story crosses an unplaced frame with saved text.',
                           page=scope[0].placement.page,pagemaker_story_range=[last_end,begin],**details)
            last_end = end
            frame_ends = [frame.end for frame in scope]
            for left,right in paragraphs(data,begin,end):
                if left==right:
                    continue
                frame_index = bisect_right(frame_ends,left)
                owner = scope[frame_index].placement
                pages = set()
                for i in range(frame_index,len(scope)):
                    frame = scope[i]
                    if frame.start>=right:
                        break
                    pages.add(frame.placement.page)
                locations = []
                for i in range(bisect_right(fragment_ends,left),len(offset_map)):
                    start,stop,physical = offset_map[i]
                    if start>=right:
                        break
                    locations.append([physical+max(start,left)-start,physical+min(stop,right)-start])
                paragraph = self.element('p',owner.offset,pagemaker_pages=sorted(pages),
                                         pagemaker_story_range=[left,right],pagemaker_byte_ranges=locations,**details)
                cursor = left
                for i in range(bisect_right(run_ends,left),len(runs)):
                    start,stop,font,flags = runs[i]
                    if start>=right:
                        break
                    self.span(paragraph,data[cursor:min(start,right)],owner.offset,'',0)
                    self.span(paragraph,data[max(left,start):min(right,stop)],owner.offset,font,flags)
                    cursor = min(right,stop)
                self.span(paragraph,data[cursor:right],owner.offset,'',0)
                if len(paragraph) or (paragraph.text or '').strip():
                    target.append(paragraph)
        if frames[-1].end<len(data) and data[frames[-1].end:].strip(b' \t\r\n\0'):
            builder.doc.inventory.append({'kind':'pagemaker_overset','story':story,
                                          'byte_range':[frames[-1].end,len(data)],'stream_byte_offset':row})

    def span(self,output,raw,origin,font,flags):
        if not raw:
            return
        visible = raw.decode(self.encoding,'replace').translate(CONTROLS)
        if font.casefold() in GLYPH_FONTS:
            self.issue(output,origin,'A PageMaker text run requires a verified legacy font mapping.',
                       visible=visible,pagemaker_font=font)
        elif '\ufffd' in visible or b'\x18' in raw:
            self.issue(output,origin,'A PageMaker text run has a character or automatic folio that needs a verified reading.',
                       visible=visible)
        else:
            for mask,tag in STYLE:
                if flags&mask:
                    output = etree.SubElement(output,tag)
            append_text(output,visible)


def read_pagemaker(path,source,sha,title):
    with path.open('rb') as stream:
        publication = Publication(stream.read(MAX_STRUCTURE+1))
    part = source.member_name or path.name
    builder = DocumentBuilder('pagemaker',title,sha)
    builder.doc.metadata['pagemaker_record_version'] = f'0x{publication.version:04x}'
    if not publication.arrays[PAGE]:
        raise Error('source_pagemaker_pages','The PageMaker source has no saved body pages.',
                    'Open a publication containing placed text, or export a readable PDF.')
    stories,placements,figures = indexes(publication)
    projector = Projection(publication,part)
    target = etree.Element('div')
    pending = [(min(boxes,key=Placement.order).order(),story,boxes) for story,boxes in placements.items()]
    pending.extend((placement,None,[]) for placement in figures)
    for order,story,boxes in sorted(pending,key=lambda item:item[0]):
        if story is None:
            projector.issue(target,order[-1],'This PageMaker illustration has no saved accessible description.',page=order[0])
        else:
            projector.story(builder,target,story,stories[story],boxes)
    html_read(etree.tostring(target,method='html',encoding='unicode'),builder,where=part,metadata=False,
              source_annotations=projector.annotations)
    return builder.doc
