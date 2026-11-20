"""Localize competing critical observations only with complete region coverage.

Unqualified coordinates or incomplete/duplicate critical spans keep the coarse
page hold. Neither the native layer nor visual OCR is selected as truth.
"""
from collections import Counter
from .pdf_fidelity import critical_signature,disputes

ISSUE='Native and visual OCR disagree on critical values or mathematical notation in this source region. Verify the original source.'


def glyph_reader(textpage):
    """Use glyph centres, not intersecting character boxes from adjacent lines.

    PDFium's bounded text query can include a neighbouring line whose tall
    hidden glyph box touches an OCR ink rectangle. Complete critical coverage
    remains mandatory in localize; this reader never selects a winning text.
    """
    glyphs=[]
    for index in range(textpage.count_chars()):
        text=textpage.get_text_range(index,1)
        if not text or text.isspace():continue
        left,bottom,right,top=textpage.get_charbox(index)
        glyphs.append((index,text,(left+right)/2,(bottom+top)/2))
    def read(rect):
        left,bottom,right,top=rect;result=[];previous=None
        for index,text,x,y in glyphs:
            if left-1<=x<=right+1 and bottom-1<=y<=top+1:
                if previous is not None and index!=previous+1:result.append(' ')
                result.append(text);previous=index
        return ''.join(result)
    return read


def combined(signatures):
    result={kind:Counter() for kind in ('numbers','symbols','typed_math','word_forms','regnal_numbers')}
    for signature in signatures:
        for kind,values in signature.items():result[kind].update(values)
    return {kind:dict(sorted(values.items())) for kind,values in result.items()}


def localize(parts,native,visual,read_region,qualified=True):
    page_disputes=disputes(native,visual);regions=[]
    if qualified and native.strip():
        try:
            for part in parts:
                observed=read_region(part['locator']['rect'])
                regions.append({'paragraph':part['locator'].get('paragraph'),'rect':part['locator']['rect'],
                                'native_observation':observed,'critical_disputes':disputes(observed,part['text'])})
        except (ValueError,RuntimeError):regions=[]
    native_coverage=bool(regions) and combined(critical_signature(r['native_observation']) for r in regions)==critical_signature(native)
    visual_coverage=bool(regions) and combined(critical_signature(p['text']) for p in parts)==critical_signature(visual)
    precise=native_coverage and visual_coverage
    if precise:
        for part,region in zip(parts,regions):
            if region['critical_disputes']:
                part['issue']=ISSUE
                # Preserve existing occurrence IDs: new reading diagnostics
                # live in the inventory, rather than changing the locator used
                # by earlier source-bound policies.
                legacy=[d for d in region['critical_disputes'] if d['kind'] not in ('word_forms','regnal_numbers')]
                if legacy:part['locator']['critical_disputes']=legacy
    elif page_disputes or any(r['critical_disputes'] for r in regions):
        for part in parts:part['issue']='Native and visual OCR have an unlocalized critical disagreement; verify this page.'
    return {'scope':'paragraph' if precise else 'page','qualified_geometry':qualified,
            'native_critical_coverage':native_coverage,'visual_critical_coverage':visual_coverage,
            'disputed_regions':sum(bool(r['critical_disputes']) for r in regions),'regions':regions}
