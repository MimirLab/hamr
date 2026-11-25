"""Project the OpenOffice 1.x Writer dialect onto Hamr's OpenDocument visitor."""

from lxml import etree

from .odf_content import DRAW, FO, MANIFEST, OFFICE, STYLE, TABLE, TEXT, XLINK_HREF


OLD_OFFICE = '{http://openoffice.org/2000/office}'
NAMESPACES = {
    **{'{http://openoffice.org/2000/'+name+'}':modern for name,modern in
       (('office',OFFICE),('drawing',DRAW),('style',STYLE),('table',TABLE),('text',TEXT),
        ('svg','{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}'),
        ('meta','{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}'),
        ('datastyle','{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}'))},
    '{http://www.w3.org/1999/XSL/Format}':FO,
    '{http://openoffice.org/2001/manifest}':MANIFEST,
}
ELEMENTS = {
    TEXT+'ordered-list':TEXT+'list',TEXT+'unordered-list':TEXT+'list',
    TEXT+'tab-stop':TEXT+'tab',TABLE+'sub-table':TABLE+'table',
    **{TEXT+kind+suffix:TEXT+'note'+suffix for kind in ('footnote','endnote')
       for suffix in ('','-body','-citation')},
}
OBJECTS = frozenset(DRAW+name for name in ('image','text-box','object','object-ole'))
CELL_VALUES = ('value-type','value','string-value','boolean-value','date-value','time-value')
FORMULA_MEDIA = 'application/vnd.oasis.opendocument.formula'


def qname(tag):
    namespace,sep,local = tag.rpartition('}')
    return NAMESPACES.get(namespace+sep,namespace+sep)+local


def legacy_document(root):
    return root.tag in (OLD_OFFICE+'document-content',OLD_OFFICE+'document')


def project(root,locations):
    """Translate only reading-relevant structure and retain each original XPath."""
    tree = root.getroottree()
    elements = [item for item in root.iter() if isinstance(item.tag,str)]
    locations.update((item,tree.getpath(item)) for item in elements)
    for item in elements:
        tag = qname(item.tag)
        item.tag = ELEMENTS.get(tag,tag)
        for field,value in list(item.attrib.items()):
            projected = qname(field)
            if field!=projected:
                del item.attrib[field]
                if projected not in item.attrib:
                    item.set(projected,value)
        if tag==STYLE+'properties' and item.getparent().get(STYLE+'family')=='text':
            item.tag = STYLE+'text-properties'
        if tag in (TABLE+'table-cell',TABLE+'covered-table-cell'):
            for field in CELL_VALUES:
                previous = TABLE+field
                if previous in item.attrib:
                    value = item.attrib.pop(previous)
                    if OFFICE+field not in item.attrib:
                        item.set(OFFICE+field,value)
        if tag in (TEXT+'footnote',TEXT+'endnote'):
            item.set(TEXT+'note-class',tag.rsplit('}',1)[-1])
        if tag in OBJECTS and item.getparent() is not None and item.getparent().tag!=DRAW+'frame':
            parent = item.getparent()
            frame = etree.Element(DRAW+'frame')
            href = item.get(XLINK_HREF)
            if href and href.startswith('#'):
                item.set(XLINK_HREF,href[1:])
            for field in list(item.attrib):
                if not field.startswith('{http://www.w3.org/1999/xlink}'):
                    frame.set(field,item.attrib.pop(field))
            frame.tail,item.tail = item.tail,None
            parent.replace(item,frame)
            frame.append(item)
            locations[frame] = locations[item]
        if tag==MANIFEST+'file-entry' and item.get(MANIFEST+'media-type')=='application/vnd.sun.xml.math':
            item.set(MANIFEST+'media-type',FORMULA_MEDIA)
