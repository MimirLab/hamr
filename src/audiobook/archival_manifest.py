"""Translate explicit archival page order into the source bundle manifest."""
from urllib.parse import unquote, urlsplit

from .core import Error
from .document_intake import attr, name, xml
from .source_bundle import IMAGE_SUFFIXES, safe_name, source_suffix


def resource(value, mapping):
    if not isinstance(value,str) or not value:
        raise Error('source_member_missing','An archival page has no file reference.')
    if value in mapping:
        return safe_name(mapping[value])
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise Error('source_member_missing', f'Bind the archived resource {value} to its local file.')
    return safe_name(unquote(parsed.path).removeprefix('./'))


def iiif(data):
    if data.get('type')!='Manifest':
        raise Error('source_manifest','Use a IIIF Presentation 3 Manifest with local resources.')
    mapping = data.get('hamr:localResources',{})
    if not isinstance(mapping,dict):
        raise Error('source_manifest','IIIF local resources must map identifiers to relative files.')
    canvases = {page.get('id'):page for page in data.get('items',[]) if page.get('type')=='Canvas'}
    ranges = [node for node in data.get('structures',[]) if 'sequence' in node.get('behavior',[])]
    if ranges:
        order = [canvases.get(ref.get('id')) for ref in ranges[0].get('items',[])]
        if not order or any(not page for page in order):
            raise Error('source_manifest','The IIIF sequence names a missing Canvas or unsupported nested range.')
    else:
        if 'unordered' in data.get('behavior',[]):
            raise Error('source_manifest','This IIIF manifest needs an ordered sequence.')
        order = list(canvases.values())
    pages = []
    for index,canvas in enumerate(order,1):
        bodies = [annotation.get('body') for item in canvas['items']
                  for annotation in item.get('items',[]) if annotation.get('motivation')=='painting']
        if len(bodies)!=1 or not isinstance(bodies[0],dict) or bodies[0].get('type')!='Image':
            raise Error('source_manifest',f'IIIF Canvas {index} needs one selected local image.')
        body, = bodies
        label = language_label(canvas.get('label')) or str(index)
        row = {'occurrence':f'canvas-{index}', 'path':resource(body.get('id'),mapping),'label':label}
        pages.append(row)
    return {'title':language_label(data.get('label')) or 'IIIF book','pages':pages}


def language_label(label):
    if isinstance(label,dict):
        for key in ('en','none',*label):
            if isinstance(label.get(key),list) and label[key] and isinstance(label[key][0],str):
                return label[key][0]
    return None


def mets(data):
    root = xml(data)
    if name(root)!='mets':
        raise Error('source_manifest','Use an archival METS package.')
    files = {}
    for group in (item for item in root.iter() if name(item)=='fileGrp'):
        for item in (element for element in group if name(element)=='file'):
            loc = next((child for child in item if name(child)=='FLocat'),None)
            if loc is not None:
                files[attr(item,'ID')] = (attr(loc,'href') or attr(loc,'hrefLocation'),
                                          attr(group,'USE') or '',attr(item,'MIMETYPE') or '')
    maps = [element for element in root if name(element)=='structMap'
            and (attr(element,'TYPE') or '').lower()=='physical']
    if len(maps)!=1:
        raise Error('source_manifest','Choose one physical METS page sequence.')
    nodes = [node for node in maps[0].iter() if name(node)=='div'
             and any(name(item)=='fptr' for item in node)]
    if nodes and all((attr(node,'ORDER') or '').isdigit() for node in nodes):
        nodes.sort(key=lambda node:int(attr(node,'ORDER')))
    pages = []
    for index,node in enumerate(nodes,1):
        paths = [files.get(attr(pointer,'FILEID')) for pointer in node if name(pointer)=='fptr']
        if any(item is None for item in paths):
            raise Error('source_member_missing',f'METS page {index} refers to an unavailable member.')
        pictures = [item for item in paths if item[2].startswith('image/')
                    or isinstance(item[0],str) and source_suffix(item[0]) in IMAGE_SUFFIXES]
        masters = [item for item in pictures if 'master' in item[1].lower()]
        if len(masters or pictures)!=1:
            raise Error('source_manifest',f'METS page {index} needs one selected image.')
        file,_,_ = (masters or pictures)[0]
        row = {'occurrence':f'mets-{index}','path':resource(file,{}),
               'label':attr(node,'ORDERLABEL') or attr(node,'LABEL') or str(index)}
        transcriptions = [item for item in paths if item not in pictures and item[0]
                          and ('ocr' in item[1].lower() or 'alto' in item[1].lower()
                               or item[0].lower().endswith(('.hocr','.alto.xml','.page.xml')))]
        if len(transcriptions)==1:
            row['transcription'] = {'path':resource(transcriptions[0][0],{})}
        elif transcriptions:
            raise Error('source_manifest',f'METS page {index} lists several OCR alternatives.')
        pages.append(row)
    return {'title':attr(root,'LABEL') or 'METS book','pages':pages}
