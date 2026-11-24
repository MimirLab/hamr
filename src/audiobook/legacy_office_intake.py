"""Convert legacy office documents through the shared OpenDocument visitor."""
from pathlib import Path
import platform
import signal
import sys
import tempfile

from .core import Error, filehash
from .document_intake import MAX_STRUCTURE, odt
from .intake_helpers import convert, legacy_document


ASSET = Path(__file__).with_name('intake_resident')/'legacy-office-linux-x86_64'
ASSET_SHA256 = 'c3545fdb2e6583010b50f67ff1a69df2a2e0a4379353a0ef5b9d610f085b8bb3'
EXIT_ERRORS = {
    3: ('protected_source','This WordPerfect document needs a password.',
        'Open it with the password and save a readable local copy.'),
    4: ('unreadable_wordperfect','The reader could not identify a supported WordPerfect document in this file.',
        'Check the file or export a readable ODT copy.'),
}
if hasattr(signal,'SIGXFSZ'):
    EXIT_ERRORS[-signal.SIGXFSZ] = (
        'source_structure_size','The legacy document exceeds the converted size bound.',
        'Divide the document at a chapter boundary or use a readable local export.')


def resident():
    if sys.platform.startswith('linux') and platform.machine().lower() in ('x86_64','amd64') and ASSET.is_file():
        return ASSET


def read_wordperfect(path,source,builder,cache):
    if resident() is None:
        return legacy_document(path,source,builder,cache)
    return read_legacy_odf(path,builder,cache)


def read_legacy_odf(path,builder,cache,*,mac=False):
    binary = resident()
    if binary is None:
        raise Error('source_helper_unavailable','The bundled classic Macintosh reader needs Linux x86-64.',
                    'Export a readable local copy to ODT or EPUB.')
    label = 'classic Macintosh' if mac else 'WordPerfect'
    binary_hash = filehash(binary)
    if binary_hash!=ASSET_SHA256:
        raise Error('asset_integrity',f'The bundled {label} reader has changed.')
    if path.stat().st_size>MAX_STRUCTURE:
        raise Error('source_structure_size',f'The {label} source exceeds the local reader size bound.',
                    'Divide the document at a chapter boundary or use a readable local export.')
    errors = EXIT_ERRORS
    mode = []
    if mac:
        mode = ['--classic-mac']
        errors = {**EXIT_ERRORS,
                  3:('protected_source','This classic Macintosh document needs a password.',
                     'Open it with the password and save a readable local copy.'),
                  4:('unreadable_classic_mac','The reader could not identify a supported classic Macintosh writing document.',
                     'Keep its matching data and resource forks together, or export a readable ODT copy.')}
    with tempfile.TemporaryDirectory(prefix='hamr-legacy-office-',dir=cache) as temporary:
        work = Path(temporary)
        output = work/'converted.fodt'
        convert(path,work,binary,[*mode,'/input/'+path.name,'/work/'+output.name],output,
                runtime=binary.parent,exit_errors=errors)
        odt(output,builder)
        builder.doc.metadata['converter'] = {
            'engine':'libmwaw' if mac else 'libwpd', 'binary_sha256':binary_hash, 'intermediate':'Flat ODF',
            'derivative_sha256':filehash(output),
        }
        for item in builder.doc.blocks:
            item.locator['converter_intermediate'] = True
    return builder.doc
