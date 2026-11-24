"""Visible unrotated CropBox geometry; unsupported rotations abstain."""
from ctypes import byref, c_double, c_int, create_string_buffer
import re

from pypdfium2 import raw


NOTE_ICON = re.compile(r'(?:^|\n)(?P<icon>\uf05a)\r?\n(?:A (?:SHORT )?)?NOTE\b', re.I)
ICON_FONT = re.compile(rb'(?:[A-Z]{6}\+)?FontAwesome5Free-Solid')


def redundant_note_icons(textpage,source):
    """Account for a standard information icon beside an authored Note label."""
    if '\uf05a' not in source or len(source)!=textpage.count_chars():
        return []
    artifacts = []
    for match in NOTE_ICON.finditer(source):
        index = match.start('icon')
        buffer = create_string_buffer(128)
        raw.FPDFText_GetFontInfo(textpage,index,buffer,len(buffer),byref(c_int()))
        if not ICON_FONT.fullmatch(buffer.value) or textpage.get_text_range(index,1)!='\uf05a':
            continue
        rect = textpage.get_charbox(index)
        label = textpage.get_charbox(index+3 if source[index+1:index+3]=='\r\n' else index+2)
        if rect[2]<label[0]:
            artifacts.append({'native_index':index,'codepoint':'U+F05A','rect':list(rect),
                              'basis':'Font Awesome information glyph paired with an adjacent authored Note label'})
    return artifacts


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
