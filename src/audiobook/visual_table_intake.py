"""Bind source observations to the common visual table projection."""

from collections import Counter, defaultdict
from ctypes import c_void_p, cast
from itertools import pairwise
from statistics import median
import math
import re

from .pdf_layout import paragraphs
from .visual_table_alignment import aligned_tables
from .visual_table_grid import Token, raster_rules, ruled_tables


def bottom_up(rect, left, top, scale):
    x1,y1,x2,y2 = rect
    return [left+x1/scale,top-y2/scale,left+x2/scale,top-y1/scale]


def observation_rect(rect, top):
    left,bottom,right,high = rect
    return left,top-high,right,top-bottom


def read_tables(image, tokens, baseline, locate, read_cell, word_join=None):
    heights = [token.rect[3]-token.rect[1] for token in tokens if token.text.strip()]
    if len(heights)<4:
        return []
    ruled = []
    if image is not None:
        size = median(heights)
        horizontal,vertical = raster_rules(image,size)
        ruled = ruled_tables(horizontal,vertical,tokens,locate,read_cell,max(1.5,.22*size))
    occupied = set().union(*(claim for claim,_ in ruled))
    rest = [token for token in tokens if token.key not in occupied]
    # The taller boxes better represent line height than punctuation and
    # lowercase x-height when estimating gaps from text without rules.
    line_height = sorted(heights)[3*len(heights)//4]
    return ordered_tables(ruled+aligned_tables(rest,baseline,line_height,locate,read_cell,word_join),tokens)


def ordered_tables(tables, tokens):
    """Only splice tables that have an independent place in the page flow."""
    if not tables:
        return []
    count = Counter(key for claim,_ in tables for key in claim)
    by_key = {token.key:token for token in tokens}
    result = []
    # A side-by-side article and table need a separate reading-order witness.
    # Leave that page on its established flow path.
    for claim,table in tables:
        top = min(by_key[key].rect[1] for key in claim)
        bottom = max(by_key[key].rect[3] for key in claim)
        if (not any(count[key]>1 for key in claim)
                and not any(t.key not in claim and t.rect[1]<bottom and t.rect[3]>top for t in tokens)):
            result.append((claim,table))
    return result


def visual_ocr_parts(image, words, page, width, height, *, format, scale, region, read_crop):
    left,top = (region[0],region[3]) if region else (0,height)

    def locate(rect):
        return {'format':format,'page':page,'rect':bottom_up(rect,left,top,scale)}

    def read_cell(owned, bounds):
        observations = [words[token.key] for token in sorted(owned,key=lambda t:t.key)]
        parts = paragraphs(observations,page,height,region=region,scale=scale,format=format)
        text = ' '.join(part['text'].replace('\n',' ') for part in parts)
        original = {'ocr_word_indices':sorted(t.key for t in owned)}
        issue = next((part['issue'] for part in parts if part['issue']),None)
        if bounds:
            text, evidence, doubt = check_rule_glyphs(image,owned,bounds,text,read_crop)
            original.update(evidence)
            issue = issue or doubt
        return text,original,issue

    tokens = []
    for index,item in enumerate(words):
        x,y,w,h = map(int,item[6:10])
        tokens.append(Token(index,item[11],(x,y,x+w,y+h)))
    tables = read_tables(image,tokens,lambda t:(t.rect[1]+t.rect[3])/2,locate,read_cell)
    if not tables:
        return None
    pending = set(range(len(words)))
    result = []
    for claim,table in sorted(tables,key=lambda item:-item[1]['locator']['rect'][3]):
        table_top = table['locator']['rect'][3]
        before = [token.key for token in tokens if token.key in pending-claim
                  and table_top<=locate(token.rect)['rect'][1]]
        result.extend(paragraphs([words[index] for index in before],page,height,region=region,width=width,scale=scale,format=format))
        result.append(table)
        pending.difference_update(before,claim)
    result.extend(paragraphs([words[index] for index in sorted(pending)],page,height,region=region,width=width,scale=scale,format=format))
    return result


def check_rule_glyphs(image, tokens, bounds, text, read_crop):
    """Check a pipe that OCR attached to a word next to a vertical rule."""
    left,top,right,bottom = bounds
    candidates = [token for token in tokens if token.text.startswith('|') and
                  token.rect[0] <= left + max(2, (token.rect[3]-token.rect[1])*.28)]
    if not candidates:
        return text,{},None
    observed = {'candidate_ocr':text,'rule_glyph_words':[t.key for t in candidates]}
    doubt = 'OCR may have read part of a vertical table border as text. Verify the marked cell.'
    conjecture = re.sub(r'\s+',' ', ' '.join((token.text.removeprefix('|') if token in candidates else token.text)
                         for token in sorted(tokens,key=lambda t:t.key))).strip()
    if read_crop and right-left>8 and bottom-top>8:
        crop = (math.ceil(left)+2,math.ceil(top)+2,math.floor(right)-2,math.floor(bottom)-2)
        if crop[0]<crop[2] and crop[1]<crop[3]:
            with image.crop(crop) as cell:
                confirmed = read_crop(cell)
            if (confirmed and all(float(item[10])>=70 for item in confirmed)
                    and ' '.join(item[11] for item in confirmed)==conjecture):
                return conjecture,{**observed,'verified_inner_crop':list(crop)},None
    return text,observed,doubt


def native_parts(page, textpage, glyphs, number, width, height):
    from pypdfium2 import raw
    from .core import Error
    from .pdf_geometry import glyph_baseline
    from .pdf_tagged_tables import ranges
    from .pdf_visual import raster, SCALE

    def locate(rect):
        return {'format':'pdf','page':number,'rect':bottom_up(rect,0,height,SCALE)}

    y_positions = {}

    def baseline(token):
        if token.key not in y_positions:
            origin = glyph_baseline(textpage,token.key,0,height-token.rect[3]/SCALE)
            y_positions[token.key] = (height-origin)*SCALE
        return y_positions[token.key]

    def word_join(left, right, size):
        # Only bridge the measured word gap within one native print run.
        # Independently positioned cells and numeric cells stay separate.
        if (not left.text.isalpha() or not right.text.isalpha()
                or right.rect[0]-left.rect[2]>1.3*size or right.key-left.key!=2):
            return False
        space = left.key+1
        if raw.FPDFText_IsGenerated(textpage,space)!=0 or not textpage.get_text_range(space,1).isspace():
            return False
        owners = [cast(raw.FPDFText_GetTextObject(textpage,key),c_void_p).value
                  for key in (left.key,space,right.key)]
        return owners[0] is not None and owners[0]==owners[1]==owners[2]

    def read_cell(owned, bounds):
        # Keep each glyph's original index. PDFium's inserted separators
        # can lie between cells, so use the observed ink to order lines.
        lines = defaultdict(list)
        measures = []
        for token in owned:
            y = baseline(token)/SCALE
            lines[round(y*2)/2].append(token)
            measures.append((token,y,raw.FPDFText_GetFontSize(textpage,token.key)))
        scripts = any(min(size,next_size)<.85*max(size,next_size) and .8<=abs(y-next_y)<.65*max(size,next_size)
                      and abs(other.rect[0]-token.rect[2])<SCALE*max(size,next_size)
                      for (token,y,size),(other,next_y,next_size) in pairwise(sorted(measures,key=lambda m:m[0].key)))
        pieces = []
        for _,line in sorted(lines.items()):
            last = None
            text = ''
            for token in sorted(line,key=lambda t:(t.rect[0],t.key)):
                if last and last.key+1!=token.key:
                    between = textpage.get_text_range(last.key+1,token.key-last.key-1) if last.key<token.key-1 else ''
                    if (between and between.isspace()) or token.rect[0]-last.rect[2]>2*SCALE:
                        text += ' '
                text += token.text
                last = token
            pieces.append(text)
        issue = 'The cell has shifted typography that needs a verified reading.' if scripts else None
        return ' '.join(pieces),{'native_ranges':ranges(sorted(t.key for t in owned))},issue

    tokens = [Token(index,ch,tuple(point*SCALE for point in observation_rect(rect,height)))
              for ch,rect,index in glyphs if ch.strip()]
    if next(page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]),None):
        try:
            picture = raster(page,[0,0,width,height])
        except Error:
            picture = None
        if picture is not None:
            with picture:
                return read_tables(picture,tokens,baseline,locate,read_cell,word_join)
    return read_tables(None,tokens,baseline,locate,read_cell,word_join)
