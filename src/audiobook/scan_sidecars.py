"""Conservative page-bound transcription readers with word geometry."""
from html.parser import HTMLParser
import re
from pathlib import PurePosixPath

from lxml import etree

from .core import Error, digest
from .pdf_visual import read_words

HOCR_BOX = re.compile(r'\bbbox\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)')


def local(tag):
    return tag.rsplit('}',1)[-1] if isinstance(tag,str) else ''


def bound_image(name,source,size,declared):
    if name and PurePosixPath(name.replace(chr(92),'/')).name.casefold()!=PurePosixPath(source).name.casefold():
        raise Error('ocr_binding', f'Transcription names a different image: {name}.')
    if declared and declared!=size:
        raise Error('ocr_binding', f'Transcription dimensions {declared} differ from the scan dimensions {size}.')


def row(paragraph,line,text,box,index):
    x,y,right,bottom = box
    return ['5','1','1',str(paragraph),str(line),str(index),str(x),str(y),
            str(right-x),str(bottom-y),'100',text]


def point_box(points):
    if not points:
        raise Error('ocr_binding','Transcription has no geometry for a selected line.')
    pairs = [tuple(map(int,pair.split(','))) for pair in points.split()]
    x,y = zip(*pairs,strict=True)
    return min(x),min(y),max(x),max(y)


def alto(root,source,size):
    pages = [node for node in root.iter() if local(node.tag)=='Page']
    if len(pages)!=1:
        raise Error('ocr_binding','The ALTO transcription must identify exactly one page.')
    page, = pages
    name = next((n.text for n in root.iter() if local(n.tag)=='fileName' and n.text),None)
    bound_image(name,source,size,(int(page.get('WIDTH')),int(page.get('HEIGHT'))))
    result = []
    blocks = {node.get('ID'):node for node in page.iter() if local(node.tag)=='TextBlock'
              and node.get('ID')}
    references = [node for node in page.iter() if local(node.tag) in ('RegionRefIndexed','ElementRef')
                  and (node.get('REGIONREF') or node.get('REF')) in blocks]
    usable = [blocks[node.get('REGIONREF') or node.get('REF')] for node in
              sorted(references,key=lambda node:int(node.get('INDEX','0')))]
    default = [node for node in page.iter() if local(node.tag)=='TextBlock']
    if usable and (len(usable)!=len(default) or len(set(usable))!=len(usable)):
        raise Error('ocr_binding','ALTO reading order omits or repeats a text region.')
    for paragraph,block in enumerate(usable or default,1):
        lines = [node for node in block if local(node.tag)=='TextLine']
        for line_id,line in enumerate(lines,1):
            for order,item in enumerate((n for n in line if local(n.tag)=='String'),1):
                text = item.get('CONTENT')
                if not text:
                    raise Error('ocr_binding','ALTO lists a word with missing text.')
                x,y,w,h = (int(float(item.get(field))) for field in ('HPOS','VPOS','WIDTH','HEIGHT'))
                result.append(row(paragraph,line_id,text,(x,y,x+w,y+h),order))
    return result


def page_xml(root,source,size):
    pages = [node for node in root.iter() if local(node.tag)=='Page']
    if len(pages)!=1:
        raise Error('ocr_binding','PAGE XML must identify exactly one page.')
    page, = pages
    bound_image(page.get('imageFilename'),source,size,
                (int(page.get('imageWidth')),int(page.get('imageHeight'))))
    regions = {item.get('id'):item for item in page if local(item.tag)=='TextRegion'}
    order = sorted((e for e in page.iter() if local(e.tag)=='RegionRefIndexed'),
                   key=lambda n:int(n.get('index','0')))
    references = [n.get('regionRef') for n in order if n.get('regionRef') in regions]
    if references and (len(references)!=len(regions) or len(set(references))!=len(regions)):
        raise Error('ocr_binding','PAGE reading order omits or repeats a text region.')

    def text_of(node):
        candidates = sorted((n for n in node if local(n.tag)=='TextEquiv'),key=lambda n:int(n.get('index','0')))
        if not candidates:
            return ''
        return next((n.text for n in candidates[0] if local(n.tag)=='Unicode' and n.text),'')

    result = []
    for paragraph,region_id in enumerate(references or regions,1):
        region = regions[region_id]
        lines = [n for n in region if local(n.tag)=='TextLine']
        for line_id,line in enumerate(lines or [region],1):
            for index,word in enumerate([n for n in line if local(n.tag)=='Word'] or [line],1):
                text = text_of(word)
                if not text:
                    raise Error('ocr_binding','PAGE XML has missing text in a selected region.')
                coords = next((n.get('points') for n in word if local(n.tag)=='Coords'),None)
                result.append(row(paragraph,line_id,text,point_box(coords),index))
    return result


def abbyy(root,source,size):
    def children(parent,tag):
        return [node for node in parent if local(node.tag)==tag]

    pages = children(root,'page')
    if len(pages)!=1:
        raise Error('ocr_binding','Bind one ABBYY XML page to its original image.')
    page, = pages
    if page.get('originalCoords') not in ('true','1') or page.get('rotation','Normal')!='Normal':
        raise Error('ocr_binding','ABBYY XML needs unrotated original-image coordinates.')
    bound_image(page.get('imageFilename'),source,size,(int(page.get('width')),int(page.get('height'))))
    blocks = children(page,'block')
    if any(block.get('blockType')!='Text' or block.get('isHidden') in ('1','true') for block in blocks):
        raise Error('ocr_binding','ABBYY XML with mixed or hidden blocks needs a layout-qualified reading.')
    paragraphs = [par for block in blocks for text in children(block,'text')
                  for par in children(text,'par')]
    result = []
    for par_index,par in enumerate(paragraphs,1):
        for line_index,line in enumerate(children(par,'line'),1):
            letters = [letter for run in children(line,'formatting') for letter in children(run,'charParams')]
            line_rows = []
            word, boxes = [], []

            def finish_word():
                if boxes:
                    left,top,right,bottom = zip(*boxes,strict=True)
                    box = min(left),min(top),max(right),max(bottom)
                    line_rows.append(row(par_index,line_index,''.join(word),box,len(line_rows)+1))
                    word.clear()
                    boxes.clear()

            if not letters and ''.join(line.itertext()).strip():
                raise Error('ocr_binding','ABBYY XML omits character coordinates.')
            for letter in letters:
                character = letter.text or ''
                if not character.strip() or any(letter.get(field) in ('true','1')
                                                for field in ('wordFirst','wordStart')):
                    finish_word()
                if not character.strip():
                    continue
                if len(character)!=1 or letter.get('suspicious') in ('true','1'):
                    raise Error('ocr_binding','ABBYY XML contains an unresolved character reading.')
                x,y,r,b = box = tuple(int(letter.get(field)) for field in ('l','t','r','b'))
                if not (0<=x<r<=size[0] and 0<=y<b<=size[1]):
                    raise Error('ocr_binding','ABBYY XML character geometry falls outside the image.')
                word.append(character)
                boxes.append(box)
            finish_word()
            result.extend(line_rows)
    return result


class Hocr(HTMLParser):
    """Keep word text once even when an enclosing line repeats its markup."""
    def __init__(self):
        super().__init__()
        self.tags = []
        self.pages = []
        self.rows = []
        self.line,self.paragraph = 0,0
        self.word = None

    def handle_starttag(self,tag,attrs):
        values = dict(attrs)
        roles = set(values.get('class','').split())
        title = values.get('title','')
        if tag not in ('meta','link','img','br','input','hr','area','base','wbr','col'):
            self.tags.append((tag,roles))
        if 'ocr_page' in roles:
            bbox = HOCR_BOX.search(title)
            if not bbox:
                raise Error('ocr_binding','hOCR page has no bounds.')
            left,top,right,bottom = map(int,bbox.groups())
            if left or top:
                raise Error('ocr_binding','hOCR uses a nonzero page origin.')
            image = re.search(r'\bimage\s+"([^"]+)"',title)
            self.pages.append((image.group(1) if image else '',(right,bottom)))
        if 'ocr_par' in roles or 'ocr_carea' in roles:
            self.paragraph += 1
        if 'ocr_line' in roles or 'ocrx_line' in roles:
            self.line += 1
        if roles & {'ocr_word','ocrx_word'}:
            box = HOCR_BOX.search(title)
            if not box or self.word:
                raise Error('ocr_binding','Invalid nested or unlocated hOCR word.')
            self.word = list(map(int,box.groups())),[]

    def handle_data(self,text):
        if self.word:
            self.word[1].append(text)

    def handle_endtag(self,tag):
        if self.tags and self.tags[-1][0]==tag:
            _,roles = self.tags.pop()
            if roles & {'ocr_word','ocrx_word'} and self.word:
                box,parts = self.word
                text = ''.join(parts).strip()
                if text:
                    self.rows.append(row(self.paragraph,self.line,text,box,len(self.rows)+1))
                self.word = None


def transcription(data, side, source, size):
    kind = (side.get('format') or '').lower()
    try:
        if kind=='tsv' or side['path'].lower().endswith('.tsv'):
            text = data.decode('utf-8-sig')
            if not text.startswith('level\tpage_num\t'):
                raise Error('ocr_binding','Unrecognized Tesseract TSV header.')
            kind = 'tsv'
        elif kind=='hocr' or side['path'].lower().endswith('.hocr'):
            reader = Hocr()
            reader.feed(data.decode('utf-8-sig'))
            reader.close()
            if len(reader.pages)!=1:
                raise Error('ocr_binding','hOCR must identify exactly one page.')
            bound_image(reader.pages[0][0],source,size,reader.pages[0][1])
            kind,text = 'hocr',tsv(reader.rows)
        else:
            root = etree.fromstring(data,etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False))
            tag = local(root.tag)
            if tag.lower()=='alto':
                kind,text = 'alto',tsv(alto(root,source,size))
            elif tag=='PcGts':
                kind,text = 'pagexml',tsv(page_xml(root,source,size))
            elif tag=='document' and (root.nsmap.get(root.prefix) or '').startswith(
                    ('http://www.abbyy.com/','https://www.abbyy.com/')):
                kind,text = 'abbyy',tsv(abbyy(root,source,size))
            else:
                raise Error('ocr_binding', 'Unknown archival OCR XML root.')
        words = read_words(text,size)
        if not words:
            raise Error('ocr_binding','The selected transcription has no readable words.')
        if len({part[1] for part in words})!=1:
            raise Error('ocr_binding','Bind only one transcribed page to an image.')
    except (ValueError, TypeError, etree.XMLSyntaxError) as exc:
        raise Error('ocr_binding','The sidecar cannot be bound to this image.') from exc
    return words,{'status':'selected','format':kind,'member':side['path'],'sha256':digest(data),
                  'geometry_checked':True,'word_count':len(words)}


def tsv(rows):
    return 'level\tpage_num\n'+''.join('\t'.join(row)+'\n' for row in rows)
