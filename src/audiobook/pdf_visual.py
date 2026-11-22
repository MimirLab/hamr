"""Visual source observations for admitted PDF pages and crops."""
import io
import math
import os
import re
import subprocess
import sys
from ctypes import byref, c_ulong, c_ushort

from PIL import Image, ImageStat

from .core import Error, digest
from .pdf_fidelity import damaged_regnal_glyph, disputes
from .pdf_layout import column_order, paragraphs
from .pdf_regions import glyph_reader, localize

SCALE = 2
MAX_PIXELS = 12_000_000
MAX_REGIONS = 64


def authored_artifact(obj):
    from pypdfium2 import raw

    for index in range(raw.FPDFPageObj_CountMarks(obj)):
        mark = raw.FPDFPageObj_GetMark(obj,index)
        name, length = (c_ushort*32)(), c_ulong()
        if (raw.FPDFPageObjMark_GetName(mark,name,len(name)*2,byref(length)) and
                bytes(name)[:length.value].decode('utf-16-le',errors='replace').rstrip('\0')=='Artifact'):
            return True
    return False


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def regional_images(images, visible_glyphs, width, height):
    """Use crops only when their ownership is separate from native lines."""
    if len(images) > MAX_REGIONS:
        return None
    visible = [rect for ch, rect, _ in visible_glyphs if ch.strip()]
    crops = []
    for left, bottom, right, top in images:
        if not all(map(math.isfinite,(left,bottom,right,top))):
            return None
        rect = [max(0, math.floor(left*SCALE)/SCALE), max(0, math.floor(bottom*SCALE)/SCALE),
                min(width, math.ceil(right*SCALE)/SCALE), min(height, math.ceil(top*SCALE)/SCALE)]
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            continue
        if any(intersects(rect, other) for other in visible) or any(intersects(rect, other) for other in crops):
            return None
        crops.append(rect)
    return crops


def raster(page, region):
    width, height = page.get_size()
    left, bottom, right, top = region
    if math.ceil((right-left)*SCALE)*math.ceil((top-bottom)*SCALE) > MAX_PIXELS:
        raise Error('ocr_pixels', 'This visual region exceeds the supported OCR raster size. A smaller verified crop is needed.')
    bitmap = page.render(scale=SCALE, crop=(left,bottom,width-right,height-top), grayscale=(left != 0 or bottom != 0 or right != width or top != height),
                         limit_image_cache=True)
    try:
        return bitmap.to_pil().convert('RGB')
    finally:
        bitmap.close()


def read_words(tsv, size):
    """Reject malformed coordinates instead of accepting a partial output."""
    words = []
    for row in tsv.splitlines()[1:]:
        fields = row.split('\t', 11)
        try:
            if fields[0] != '5':
                continue
            x,y,w,h = map(int, fields[6:10])
            score = float(fields[10])
            text = fields[11]
            if not (0 <= x < x+w <= size[0] and 0 <= y < y+h <= size[1] and 0 <= score <= 100):
                raise ValueError
            if text.strip():
                words.append(fields)
        except (ValueError, IndexError) as exc:
            raise Error('ocr_contract', 'OCR returned an invalid word, score, or position for this region.') from exc
    return words


def recheck_dialogue_initials(image, words, read_line):
    """Use the page pixels to confirm a common I/T error after an opening quote."""
    lines = {}
    for index, word in enumerate(words):
        lines.setdefault(tuple(word[1:5]), []).append(index)
    corrections = []
    for indices in lines.values():
        if len(indices) < 3:
            continue
        index = indices[0]
        first = words[index][11]
        if not re.fullmatch(r"""["'“‘]T""", first):
            continue
        following = [words[i][11] for i in indices[1:]]
        if not following[0][0].islower():
            continue
        boxes = [tuple(map(int, words[i][6:10])) for i in indices]
        left = max(0, min(x for x,y,w,h in boxes)-10)
        top = max(0, min(y for x,y,w,h in boxes)-10)
        right = min(image.width, max(x+w for x,y,w,h in boxes)+10)
        bottom = min(image.height, max(y+h for x,y,w,h in boxes)+10)
        if (right-left)*(bottom-top)*9 > MAX_PIXELS:
            continue
        crop = image.crop((left,top,right,bottom))
        expected = [first[:-1]+'I', *following]
        confirmations = []
        for scale in (2,3):
            view = crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS)
            alternate = read_line(view)
            if [row[11] for row in alternate] != expected:
                break
            confirmations.append({'scale':scale,'confidence':float(alternate[0][10])})
        if len(confirmations) == 2:
            corrections.append({'original':first,'text':expected[0],'rect_pixels':list(boxes[0]),
                                'confidence':float(words[index][10]),'confirmations':confirmations})
            # Keep the page geometry and the original confidence in the audit record.
            words[index] = [*words[index][:10],str(min(x['confidence'] for x in confirmations)),expected[0]]
    return corrections


class Observer:
    """Identify the OCR engine and acquire a process only after a cache miss."""
    def __init__(self, cache=None, config=None):
        self.cache = cache
        self.config = config
        self.scope = None

    def read(self, page, textpage, entry, rect, native='', *, regional=False):
        from .pdf_ocr import configuration, observe, offline_observation
        from .pdf_words import localize_words

        page_number = entry['page']
        locator = {'format':'pdf', 'page':page_number, 'rect':rect}
        evidence = {'rect':rect, 'scale':SCALE, 'native_observation':native,
                    'coordinate_space':'rendered_page_points'}
        try:
            image = raster(page, rect)
            if not native.strip() and all(lo==hi for lo,hi in ImageStat.Stat(image).extrema):
                evidence['status'] = 'verified_blank'
                return [], evidence
            if self.scope is None:
                self.scope = {name:os.environ.get(name) for name in ('AUDIOBOOK_OCR_ROOT','TESSDATA_PREFIX')}
                settings = self.cache.parent.parent / 'ocr-settings.json' if self.cache else None
                self.scope['managed_settings'] = digest(settings.read_bytes()) if settings and settings.is_file() else None
            png = io.BytesIO()
            image.save(png, format='PNG')
            payload = png.getvalue()
            offline = False
            try:
                if self.config is None:
                    from .ocr_setup import MODEL_SHA256, english_data
                    data = english_data(self.cache.parent.parent) if self.cache else None
                    if self.scope['managed_settings'] and not data:
                        raise Error('missing_ocr_data','Managed English OCR data is missing.', 'Run hamr setup --ocr-only.')
                    self.config = configuration(data) if data else configuration()
                    if data and self.config[2]['model_sha256'] != MODEL_SHA256:
                        self.config = None
                        raise Error('asset_integrity','The installed English OCR model changed.',
                                    'Restore the pinned model and run hamr setup --ocr-only.')
                selected = self.config
                tsv, reused = observe(payload, selected, self.cache, self.scope)
            except Error as exc:
                if exc.code not in ('missing_ocr','missing_ocr_data') or not (
                        previous := offline_observation(payload,self.cache,self.scope)):
                    raise
                tsv, recipe = previous
                selected, reused, offline = (None,None,recipe,False), True, True
            words = read_words(tsv, image.size)
            if regional and not words and not offline and selected and len(selected)==4:
                binary, env, recipe, reusable = selected
                alternate = (binary, env, {**recipe,'page_segmentation':11}, reusable)
                retry, retry_reused = observe(payload, alternate, self.cache, self.scope)
                alternate_words = read_words(retry, image.size)
                evidence['attempts'] = [{'observer':recipe,'recognized_words':0,'reused':reused},
                                        {'observer':alternate[2],'recognized_words':len(alternate_words),'reused':retry_reused}]
                if alternate_words:
                    words, selected, reused = alternate_words, alternate, retry_reused
            if words and selected and len(selected)==4:
                binary, env, recipe, reusable = selected
                line_recipe = {**recipe,'page_segmentation':7}
                line_scope = {**self.scope,'view':'dialogue_initial'}

                def read_line(view):
                    stream = io.BytesIO()
                    view.save(stream,format='PNG')
                    line_png = stream.getvalue()
                    if offline:
                        saved = offline_observation(line_png,self.cache,line_scope)
                        return read_words(saved[0],view.size) if saved and saved[1]==line_recipe else []
                    try:
                        result,_ = observe(line_png,(binary,env,line_recipe,reusable),self.cache,line_scope)
                        return read_words(result,view.size)
                    except Error as exc:
                        if exc.code not in ('ocr_contract','ocr_size'):
                            raise
                        return []
                    except (subprocess.SubprocessError,OSError):
                        return []

                if corrections := recheck_dialogue_initials(image,words,read_line):
                    evidence['ocr_rechecks'] = corrections
            if offline:
                evidence['offline_checkpoint'] = True
            if self.cache:
                status = 'saved observation reused' if reused else 'observation recorded'
                print(f'PDF OCR · page {page_number} · {status}.', file=sys.stderr, flush=True)
        except Error as exc:
            issue = ' '.join(filter(None,(exc.message,exc.action)))
            evidence.update(status='unresolved', error_code=exc.code, reason=issue)
            return [{'text':native, 'locator':locator, 'issue':issue, 'role':'unreadable'}], evidence
        except (subprocess.SubprocessError, OSError) as exc:
            issue = 'OCR did not finish for this source region. Check the local engine and reimport.'
            evidence.update(status='unresolved', error_code='ocr_failed', failure=type(exc).__name__)
            return [{'text':native, 'locator':locator, 'issue':issue, 'role':'unreadable'}], evidence

        groups = {}
        for word in words:
            groups.setdefault(tuple(word[1:5]), []).append(word)
        text = '\n'.join(' '.join(word[11] for word in line) for line in groups.values())
        confidence = sum(float(word[10]) for word in words) / max(1,len(words))
        issue = 'Visible page text could not be read reliably.' if not text.strip() or confidence < 65 else None
        critical = disputes(native,text)
        width,height = entry['size']
        parts = paragraphs(words,page_number,height,region=rect if regional else None,
                           width=None if regional else width)
        geometry = entry['native_geometry_qualified']
        source_reader = glyph_reader(textpage) if geometry and native.strip() else lambda rect: ''
        critical_localization = localize(parts,native,text,source_reader,geometry)
        if not parts:
            parts = [{'text':text,'locator':locator,'issue':issue,'role':'unreadable'}]
        lexical_localization = localize_words(parts,native,text,source_reader,geometry)
        for part in parts:
            if damaged_regnal_glyph(part['text']):
                reason = 'Visual OCR shows an unmatched bracket in a possible regnal numeral. Verify the printed reading before synthesis.'
                part['issue'] = ' '.join(filter(None,(part['issue'],reason)))
        evidence.update(
            status='needs_review' if any(part['issue'] for part in parts) else 'observed',
            observer=selected[2] if selected and len(selected)>2 else None,
            ocr_confidence=confidence, score_kind='tesseract_word_score',
            ocr_observation=text, raw_OCR_reused=reused, critical_disputes=critical,
            critical_localization=critical_localization, lexical_localization=lexical_localization,
            ocr_words=[{'text':word[11], 'confidence':float(word[10]), 'rect_pixels':[int(v) for v in word[6:10]],
                        'group':[int(v) for v in word[1:5]]} for word in words],
        )
        return parts, evidence


def merge_regions(blocks, width):
    def rect(block):
        return block.locator['rect']
    # Keep native ordering unless there is visual content to insert.
    return column_order(blocks,width,rect) or sorted(blocks,key=lambda block: (-rect(block)[3],rect(block)[0]))


def bind_captions(blocks, evidence):
    """Pair a raster with a uniquely adjacent authored figure caption."""
    pairs = []
    for item in evidence:
        rect = item['rect']
        candidates = []
        for block in blocks:
            if not re.match(r'^\s*(?:Figure|Fig\.)\s+\d+\b',block.source,re.I):
                continue
            left,bottom,right,top = block.locator['rect']
            if left < rect[2] and rect[0] < right and (0 <= rect[1]-top <= 36 or 0 <= bottom-rect[3] <= 36):
                candidates.append(block)
        if len(candidates) == 1:
            pairs.append((item,candidates[0]))
    for item,caption in pairs:
        if sum(block.id == caption.id for _,block in pairs) == 1:
            caption.role = 'caption'
            item['caption_block'] = caption.id
