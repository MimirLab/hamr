"""Saved section content and references shared by OpenDocument text readers."""

import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .core import Error
from .odf_content import DRAW, MATH_ROOT, OFFICE, TABLE, TEXT, XLINK_HREF
from .source_bundle import safe_name


SECTION = TEXT+'section'
SECTION_SOURCE = TEXT+'section-source'
SECTION_NAME = TEXT+'section-name'
NAME = TEXT+'name'
LINKED_SUFFIXES = frozenset(('.odt','.ott','.oth','.fodt','.odm'))
SKIP = frozenset({TEXT+'tracked-changes',TEXT+'deletion',OFFICE+'annotation',
                  OFFICE+'scripts',OFFICE+'forms'})


def visible_section(node):
    return node.get(TEXT+'display')!='none' and node.get(TEXT+'is-hidden')!='true'


def linked_source(node):
    return node.find(SECTION_SOURCE)


def visible_contents(node):
    if not isinstance(node.tag,str) or node.tag in SKIP or node.tag==SECTION and not visible_section(node):
        return
    yield node
    for child in node:
        yield from visible_contents(child)


def saved_content(section):
    for item in visible_contents(section):
        if item.tag in (TABLE+'table',DRAW+'frame',DRAW+'image',MATH_ROOT,TEXT+'note'):
            return True
        if item is not section and ((item.text or '').strip() or (item.tail or '').strip()):
            return True
    return False


def body_region(body,region):
    if region:
        matches = [item for item in visible_contents(body) if item.tag==SECTION and item.get(NAME)==region]
        if len(matches)!=1:
            raise Error('odf_section',f'The linked Writer section {region!r} is missing or ambiguous.')
        return matches[0]
    return body


def section_target(part,source,*,packaged):
    href = source.get(XLINK_HREF,'')
    try:
        url = urlsplit(href)
        if url.scheme or url.netloc or url.query or url.path.startswith('/'):
            raise ValueError
        segments = url.path.split('/')
        fragments = [unquote(piece,errors='strict') for piece in segments]
        if any('/' in piece or '\\' in piece or ':' in piece or
               piece in ('.','..') and raw!=piece
               for raw,piece in zip(segments,fragments)):
            raise ValueError
        fragment = unquote(url.fragment,errors='strict')
    except ValueError as exc:
        raise Error('odf_link','The Writer link is not a readable relative local document.') from exc
    named = source.get(SECTION_NAME)
    if named and fragment and named!=fragment:
        raise Error('odf_link','The Writer link names two different sections.')
    region = named or fragment or None
    if not url.path:
        if region:
            return part,region
        raise Error('odf_link','The Writer link has no document or section name.')
    # Packaged content.xml resolves from the virtual directory of its ODF.
    base = part if packaged else posixpath.dirname(part)
    target = posixpath.normpath(posixpath.join(base,'/'.join(fragments)))
    if packaged and target.startswith(part+'/'):
        raise Error('odf_link','This Writer link points to a document embedded inside its package.')
    try:
        target = safe_name(target)
    except Error as exc:
        raise Error('odf_link','The Writer link leaves the supplied source folder.') from exc
    if PurePosixPath(target).suffix.casefold() not in LINKED_SUFFIXES:
        raise Error('odf_link','The Writer link needs a local OpenDocument text file.')
    return target,region
