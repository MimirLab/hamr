"""Inventory PDF text objects declared nonpainting by the renderer."""


def nonpainting_regions(page, crop):
    import pypdfium2.raw as pdfium

    left, bottom, right, top = crop
    regions = []
    for obj in page.get_objects(filter=[pdfium.FPDF_PAGEOBJ_TEXT]):
        mode = pdfium.FPDFTextObj_GetTextRenderMode(obj)
        if mode not in (pdfium.FPDF_TEXTRENDERMODE_INVISIBLE, pdfium.FPDF_TEXTRENDERMODE_CLIP):
            continue
        bounds = obj.get_bounds()
        if bounds[2] > left and bounds[0] < right and bounds[3] > bottom and bounds[1] < top:
            regions.append({'rect': list(bounds), 'render_mode': mode})
    return regions
