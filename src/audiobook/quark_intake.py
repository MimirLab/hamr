"""Project QuarkXPress text stories through shared narration preparation."""

from bisect import bisect_right
from collections import defaultdict
import re

from lxml import etree

from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, html_read
from .quark_binary import VARIANTS, Publication
from .xml_projection import append_text

PARAGRAPH_END = re.compile(rb'\r\n|[\r\n]')
CONTROL_CHARACTERS = {**dict.fromkeys(range(32),' '),7:'\n',9:'\t',16:'\xa0',30:None}
EMPHASIS = ((1,'strong'),(2,'em'),(0x20,'sup'),(0x40,'sub'))
DINGBATS = {'symbol','wingdings','webdings','zapf dingbats'}
GRID_FONTS = {'tfcrossword'}


def thread(head,continuations,length):
    frames = [head]
    seen = set()
    current = head
    while current.successor:
        matches = continuations.get((head.link,current.successor),())
        if len(matches)!=1:
            return None
        current = matches[0]
        if current.offset in seen or not frames[-1].start<current.start<=length:
            return None
        seen.add(current.offset)
        frames.append(current)
    return frames


def para_ranges(text):
    start = 0
    for boundary in PARAGRAPH_END.finditer(text):
        yield start,boundary.start()
        start = boundary.end()
    if start<len(text):
        yield start,len(text)


class Projection:
    def __init__(self,publication,part):
        self.pub,self.part = publication,part
        self.annotations = {}

    def location(self,address,**details):
        return {'source_file':self.part,'source_element':f'{self.part}:Quark@{address}',
                'quark_main_chain_offset':address,**details}

    def element(self,tag,address,**details):
        node = etree.Element(tag)
        label = str(len(self.annotations)+1)
        node.set('data-hamr-node',label)
        self.annotations[label] = self.location(address,**details)
        return node

    def read_issue(self,target,address,reason,**details):
        target.append(self.element('span',address,source_issue=reason,**details))

    def write_text(self,parent,raw,address,font='',flags=0):
        if not raw:
            return
        text = raw.decode(self.pub.encoding,'replace').translate(CONTROL_CHARACTERS)
        if font.casefold() in DINGBATS or '\ufffd' in text or any(char in raw for char in (2,3,4)):
            reason = 'This QuarkXPress text run needs a verified symbol or folio reading.'
            visible = self.element('span',address,reading_span={'kind':'source_review','source':text,'issue':reason,
                                   'source_locator':self.location(address,quark_font=font)})
            visible.text = text
            parent.append(visible)
            return
        for mask,tag in EMPHASIS:
            if flags&mask:
                parent = etree.SubElement(parent,tag)
        for i,line in enumerate(text.split('\n')):
            if i:
                etree.SubElement(parent,'br')
            append_text(parent,line)

    def story(self,builder,target,head,continuations,matched):
        try:
            story = self.pub.story(head.ref)
        except Error as error:
            if error.code=='source_structure_size':
                raise
            self.read_issue(target,head.offset,error.message,quark_page=head.page,quark_story_block=head.ref)
            return
        frames = thread(head,continuations,len(story.data))
        if frames is None:
            self.read_issue(target,head.offset,'This QuarkXPress story has an unresolved linked frame.',
                            quark_page=head.page,quark_story_block=head.ref)
            return
        seen = {frame.offset for frame in frames[1:]}
        if seen&matched:
            self.read_issue(target,head.offset,'Multiple QuarkXPress stories claim the same continuation.',
                            quark_page=head.page,quark_story_block=head.ref)
            return
        matched.update(seen)
        boundaries = [frame.start for frame in frames]
        builder.doc.inventory.append({'kind':'quark_story','story_block':head.ref,'stored_bytes':len(story.data),
                                      'frames':[{'page':frame.page,'main_chain_offset':frame.offset,
                                                 'range':[frame.start,end]}
                                                for frame,end in zip(frames,boundaries[1:]+[len(story.data)],strict=True)]})
        formats = []
        for begin,end,index in story.formats:
            font,flags = self.pub.styles[index] if index<len(self.pub.styles) else (None,0)
            formats.append((begin,end,self.pub.fonts.get(font,''),flags))
        run_ends = [end for _,end,_,_ in formats]
        piece_ends = [end for _,end,_ in story.pieces]
        for start,end in para_ranges(story.data):
            if start==end:
                continue
            first = bisect_right(boundaries,start)-1
            last = bisect_right(boundaries,end-1)-1
            pages = list(dict.fromkeys(frame.page for frame in frames[first:last+1]))
            ranges = []
            for index in range(bisect_right(piece_ends,start),len(story.pieces)):
                begin,stop,offset = story.pieces[index]
                if begin>=end:
                    break
                ranges.append([offset+max(start,begin)-begin,offset+min(end,stop)-begin])
            address = frames[first].offset
            paragraph = self.element('p',address,quark_pages=pages,quark_story_block=head.ref,
                                     quark_story_range=[start,end],quark_byte_ranges=ranges)
            position = start
            fonts = set()
            for index in range(bisect_right(run_ends,start),len(formats)):
                begin,stop,font,flags = formats[index]
                if begin>=end:
                    break
                if position<begin:
                    fonts.add('')
                    self.write_text(paragraph,story.data[position:begin],address)
                fonts.add(font.casefold())
                position = min(stop,end)
                self.write_text(paragraph,story.data[max(begin,start):position],address,font,flags)
            if position<end:
                fonts.add('')
                self.write_text(paragraph,story.data[position:end],address)
            if fonts and fonts<=GRID_FONTS:
                self.annotations[paragraph.get('data-hamr-node')]['source_control'] = True
            if len(paragraph) or (paragraph.text or '').strip():
                target.append(paragraph)


def read_quark(path,source,sha,title):
    with path.open('rb') as stream:
        publication = Publication(stream.read(MAX_STRUCTURE+1))
    part = source.member_name or path.name
    builder = DocumentBuilder('quark',title,sha)
    builder.doc.metadata.update(quark_version=VARIANTS[publication.version],quark_version_word=publication.version,
                                quark_pages=publication.physical_pages,quark_byte_order=publication.order)
    projector = Projection(publication,part)
    html = etree.Element('div')
    pending = []
    continuations = defaultdict(list)
    for frame in publication.placements:
        if frame.start:
            continuations[frame.link,frame.ref].append(frame)
        else:
            pending.append((frame.order,frame))
    pending.extend((figure,None) for figure in publication.figures)
    matched = set()
    for properties,frame in sorted(pending,key=lambda item:item[0]):
        if frame is None:
            projector.read_issue(html,properties[-1],'This QuarkXPress illustration has no saved accessible description.',
                                 quark_page=properties[0])
        else:
            projector.story(builder,html,frame,continuations,matched)
    for frames in continuations.values():
        for frame in frames:
            if frame.offset not in matched:
                projector.read_issue(html,frame.offset,'This QuarkXPress continuation has no verified story placement.',
                                     quark_page=frame.page)
    html_read(etree.tostring(html,method='html',encoding='unicode'),builder,where=part,metadata=False,
              source_annotations=projector.annotations)
    return builder.doc
