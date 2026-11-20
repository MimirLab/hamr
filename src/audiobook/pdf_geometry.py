"""Visible unrotated CropBox geometry; unsupported rotations abstain."""


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
