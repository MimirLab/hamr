"""Read page-local PDF structure without losing document-wide child positions."""
from collections import defaultdict
from ctypes import byref, c_float, c_ulong, create_string_buffer
from dataclasses import dataclass

from pypdfium2 import raw


INLINE_TAGS = {'Span', 'Quote', 'Link', 'Em', 'Strong'}


@dataclass(frozen=True,slots=True)
class Mark:
    page: int
    ident: int


@dataclass(frozen=True,slots=True)
class Tag:
    role: str
    path: tuple
    children: tuple
    attributes: tuple = ()


def wide_text(getter,*arguments):
    size = getter(*arguments,None,0)
    if not 2<=size<=8192:
        return ''
    buffer = create_string_buffer(size)
    if getter(*arguments,buffer,size)!=size:
        return ''
    return buffer.raw[:size-2].decode('utf-16-le',errors='replace')


def attribute_string(value):
    size = c_ulong()
    getter = raw.FPDF_StructElement_Attr_GetStringValue
    if not getter(value,None,0,byref(size)) or not 2<=size.value<=8192:
        return None
    buffer = create_string_buffer(size.value)
    if not getter(value,buffer,len(buffer),byref(size)) or not 2<=size.value<=len(buffer):
        return None
    return buffer.raw[:size.value-2].decode('utf-16-le',errors='replace')


def cell_attributes(element):
    evidence = defaultdict(set)
    for index in range(min(64,raw.FPDF_StructElement_GetAttributeCount(element))):
        attr = raw.FPDF_StructElement_GetAttributeAtIndex(element,index)
        if not attr:
            continue
        for name in ('RowSpan','ColSpan','Scope','Headers'):
            value = raw.FPDF_StructElement_Attr_GetValue(attr,name.encode())
            if not value:
                continue
            if name in ('RowSpan','ColSpan'):
                number = c_float()
                found = raw.FPDF_StructElement_Attr_GetNumberValue(value,byref(number))
                data = number.value if found and number.value.is_integer() else None
            elif name=='Headers':
                count = raw.FPDF_StructElement_Attr_CountChildren(value)
                data = (tuple(attribute_string(raw.FPDF_StructElement_Attr_GetChildAtIndex(value,i))
                              for i in range(count)) if 0<count<=256 else None)
                if data and not all(data):
                    data = None
            else:
                data = attribute_string(value)
            evidence[name].add(data)
    values = {name:next(iter(options)) if len(options)==1 else None for name,options in evidence.items()}
    if identifier:=wide_text(raw.FPDF_StructElement_GetID,element):
        values['id'] = identifier
    return tuple(sorted(values.items()))


def page_tags(page,number):
    """Preserve unresolved slots so other pages can supply the same tree's missing children."""
    tree = raw.FPDF_StructTree_GetForPage(page)
    if not tree:
        return ()
    remaining = 10000

    def visit(element,path):
        nonlocal remaining
        count = raw.FPDF_StructElement_CountChildren(element)
        if not 0<=count<=remaining or len(path)>32:
            raise ValueError('PDF structure exceeds the page tree budget.')
        remaining -= count
        role = wide_text(raw.FPDF_StructElement_GetType,element)
        children = []
        for index in range(count):
            child = raw.FPDF_StructElement_GetChildAtIndex(element,index)
            if child:
                children.append(visit(child,(*path,index)))
            else:
                ident = raw.FPDF_StructElement_GetChildMarkedContentID(element,index)
                children.append(Mark(number,ident) if ident>=0 else None)
        attributes = cell_attributes(element) if role in ('TH','TD') else ()
        return Tag(role,path,tuple(children),attributes)
    try:
        count = raw.FPDF_StructTree_CountChildren(tree)
        if not 0<=count<=remaining:
            return ()
        remaining -= count
        return tuple(visit(node,(index,)) for index in range(count)
                     if (node:=raw.FPDF_StructTree_GetChildAtIndex(tree,index)))
    except ValueError:
        return ()
    finally:
        raw.FPDF_StructTree_Close(tree)


def paragraph_owners(tags):
    """Return unambiguous paragraph owners for page-local marked content IDs."""
    owners = defaultdict(set)

    def visit(node,paragraph):
        if isinstance(node,Mark):
            if paragraph is not None:
                owners[node.ident].add(paragraph)
        elif isinstance(node,Tag):
            if node.role=='P':
                paragraph = node.path
            elif node.role not in INLINE_TAGS:
                paragraph = None
            for child in node.children:
                visit(child,paragraph)

    for tag in tags:
        visit(tag,None)
    return {content_id: '.'.join(map(str, next(iter(paths)))) for content_id, paths in owners.items()
            if len(paths) == 1}


def line_paragraph(textpage, glyphs, owners):
    """Identify a paragraph only when it owns every visible glyph of the line."""
    groups = set()
    for _, _, character, _, index in glyphs:
        if character.isspace():
            continue
        obj = raw.FPDFText_GetTextObject(textpage, index)
        paragraph = owners.get(raw.FPDFPageObj_GetMarkedContentID(obj)) if obj else None
        if paragraph is None:
            return None
        groups.add(paragraph)
        if len(groups) > 1:
            return None
    return next(iter(groups), None)
