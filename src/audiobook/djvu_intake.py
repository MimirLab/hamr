"""Keep DjVu page inventory and use the shared OCR observer for raster pages."""
from pathlib import Path
import platform
import sys
import tempfile

from PIL import Image

from .core import Document, Error, fault, filehash, key
from .intake_helpers import convert
from .pdf_layout import paragraphs
from .pdf_visual import Observer, read_words
from .scan_sidecars import row, tsv


def djvu(path,source,sha,cache,title):
    from .importers import block

    tool = Path(__file__).with_name('intake_resident')/'djvu-linux-x86_64'
    if sys.platform!='linux' or platform.machine().lower() not in ('x86_64','amd64') or not tool.is_file():
        raise Error('source_helper_unavailable','This platform build has no tested DjVu runtime.',
                    'Supply a readable PDF or scan bundle.')
    if filehash(tool)!='dc0f35ae2e2170bc3a2b4c0e7c2a16abe8608c79cc0aa6bd794ae3bb20e0f357':
        raise Error('asset_integrity','The shipped DjVu reader has changed.')
    blocks,inventory = [],[]
    observer = Observer(cache/'scan-observations' if cache else None)
    with tempfile.TemporaryDirectory(prefix='hamr-djvu-',dir=cache) as folder:
        work = Path(folder)
        result = work/'page-record'

        def read(mode,index):
            if result.exists():
                result.unlink()
            convert(path,work,tool,[mode,'/input/'+path.name,str(index),'/work/page-record'],
                    result,runtime=tool.parent)
            return result

        try:
            count = int(read('count',0).read_text(encoding='ascii').strip())
            if count<1:
                raise ValueError
        except (OSError,ValueError) as exc:
            raise Error('unreadable_djvu','The DjVu decoder could not establish a page inventory.') from exc

        for index in range(count):
            locator = {'format':'scan','page':index+1,'member':source.member_name or path.name,
                       'occurrence':f'djvu-{index+1}'}
            entry = dict(locator)
            try:
                record = read('text',index)
                if record.stat().st_size>8*1024*1024:
                    raise Error('source_structure_size','The DjVu text layer exceeds the per-page memory budget.')
                data = record.read_text(encoding='utf-8').splitlines()
                if data==['no-text']:
                    units = []
                else:
                    width,height = map(int,data[0].split('\t'))
                    rows = []
                    for position,line in enumerate(data[1:],1):
                        para,line_no,left,bottom,right,top,text = line.split('\t',6)
                        bounds = int(left),height-int(top),int(right),height-int(bottom)
                        rows.append(row(int(para),int(line_no),text,bounds,position))
                    units = read_words(tsv(rows),(width,height))
                if not units:
                    read('image',index)
                    with Image.open(result) as source_image, source_image.convert('RGB') as picture:
                        width,height = picture.size
                        entry['size'] = [width,height]
                        parts,evidence = observer.read_raster(lambda:picture,None,
                            {**entry,'native_geometry_qualified':False},[0,0,width,height],
                            format='scan',scale=1)
                    entry.update(evidence,route='ocr')
                else:
                    entry.update(size=[width,height],status='observed',route='native_text')
                    parts = paragraphs(units,index+1,height,width=width,scale=1,format='scan')
                for position,part in enumerate(parts):
                    loc = {**locator,**part['locator'],'chapter_id':'djvu:opening',
                           'location_id':key([sha,index,position])[:20]}
                    blocks.append(block(part['text'],loc,part.get('role','body'),issue=part.get('issue')))
                if any(item.get('issue') for item in parts):
                    entry['status'] = 'needs_review'
            except (Error,OSError,ValueError,IndexError) as exc:
                reason = f'DjVu page {index+1}: {exc}'
                entry.update(status='failed',reason=reason)
                blocks.append(block('',{**locator,'location_id':key([sha,index])[:20]},
                                    'unreadable',issue=reason))
            inventory.append(entry)
            fault('after_intake_page')
    return Document(title,'djvu',sha,blocks,inventory=inventory,
                    metadata={'source_capabilities':['paginated','native_text','raster_ocr'],
                              'decoder':{'engine':'DjVuLibre','binary_sha256':filehash(tool)}})
