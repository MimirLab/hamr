"""Bind DAISY playback order to exact ranges in the shared DTBook reading."""

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
import re
from urllib.parse import unquote, urlsplit

from .archive_intake import Member
from .core import Block, Error, key
from .document_intake import DocumentBuilder, MAX_STRUCTURE, name, xml
from .dtbook_intake import read_dtbook
from .opf_intake import DTBOOK, target
from .publication_package import member_bytes
from .source_math import MATH_SPANS

SMIL_TYPES = frozenset(('application/smil','application/smil+xml'))
PUNCTUATION = re.compile(r'''[\s.,!?;:()[\]“”‘’"'–—-]*''')
STRUCTURAL_FIELDS = {'ordinal','chapter_id','element','location_id','inline_semantics'}
ATOMIC_SPANS = frozenset((*MATH_SPANS,'pronunciation_hint','note_reference','object_label'))


@dataclass
class Passage:
    member: Member
    block: Block
    start: int
    end: int
    cues: list[dict] = field(default_factory=list)

    def extend(self,other):
        if (self.member!=other.member or self.block is not other.block or self.end>other.start or
                other.end<=self.end or not PUNCTUATION.fullmatch(self.block.source[self.end:other.start])):
            return False
        self.end = other.end
        self.cues.extend(other.cues)
        return True


def anchored_blocks(doc):
    result = defaultdict(list)
    for block in doc.blocks:
        locations = {(span['id'],span['source_start'],span['source_end'])
                     for span in block.locator.get('inline_semantics',()) if span['kind']=='anchor'}
        if anchor:=block.locator.get('anchor'):
            locations.add((anchor,0,len(block.source)))
        for anchor,start,end in sorted(locations):
            result[anchor].append((block,start,end))
    return result


def projected(passage,builder):
    block,start,end = passage.block,passage.start,passage.end
    if PUNCTUATION.fullmatch(block.source[:start]):
        start = 0
    if PUNCTUATION.fullmatch(block.source[end:]):
        end = len(block.source)
    raw = block.source[start:end]
    start += len(raw)-len(raw.lstrip())
    end = max(start,end-(len(raw)-len(raw.rstrip())))
    locator = {k:v for k,v in block.locator.items() if k not in STRUCTURAL_FIELDS}
    spans = []
    issue = block.issue
    for span in block.locator.get('inline_semantics',()):
        a,b = span['source_start'],span['source_end']
        outside = not start<=a<=end if a==b else b<=start or a>=end
        if outside:
            continue
        if span['kind'] in ATOMIC_SPANS and (a<start or b>end):
            issue = issue or 'A DAISY text cue cuts through an authored note, image, or equation.'
        left,right = max(a,start),min(b,end)
        spans.append({**span,'source_start':left-start,'source_end':right-start,'source':block.source[left:right]})
    links = []
    for link in block.links:
        offset = link.get('source_start')
        if offset is not None and not start<=offset<=end:
            continue
        links.append({**link,**({'source_start':offset-start} if offset is not None else {})})
    locator.update(inline_semantics=spans,source_range=[start,end],playback_cues=passage.cues)
    return builder.add(block.source[start:end],block.role,block.locator['element'],loc=locator,links=links,issue=issue)


def read_daisy3(archive,package,descriptor,by_name,builder,records,scoped_issue,bind_reading):
    if not any(resource.media==DTBOOK for resource in package.resources()):
        raise Error('daisy_audio_only','This DAISY edition has no DTBook text for a complete narrated reading.',
                    'Open the text-bearing edition, or play the original DAISY audio.')
    if not package.spine:
        scoped_issue('The DAISY package has no authored SMIL order.',1)
        return
    cached,notes,matched = OrderedDict(),{},{}
    pending = None
    sequence = 0

    def flush():
        nonlocal pending,sequence
        if pending is None:
            return
        if block:=projected(pending,builder):
            sequence += 1
            bind_reading(block,pending.member,sequence)
            matched.setdefault((pending.member.index,pending.block.id),block.id)
        pending = None

    def issue(smil,node,problem):
        flush()
        where = smil.name+':'+node.getroottree().getpath(node) if node is not None else smil.name
        record = builder.issue(problem,where)
        if node is not None:
            record.locator['lines'] = [node.sourceline,node.sourceline]
        record.locator.update(member=smil.name,archive_entry=smil.index,
                              location_id=key([builder.doc.source_sha256,smil.index,record.id])[:20])

    for ordinal,ident in enumerate(package.spine,1):
        items = package.manifest.get(ident,())
        choices = by_name.get(items[0].target,()) if len(items)==1 and items[0].media in SMIL_TYPES else ()
        if len(choices)!=1:
            flush()
            scoped_issue(f'DAISY spine entry {ident!r} needs one local SMIL member.',ordinal)
            continue
        smil, = choices
        try:
            root = xml(member_bytes(archive,smil,MAX_STRUCTURE))
        except Error as exc:
            issue(smil,None,f'Cannot read {smil.name}: {exc}')
            continue
        body = next((node for node in root if name(node)=='body'),None)
        if name(root)!='smil' or body is None:
            issue(smil,root,'This SMIL member has no authored playback body.')
            continue
        text_cues = [node for node in body.iter() if name(node)=='text']
        if not text_cues:
            issue(smil,body,'This SMIL member contains no text references.')
        for node in text_cues:
            href = node.get('src','')
            filename = target(smil.name,href)
            paths = by_name.get(filename,())
            if len(paths)!=1 or records.get(filename,{}).get('media_type')!=DTBOOK:
                issue(smil,node,f'DAISY text cue {href!r} needs one declared local DTBook member.')
                continue
            if any(name(parent)=='switch' for parent in node.iterancestors()):
                issue(smil,node,'An authored SMIL switch has alternate text branches and needs a verified selection.')
                continue
            member, = paths
            if filename not in cached:
                document = DocumentBuilder(builder.doc.format,builder.doc.title,builder.doc.source_sha256)
                try:
                    read_dtbook(xml(member_bytes(archive,member,MAX_STRUCTURE)),document,filename,collect_resources=True)
                except Error as exc:
                    issue(smil,node,f'Cannot read {filename}: {exc}')
                    continue
                cached[filename] = (member,document.doc,anchored_blocks(document.doc))
                notes[member] = [block for block in document.doc.blocks if block.role=='note']
                if len(cached)>2:
                    cached.popitem(last=False)
                records[filename]['status'] = 'represented'
            cached.move_to_end(filename)
            fragment = unquote(urlsplit(href).fragment)
            _,document,indexed = cached[filename]
            ranges = indexed.get(fragment,()) if fragment else [(b,0,len(b.source)) for b in document.blocks]
            if not ranges or fragment and len(ranges)!=1:
                issue(smil,node,f'DAISY text cue {href!r} lacks a unique source anchor.')
                continue
            for block,start,end in ranges:
                cue = {'member':smil.name,'archive_entry':smil.index,'line':node.sourceline,'href':href,
                       'spine_occurrence':ordinal}
                current = Passage(member,block,start,end,[cue])
                if pending is not None and pending.extend(current):
                    continue
                flush()
                pending = current
    flush()

    for member,definitions in notes.items():
        for original in definitions:
            if (member.index,original.id) not in matched:
                pending = Passage(member,original,0,len(original.source))
                flush()
    for block in builder.doc.blocks:
        index = block.locator['archive_entry']
        for link in block.links:
            if destinations:=link.get('destinations'):
                link['destinations'] = [
                    'source.html#'+matched.get((index,part.split('#',1)[1]),part.split('#',1)[1])
                    if part.startswith('source.html#b') else part for part in destinations]
