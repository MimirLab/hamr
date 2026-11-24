"""Convert WordPerfect through the same OpenDocument visitor as Writer files."""
from pathlib import Path
import platform
import signal
import sys
import tempfile

from .core import Error, filehash
from .document_intake import MAX_STRUCTURE, odt
from .intake_helpers import convert, legacy_document


ASSET = Path(__file__).with_name('intake_resident')/'wordperfect-linux-x86_64'
ASSET_SHA256 = '134cec4118c91be8baa11ab795c0583a1a5001047a7ac708a2c243c6542d9a6b'
EXIT_ERRORS = {
    3: ('protected_source','This WordPerfect document needs a password.',
        'Open it with the password and save a readable local copy.'),
    4: ('unreadable_wordperfect','The reader could not identify a supported WordPerfect document in this file.',
        'Check the file or export a readable ODT copy.'),
}
if hasattr(signal,'SIGXFSZ'):
    EXIT_ERRORS[-signal.SIGXFSZ] = (
        'source_structure_size','The WordPerfect document exceeds the converted size bound.',
        'Divide the document at a chapter boundary or use a readable local export.')


def resident():
    if sys.platform.startswith('linux') and platform.machine().lower() in ('x86_64','amd64') and ASSET.is_file():
        return ASSET


def read_wordperfect(path,source,builder,cache):
    binary = resident()
    if binary is None:
        return legacy_document(path,source,builder,cache)
    binary_hash = filehash(binary)
    if binary_hash!=ASSET_SHA256:
        raise Error('asset_integrity','The bundled WordPerfect reader has changed.')
    if path.stat().st_size>MAX_STRUCTURE:
        raise Error('source_structure_size','The WordPerfect source exceeds the local reader size bound.',
                    'Divide the document at a chapter boundary or use a readable local export.')
    with tempfile.TemporaryDirectory(prefix='hamr-wordperfect-',dir=cache) as temporary:
        work = Path(temporary)
        output = work/'converted.fodt'
        convert(path,work,binary,['/input/'+path.name,'/work/'+output.name],output,
                runtime=binary.parent,exit_errors=EXIT_ERRORS)
        odt(output,builder)
        builder.doc.metadata['converter'] = {
            'engine':'libwpd', 'binary_sha256':binary_hash, 'intermediate':'Flat ODF',
            'derivative_sha256':filehash(output),
        }
        for item in builder.doc.blocks:
            item.locator['converter_intermediate'] = True
    return builder.doc
