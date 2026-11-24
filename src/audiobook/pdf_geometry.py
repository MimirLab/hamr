"""Visible unrotated CropBox geometry; unsupported rotations abstain."""
from ctypes import byref, c_double, c_int, create_string_buffer
import re

from pypdfium2 import raw


NOTE_ICON = re.compile(r'(?:^|\n)(?P<icon>\uf05a)\r?\n(?:A (?:SHORT )?)?NOTE\b', re.I)
TIP_ICON = re.compile(r'(?:^|\n)(?P<icon>\uf0eb)\r?\n(?P<heading>[A-Za-z])')
ICON_FONT = re.compile(rb'(?:[A-Z]{6}\+)?FontAwesome5Free-(?:Solid|Regular)')


def known_icon(textpage,index,value):
    buffer = create_string_buffer(128)
    raw.FPDFText_GetFontInfo(textpage,index,buffer,len(buffer),byref(c_int()))
    return ICON_FONT.fullmatch(buffer.value) and textpage.get_text_range(index,1)==value


def redundant_note_icons(textpage,source):
    """Account for a standard information icon beside an authored Note label."""
    if '\uf05a' not in source or len(source)!=textpage.count_chars():
        return []
    artifacts = []
    for match in NOTE_ICON.finditer(source):
        index = match.start('icon')
        if not known_icon(textpage,index,'\uf05a'):
            continue
        rect = textpage.get_charbox(index)
        label = textpage.get_charbox(index+3 if source[index+1:index+3]=='\r\n' else index+2)
        if rect[2]<label[0]:
            artifacts.append({'native_index':index,'codepoint':'U+F05A','rect':list(rect),
                              'basis':'Font Awesome information glyph paired with an adjacent authored Note label'})
    return artifacts


def asciidoctor_tips(page,textpage,source,producer):
    """Bind the converter's standard Tip icon to its adjacent titled panel."""
    if '\uf0eb' not in source or not producer.startswith('Asciidoctor PDF ') or len(source)!=textpage.count_chars():
        return []
    rules = [path.get_bounds() for path in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH])]
    tips = []
    for match in TIP_ICON.finditer(source):
        index,target = match.start('icon'),match.start('heading')
        if not known_icon(textpage,index,'\uf0eb'):
            continue
        rect,heading = textpage.get_charbox(index),textpage.get_charbox(target)
        adjacent = [rule for rule in rules if rect[2]<rule[0]<rule[2]<heading[0] and rule[2]-rule[0]<=2
                    and rule[1]<rect[1]<rect[3]<rule[3] and rule[1]<heading[1]<heading[3]<rule[3]]
        if len(adjacent)==1:
            tips.append({'native_index':index,'heading_index':target,'codepoint':'U+F0EB','rect':list(rect),
                         'label':'Tip.','rule':list(adjacent[0]),'basis':'Asciidoctor PDF default Tip icon and divider'})
    return tips


def glyph_baseline(textpage,index,crop_bottom,fallback):
    x,y=c_double(),c_double()
    if raw.FPDFText_GetCharOrigin(textpage,index,byref(x),byref(y)):
        return y.value-crop_bottom
    return fallback


def crop_glyphs(textpage,box):
    left,bottom,right,top=box;inside=[];outside=[];clipped=False
    for index in range(textpage.count_chars()):
        ch=textpage.get_text_range(index,1)
        if not ch or ch in ('\r','\n'):continue
        l,b,r,t=textpage.get_charbox(index)
        if r<=left or l>=right or t<=bottom or b>=top:
            outside.append({'index':index,'text':ch,'rect':[l,b,r,t]});continue
        if ch.strip() and (l<left-.1 or r>right+.1 or b<bottom-.1 or t>top+.1):clipped=True
        inside.append((ch,(l-left,b-bottom,r-left,t-bottom),index))
    return inside,outside,clipped
