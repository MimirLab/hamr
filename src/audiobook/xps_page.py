"""Observe literal XPS text, its visible geometry, and word boundaries."""

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import PurePosixPath
import math
import re
from urllib.parse import urlsplit

from PIL import ImageFont

from .core import Error
from .document_intake import name


IDENTITY = (1,0,0,1,0,0)
NUMBER = r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'
POINT = rf'({NUMBER})\s*,\s*({NUMBER})'
RECTANGLE = re.compile(rf'\s*M\s*{POINT}\s*L\s*{POINT}\s*L\s*{POINT}\s*L\s*{POINT}\s*[Zz]\s*')
POSITION_ISSUE = 'The XPS glyph has an unsupported transform, clip or text direction. Verify the marked text.'


def coordinates(values):
    data = tuple(float(v) for v in values)
    if any(not math.isfinite(v) or abs(v)>10**8 for v in data):
        raise ValueError('Invalid XPS geometry')
    return data


def point(matrix,x,y):
    a,b,c,d,e,f = matrix
    return a*x+c*y+e,b*x+d*y+f


def compose(outer,inner):
    a,b,c,d,e,f = inner
    u,v = point(outer,a,b)
    w,x = point(outer,c,d)
    y,z = point(outer,e,f)
    return u-outer[4],v-outer[5],w-outer[4],x-outer[5],y,z


def rectangle(points):
    xs,ys = zip(*points)
    return min(xs),min(ys),max(xs),max(ys)


def transformed(rect,matrix):
    left,top,right,bottom = rect
    return rectangle(point(matrix,x,y) for x in (left,right) for y in (top,bottom))


def intersect(first,second):
    a,b,c,d = first
    x,y,w,h = second
    return max(a,x),max(b,y),min(c,w),min(d,h)


def transform(node):
    explicit = node.get('RenderTransform')
    wrapper = node.find(f'{node.tag}.RenderTransform')
    if wrapper is not None:
        if len(wrapper)!=1 or name(wrapper[0])!='MatrixTransform' or explicit is not None:
            raise ValueError('Unresolved transform')
        explicit = wrapper[0].get('Matrix')
    if explicit is None:
        return IDENTITY
    values = coordinates(re.split(r'[\s,]+',explicit.strip()))
    if len(values)!=6:
        raise ValueError('Invalid matrix')
    return values


def clip_box(node):
    if node.find(f'{node.tag}.Clip') is not None:
        raise ValueError('Nonrectangular clip')
    clip = node.get('Clip')
    if clip is None:
        return None
    match = RECTANGLE.fullmatch(clip)
    if match is None:
        raise ValueError('Nonrectangular clip')
    vals = coordinates(match.groups())
    points = list(zip(vals[::2],vals[1::2]))
    x,y,w,h = rectangle(points)
    if set(points)!={(i,j) for i in (x,w) for j in (y,h)}:
        raise ValueError('Nonrectangular clip')
    return x,y,w,h


class FontMetrics:
    def __init__(self,package):
        self.package = package
        self.font = lru_cache(maxsize=16)(self.read_font)

    def read_font(self, member, index):
        try:
            if member.size is not None and member.size>8*1024**2:
                return None
            data = self.package.read(member)
            if len(data)>8*1024**2:
                return None
            path = PurePosixPath(member.name)
            if path.suffix.casefold() in ('.odttf','.odttc'):
                key = bytes.fromhex(path.stem.replace('-',''))[::-1]*2
                if len(key)!=32 or len(data)<32:
                    return None
                data = bytes(x^y for x,y in zip(data[:32],key))+data[32:]
            return ImageFont.truetype(BytesIO(data),size=256,index=index,layout_engine=ImageFont.Layout.BASIC)
        except (OSError,ValueError,Error):
            return None

    def measure(self, page, node, text, size):
        uri = node.get('FontUri','')
        member = self.package.resolve(page,uri)
        face = None
        if member:
            fragment = urlsplit(uri).fragment
            if not fragment or re.fullmatch(r'[0-9]{1,3}',fragment):
                face = self.font(member,int(fragment or '0'))
        space = face.getlength(' ')*size/256 if face else .28*size
        fallback = face.getlength(text)*size/256 if face and text else None
        ink = tuple(v*size/256 for v in face.getbbox(text.rstrip(),anchor='ls')) if face and text.strip() else None
        advance = fallback
        indices = node.get('Indices','').rstrip(';')
        if indices:
            fields = [item.split(',') for item in indices.split(';')]
            if all(len(item)>1 and item[1] for item in fields):
                widths = coordinates(item[1] for item in fields)
                advance = sum(widths)*size/100
                # Producers often omit the last advance, which is implicit.
                if len(fields)==len(text)-1 and face and '(' not in indices:
                    advance += face.getlength(text[-1])*size/256
                advance = max(0,advance)
        if ink and advance is not None and fallback is not None:
            ink = (*ink[:2],max(ink[0],ink[2]+advance-fallback),ink[3])
        return advance,space,ink


@dataclass(frozen=True)
class GlyphRun:
    key: int
    source: str
    text: str
    rect: tuple
    origin: tuple
    advance: float | None
    space: float
    size: float
    issue: str | None

    def observation(self):
        return {'glyph':self.key,'rect':self.rect,'source':self.source,
                **({'issue':self.issue} if self.issue else {})}


def run(node,index,matrix,region,uncertain,metrics,member):
    source = node.get('UnicodeString','')
    text = source.removeprefix('{}')
    if not text and not node.get('Indices'):
        return None
    fill = node.get('Fill','')
    if fill.casefold()=='transparent' or re.fullmatch(r'#00[0-9a-fA-F]{6}',fill):
        return None
    try:
        x,y,size = coordinates(node.get(k,'0') for k in ('OriginX','OriginY','FontRenderingEmSize'))
        if size<=0:
            raise ValueError('Invalid text size')
        width,space,ink = metrics.measure(member,node,text,size)
        origin = point(matrix,x,y)
        if not ink or ink[0]>=ink[2] or ink[1]>=ink[3]:
            ink = (0,-size*.82,width if width else size,size*.24)
        bounds = transformed((x+ink[0],y+ink[1],x+ink[2],y+ink[3]),matrix)
        extent = intersect(bounds,region)
        if extent[0]>=extent[2] or extent[1]>=extent[3]:
            return None
        clipped = max(abs(a-b) for a,b in zip(extent,bounds))>size*.12*max(abs(matrix[0]),abs(matrix[3]))
        angled = matrix[1] or matrix[2] or matrix[0]<=0 or matrix[3]<=0
        sideways = node.get('IsSideways','false').casefold() in ('true','1')
        direction = int(node.get('BidiLevel','0'))%2
        issue = POSITION_ISSUE if uncertain or angled or sideways or direction or clipped else None
        if text and not text.strip():
            issue = None
        elif not text:
            issue = 'This XPS text has printed glyph indices without a Unicode reading. Verify the marked source.'
        return GlyphRun(index,source,text,extent,origin,width*matrix[0] if width is not None else None,
                        max(.1,space*matrix[0]),size*abs(matrix[3]),issue)
    except (ValueError,OverflowError):
        return GlyphRun(index,source,text,region,(region[0],region[1]),None,1,1,POSITION_ISSUE)


def observations(package,member,root,metrics,size):
    runs, owners, declared = [],defaultdict(list),set()
    content = False
    serial = 0

    def visit(node,matrix,scope,ancestors,uncertain=False):
        nonlocal content,serial
        kind = name(node)
        if kind not in ('FixedPage','Canvas','Glyphs','Path','AlternateContent'):
            return
        identifier = node.get('Name')
        if identifier:
            if identifier in declared:
                owners[identifier] = None
            else:
                owners[identifier] = []
            declared.add(identifier)
            ancestors = (*ancestors,identifier)
        try:
            if float(node.get('Opacity','1'))==0:
                return
            matrix = compose(matrix,transform(node))
            if clip := clip_box(node):
                scope = intersect(scope,transformed(clip,matrix))
                if scope[0]>=scope[2] or scope[1]>=scope[3]:
                    return
        except (ValueError,OverflowError):
            uncertain = True
        uncertain |= bool(node.get('OpacityMask') or node.find(f'{node.tag}.OpacityMask') is not None)
        if kind=='Glyphs':
            serial += 1
            glyph = run(node,serial,matrix,scope,uncertain,metrics,member)
            if glyph:
                runs.append(glyph)
                for owner in ancestors:
                    if owners[owner] is not None:
                        owners[owner].append(glyph)
        elif kind=='Path':
            content = True
        elif kind=='AlternateContent':
            content = True
            serial += 1
            runs.append(GlyphRun(serial,'','',scope,(0,0),None,1,1,
                                 'XPS markup alternatives need a verified visible reading.'))
        else:
            for child in node:
                if child.tag==package.fixed+name(child) or child.tag=='{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent':
                    visit(child,matrix,scope,ancestors,uncertain)

    visit(root,IDENTITY,(0,0,*size),())
    return runs,owners,declared,content


def raised_ordinal(previous,glyph,baseline,spacing):
    suffix = glyph.text.casefold()
    if suffix not in ('st','nd','rd','th') or abs(spacing)>.3*previous.space:
        return False
    height = baseline-glyph.origin[1]
    if glyph.size>.85*previous.size or not .18*previous.size<=height<=.7*previous.size:
        return False
    number = re.search(r'\b[1-9][0-9]*$',previous.text)
    if not number:
        return False
    value = int(number[0][-2:])
    expected = 'th' if 11<=value<=13 else {1:'st',2:'nd',3:'rd'}.get(value%10,'th')
    return suffix==expected


def assemble(runs):
    """Keep source order and use measured advances only between adjacent runs."""
    current = []
    previous = None
    reading = ''
    unresolved = None
    baseline = 0
    line_size = 0
    for glyph in runs:
        issue = glyph.issue
        sep = ''
        join = False
        if previous is not None and not issue and not unresolved:
            x,y = glyph.origin
            p = previous.origin[0]
            literal_space = reading.endswith((' ','\t')) or glyph.text.startswith((' ','\t'))
            gap = x-p-previous.advance if previous.advance is not None else None
            if gap is not None and raised_ordinal(previous,glyph,baseline,gap):
                join = True
            elif abs(y-baseline)<=.19*max(glyph.size,line_size) and x>=p-.25*previous.space:
                if gap is None:
                    join = literal_space
                    if not join:
                        issue = 'Adjacent XPS glyph runs lack measurements for their word boundary.'
                else:
                    join = -.75*previous.space<=gap<=3.2*previous.space
                    if join and not literal_space and gap>=.5*previous.space:
                        sep = ' '
        if current and not join:
            yield reading.strip(),current,unresolved
            current = []
            reading = ''
        if not current:
            baseline,line_size = glyph.origin[1],glyph.size
        current.append(glyph)
        reading += sep+glyph.text
        previous = glyph
        unresolved = issue
    if current:
        yield reading.strip(),current,unresolved


def locator(runs,evidence):
    height = evidence['size'][1]
    x,y,w,h = rectangle((x,y) for run in runs for x,y in ((run.rect[0],run.rect[1]),(run.rect[2],run.rect[3])))
    return {'page':evidence['page'],'rect':[x,height-h,w,height-y],
            'xps_glyphs':[run.observation() for run in runs]}


def page_reading(package,member,root,metrics,builder,evidence,visual=None):
    from .xps_story import story_reading

    try:
        width,height = coordinates(root.get(field,'0') for field in ('Width','Height'))
        if width<=0 or height<=0:
            raise ValueError('Invalid page size')
    except ValueError as error:
        raise Error('source_xps_page',f'Invalid page size in {member.name}.') from error
    evidence['size'] = [width,height]
    runs,owners,names,graphics = observations(package,member,root,metrics,(width,height))
    evidence['glyph_runs'] = len(runs)
    if not runs and not graphics:
        evidence['status'] = 'verified_blank'
        return names
    if visual is not None and not any(glyph.text.strip() for glyph in runs):
        if runs:
            evidence['glyph_observation'] = [glyph.observation() for glyph in runs]
        visual.read(member,builder,evidence)
        return names
    first = len(builder.doc.blocks)
    try:
        used = story_reading(package,member,owners,builder,evidence)
    except Error as error:
        builder.add('','unreadable',member.name,loc={'page':evidence['page']},issue=error.message)
        used = set()
    for index,(text,observed,issue) in enumerate(assemble(run for run in runs if run.key not in used)):
        builder.add(text,'body' if text else 'unreadable',member.name,
                    loc={'paragraph':index,**locator(observed,evidence)},issue=issue)
    if graphics and not runs:
        builder.add('','unreadable',member.name,loc={'page':evidence['page']},
                    issue='This XPS page has graphics without extractable text. Supply or verify its visible reading.')
    evidence['status'] = 'needs_review' if any(b.issue for b in builder.doc.blocks[first:]) else 'observed'
    return names
