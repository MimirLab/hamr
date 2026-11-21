"""Classify OCF font-only obfuscation without reading obfuscated font data."""
from urllib.parse import unquote, urlsplit

from .core import Error

OCF = 'urn:oasis:names:tc:opendocument:xmlns:container'
ENC = 'http://www.w3.org/2001/04/xmlenc#'
IDPF_FONT = 'http://www.idpf.org/2008/embedding'
FONT_TYPES = {'font/collection', 'font/otf', 'font/ttf', 'font/woff', 'font/woff2'}


def font_references(root, names):
    if root.tag != f'{{{OCF}}}encryption' or root.getroottree().docinfo.doctype:
        raise Error('protected_epub', 'EPUB resource protection metadata is malformed or unsupported.')
    seen = set()
    for entry in root:
        if not isinstance(entry.tag, str):
            continue
        methods = entry.findall(f'{{{ENC}}}EncryptionMethod')
        references = entry.findall(f'{{{ENC}}}CipherData/{{{ENC}}}CipherReference')
        if (entry.tag != f'{{{ENC}}}EncryptedData' or len(methods) != 1
                or methods[0].get('Algorithm') != IDPF_FONT or len(references) != 1
                or len(references[0]) or len(entry.findall(f'.//{{{ENC}}}CipherReference')) != 1
                or entry.find(f'.//{{{ENC}}}CipherValue') is not None):
            raise Error('protected_epub', 'EPUB contains an unsupported protected resource.')
        uri = references[0].get('URI', '')
        try:
            parts = urlsplit(uri)
            path = unquote(parts.path, errors='strict')
        except (UnicodeError, ValueError) as exc:
            raise Error('protected_epub', 'EPUB protection metadata names an invalid resource.') from exc
        if (parts.scheme or parts.netloc or parts.query or parts.fragment or path in seen
                or path not in names or path in ('mimetype', 'META-INF') or path.startswith('META-INF/')
                or '\\' in path or any(part in ('', '.', '..') for part in path.split('/'))):
            raise Error('protected_epub', 'EPUB protection metadata does not identify one local font resource.')
        seen.add(path)
    return seen


def verify_font_manifest(fonts, manifest, manifest_nodes):
    media_types = {font: set() for font in fonts}
    for identifier, path in manifest.items():
        if path in media_types:
            media_types[path].add(manifest_nodes[identifier].get('media-type'))
    if any(not types or not types <= FONT_TYPES for types in media_types.values()):
        raise Error('protected_epub', 'The EPUB font-obfuscation method names a resource without a valid font declaration.')
