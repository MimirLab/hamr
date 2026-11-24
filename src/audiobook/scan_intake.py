"""Read page sets directly and keep the source position of every observation."""
from contextlib import ExitStack, contextmanager
import shutil
import tempfile
import math
from pathlib import PurePosixPath
import re
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageOps, UnidentifiedImageError

from .core import Document, Error, fault, key
from .pdf_headers import mark_headers
from .pdf_layout import mark_folios, paragraphs
from .pdf_visual import MAX_PIXELS, Observer, read_words
from .source_bundle import (
    BUNDLE_SCHEMA, CHUNK, IMAGE_SUFFIXES, MAX_MANIFEST, SIDECAR_ENDINGS,
    image_magic, manifest_data, natural, safe_name,
)

MAX_MEMBER_SPOOL = 384*1024*1024
CHAPTER = re.compile(r'\s*(?:chapter|book|part)\s+(?:[ivxlcdm]+|[0-9]+)\s*[.!]?\s*',re.I)


def bounded(stream, size):
    data = stream.read(size+1)
    if len(data)>size:
        raise Error('source_member_size', 'A source member exceeds the maximum buffered observation size.')
    return data


def bundle_index(archive):
    import json

    try:
        with archive.open('manifest.json') as stream:
            manifest = json.loads(bounded(stream,MAX_MANIFEST))
        if (manifest['schema']!=BUNDLE_SCHEMA or not isinstance(manifest['members'],dict)
                or not isinstance(manifest['pages'],list) or not manifest['pages']):
            raise ValueError
        for relative,item in manifest['members'].items():
            safe_name(relative)
            digest = item['sha256']
            if not re.fullmatch('[0-9a-f]{64}',digest) or archive.getinfo('blobs/'+digest).file_size!=item['size']:
                raise ValueError
        for item in manifest['pages']:
            if item['path'] not in manifest['members']:
                raise ValueError
            if item.get('transcription') and item['transcription']['path'] not in manifest['members']:
                raise ValueError
        return manifest
    except (KeyError, ValueError, TypeError, BadZipFile) as exc:
        raise Error('source_manifest', 'The saved source bundle has an invalid manifest or member index.') from exc


def archive_order(archive,entries,selected):
    paths = {}
    for item in entries:
        paths.setdefault(item.name,[]).append(item)
    order = next((item for item in entries if item.index==selected),None)
    if order is None:
        raise Error('source_member_missing','The saved archive has no selected page order.')
    with archive.open_member(order) as stream:
        manifest = manifest_data(bounded(stream,MAX_MANIFEST),order.name)
    root = PurePosixPath(order.name).parent

    def member(item):
        path = str(root / safe_name(item.get('path')))
        matches = paths.get(path,[])
        if len(matches)!=1:
            raise Error('source_manifest',f'The archive page order needs one member named {path}. Found {len(matches)}.')
        return {'path':path,'entry':matches[0].index}

    pages, occurrences = [], set()
    for i,row in enumerate(manifest['pages'],1):
        if not isinstance(row,dict):
            raise Error('source_manifest',f'Page entry {i} must name a relative image path.')
        occurrence = row.get('occurrence',f'p{i:06d}')
        if not isinstance(occurrence,str) or not occurrence or occurrence in occurrences:
            raise Error('source_manifest',f'Invalid or repeated occurrence in page entry {i}.')
        occurrences.add(occurrence)
        page = {**row,**member(row),'occurrence':occurrence}
        if 'transcription' in row:
            trans = row['transcription']
            if not isinstance(trans,dict):
                raise Error('source_manifest',f'Page entry {i} needs an OCR transcription path.')
            page['transcription'] = {**trans,**member(trans)}
        pages.append(page)
    return manifest.get('title'),pages


@contextmanager
def sequence(path,source):
    if source.kind=='file':
        pages = [{'path':source.member_name or path.name,'occurrence':'p000001'}]
        yield None,pages,lambda item: path.open('rb'),None
        return
    from .archive_intake import containers

    with containers(path,source.kind) as archive:
        if source.kind=='bundle':
            manifest = bundle_index(archive)
            def member(item):
                location = manifest['members'][item['path']]
                return archive.open('blobs/'+location['sha256'])
            yield manifest.get('title'),manifest['pages'],member,manifest
        elif source.kind=='fixed-epub':
            from .fixed_pages import page_sequence
            title,pages = page_sequence(archive)
            indexed = {item.index:item for item in archive.members()}
            yield title,pages,lambda item:archive.open_member(indexed[item['entry']]),None
        else:
            entries = list(archive.members())
            indexed = {item.index:item for item in entries}
            def member(item):
                return archive.open_member(indexed[item['entry']])
            if source.entry is not None:
                title,pages = archive_order(archive,entries,source.entry)
                yield title,pages,member,None
                return
            from .publication_package import archive_packages

            _,claimed = archive_packages(archive,entries)
            image_names = []
            for item in entries:
                if item.name.startswith('__MACOSX/') or item.index in claimed:
                    continue
                if PurePosixPath(item.name).suffix.lower() in IMAGE_SUFFIXES:
                    image_names.append(item)
                elif source.kind=='zip':
                    with archive.open_member(item) as candidate:
                        if image_magic(candidate.read(16)):
                            image_names.append(item)
            by_name = {}
            for item in entries:
                by_name.setdefault(item.name,[]).append(item)
            pages = []
            for item in sorted(image_names,key=lambda candidate:(natural(candidate.name),candidate.index)):
                row = {'path':item.name,'entry':item.index,'occurrence':f'm{item.index:06d}'}
                candidates = [match for ext in SIDECAR_ENDINGS
                              for match in by_name.get(str(PurePosixPath(item.name).with_suffix(ext)),[])]
                if len(candidates)==1:
                    row['transcription'] = {'path':candidates[0].name,'entry':candidates[0].index}
                elif candidates:
                    row['transcription_alternatives'] = [match.name for match in candidates]
                pages.append(row)
            yield None,pages,member,None


def prepare_image(image,frame,crop):
    image.seek(frame)
    raw_size = image.size
    if raw_size[0]*raw_size[1]>MAX_PIXELS:
        raise Error('ocr_pixels', 'This page exceeds the per-image decode budget. Supply a smaller scan of the same page.')
    orientation = image.getexif().get(274,1)
    oriented = ImageOps.exif_transpose(image)
    try:
        width,height = oriented.size
        bounds = [0,0,width,height]
        if crop is not None:
            if (not isinstance(crop,list) or len(crop)!=4
                    or not all(type(n) in (float,int) and math.isfinite(n) and 0<=n<=1 for n in crop)
                    or not (crop[0]<crop[2] and crop[1]<crop[3])):
                raise Error('source_crop', 'Expected a nonempty normalized [left, top, right, bottom] crop.')
            bounds = [math.floor(crop[0]*width),math.floor(crop[1]*height),
                      math.ceil(crop[2]*width),math.ceil(crop[3]*height)]
        with oriented.crop(bounds) as portion:
            if portion.mode in ('RGBA','LA','PA') or 'transparency' in portion.info:
                with portion.convert('RGBA') as foreground:
                    result = Image.new('RGB',foreground.size,'white')
                    with foreground.getchannel('A') as alpha:
                        result.paste(foreground,mask=alpha)
            else:
                result = portion.convert('RGB')
        return result,{'raw_size':list(raw_size),'oriented_size':[width,height],'orientation':orientation,
                       'crop_pixels':bounds,'coordinate_space':'EXIF-oriented top-left pixels'}
    finally:
        if oriented is not image:
            oriented.close()


def location(base,rectangle,transform):
    x,y,_,_ = transform['crop_pixels']
    height = transform['crop_pixels'][3]-y
    left,bottom,right,top = rectangle
    region = [x+left,y+height-top,x+right,y+height-bottom]
    return {**base,'rect':rectangle,'source_region':region,'image_transform':transform}


def scan(path,source,sha,cache=None,title=None):
    from .importers import block
    from .scan_sidecars import transcription

    observer = Observer(cache)
    inventory,blocks = [],[]
    chapter, chapter_id = 'Opening','scan:opening'
    with sequence(path,source) as (authored_title,pages,open_member,manifest):
        if not pages:
            raise Error('empty_document', 'No supported page images were found in the archive.')
        for page in pages:
            try:
                with ExitStack() as opened_files:
                    opened = opened_files.enter_context(open_member(page))
                    if not opened.seekable():
                        spool = opened_files.enter_context(tempfile.TemporaryFile(dir=cache.parent if cache else None))
                        budget = shutil.disk_usage(cache.parent if cache else tempfile.gettempdir()).free
                        left = min(MAX_MEMBER_SPOOL,budget)
                        while chunk := opened.read(CHUNK):
                            left -= len(chunk)
                            if left<0:
                                raise Error('source_member_size','An archive page exceeds the bounded decoder workspace.')
                            spool.write(chunk)
                        spool.seek(0)
                        opened = spool
                    with Image.open(opened) as image:
                        frames = range(getattr(image,'n_frames',1)) if 'frame' not in page else [page['frame']]
                        for frame in frames:
                            ordinal = len(inventory)+1
                            occurrence = page['occurrence']
                            if 'frame' not in page and len(frames)>1:
                                occurrence += f':frame-{frame}'
                            locator = {'format':'scan','page':ordinal,'occurrence':occurrence,
                                       'member':page['path'],'frame':frame}
                            if page.get('entry') is not None:
                                locator['archive_entry'] = page['entry']
                            if manifest:
                                locator['member_sha256'] = manifest['members'][page['path']]['sha256']
                            entry = {**locator,'label':page.get('label'),'source_role':page.get('role','body')}
                            if type(frame) is not int or frame<0:
                                raise Error('source_frame', 'A frame number must be a nonnegative integer.')
                            try:
                                picture, transform = prepare_image(image,frame,page.get('crop'))
                            except (EOFError, OSError, Error, ValueError) as exc:
                                add_failure(blocks,inventory,entry,chapter,chapter_id,exc)
                                continue
                            with picture:
                                width,height = picture.size
                                entry['size'] = [width,height]
                                entry['image_transform'] = transform
                                rect = [0,0,width,height]
                                if page.get('role')=='cover':
                                    inventory.append({**entry,'status':'excluded','reason':'Authored cover designation.'})
                                    cover = location(locator,rect,transform)
                                    blocks.append(block('',{**cover,'chapter_id':chapter_id},'cover',chapter))
                                    continue
                                words = None
                                side = page.get('transcription')
                                if side and not page.get('crop') and len(frames)==1 and transform['orientation']==1:
                                    try:
                                        with open_member(side) as stream:
                                            words, evidence = transcription(bounded(stream,MAX_MANIFEST),side,
                                                                            page['path'],picture.size)
                                        entry['sidecar'] = evidence
                                    except (Error, OSError, KeyError, BadZipFile) as exc:
                                        entry['sidecar'] = {'member':side['path'],'status':'unresolved','reason':str(exc)}
                                elif side:
                                    entry['sidecar'] = {'member':side['path'],'status':'unresolved',
                                                        'reason':'Source coordinates need a qualified crop, frame or orientation binding.'}
                                if page.get('transcription_alternatives'):
                                    entry['transcription_alternatives'] = page['transcription_alternatives']
                                if words:
                                    parts = paragraphs(words,ordinal,height,width=width,scale=1,format='scan')
                                    evidence = {'status':'observed','route':'supplied_text'}
                                else:
                                    parts,evidence = observer.read_raster(lambda:picture,None,
                                        {**entry,'native_geometry_qualified':False},rect,format='scan',scale=1)
                                    evidence['route'] = 'ocr'
                            for position,part in enumerate(parts):
                                role = part.get('role','body')
                                if position==0 and not part['issue'] and CHAPTER.fullmatch(part['text']):
                                    chapter,chapter_id,role = part['text'].strip(),'scan:'+occurrence,'heading'
                                loc = {**location(locator,part['locator']['rect'],transform),'chapter_id':chapter_id,
                                       'location_id':key([occurrence,frame,position])[:20],
                                       **{k:v for k,v in part['locator'].items()
                                          if k in ('ocr_layout','ocr_confidence','table_grid','rows','table_evidence','observed_table_rows',
                                                   'ocr_word_indices')}}
                                cells = loc.get('table_grid',{}).get('cells',())
                                unresolved = [cell for row in loc.get('observed_table_rows',()) for cell in row]
                                for cell in (*cells,*unresolved):
                                    inner = cell['location']
                                    cell['location'] = {**location(locator,inner['rect'],transform),**inner}
                                if part.get('issue'):
                                    evidence['status'] = 'needs_review'
                                item = block(part['text'],loc,role,chapter,issue=part.get('issue'))
                                if evidence['route']=='supplied_text':
                                    item.locator['verified_transcription'] = True
                                blocks.append(item)
                            inventory.append({**entry,**evidence})
                            fault('after_intake_page')
            except (Error, UnidentifiedImageError, Image.DecompressionBombError, BadZipFile, OSError, ValueError) as exc:
                add_failure(blocks,inventory,{'format':'scan','page':len(inventory)+1,'member':page['path'],
                    'occurrence':page['occurrence'],'archive_entry':page.get('entry'),'frame':page.get('frame')},
                            chapter,chapter_id,exc)
    good_pages = [row for row in inventory if row.get('size')]
    mark_folios(blocks,good_pages)
    mark_headers(blocks,good_pages)
    metadata = {'source_capabilities':['paginated','image_regions'],'page_count':len(inventory)}
    return Document(authored_title or title or 'Scanned book','scan',sha,blocks,
                    inventory=inventory,metadata=metadata)


def add_failure(blocks,inventory,entry,chapter,chapter_id,error):
    from .importers import block

    detail = error.message if isinstance(error,Error) else str(error)
    message = f"Could not read page {entry['page']} ({entry['member']}). {detail}"
    inventory.append({**entry,'status':'failed','reason':message})
    locator = {**entry,'chapter_id':chapter_id,
               'location_id':key([entry['occurrence'],entry.get('frame'),'failure'])[:20]}
    blocks.append(block('',locator,'unreadable',chapter,issue=message))
