"""Keep presentation notes and raster observations in the common source model."""

from io import BytesIO
import re
from unicodedata import normalize

from PIL import Image

from .core import Error


IMAGE_FILENAME = re.compile(r'[^/\\]+\.(?:png|jpe?g|gif|tiff?|webp|bmp|svg|emf|wmf)',re.I)


def canonical(text):
    return ' '.join(normalize('NFC',text).split())


def visual_label(*labels):
    return next((text for label in labels if (text:=(label or '').strip())
                 and not IMAGE_FILENAME.fullmatch(text)),'')


def speaker_note(text,location,known,*,slide_member=None):
    loc = location|{'supplemental_note':True}
    if slide_member is not None:
        loc['slide_member'] = slide_member
    if prior:=known.get(canonical(text)):
        loc['duplicates_slide_passage'] = prior
        return 'note',loc
    return 'body',loc


def observe_picture(observer,data,location,crop=None):
    from .scan_intake import location as pixel_location, prepare_image

    try:
        with Image.open(BytesIO(data)) as source:
            if getattr(source,'n_frames',1)!=1:
                raise Error('source_slide_image','A slide image has more than one possible frame.')
            image,transform = prepare_image(source,0,crop)
        with image:
            size = image.size
            entries,receipt = observer.read_raster(lambda:image,None,
                {'page':location['slide'],'size':size,'native_geometry_qualified':False},[0,0,*size],
                format='scan',scale=1)
    except (OSError,ValueError,Image.DecompressionBombError) as error:
        raise Error('source_slide_image','The embedded slide raster could not be opened.') from error
    receipt['image_transform'] = transform
    entries = [(row,pixel_location(location,row['locator']['rect'],transform)) for row in entries]
    return entries,receipt
