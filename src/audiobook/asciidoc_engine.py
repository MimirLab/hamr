"""Run the resident AsciiDoc parser with an occurrence-bounded source port."""

from functools import lru_cache
from gzip import decompress
from pathlib import Path
import json
import posixpath
from time import monotonic

from .core import Error, filehash
from .document_intake import MAX_STRUCTURE
from .source_bundle import safe_name


ASSETS = Path(__file__).with_name('intake_resident')
BUNDLE = ASSETS/'asciidoctor-4.3.0.js.gz'
BUNDLE_SHA256 = '500fa2c42995d2b814ea29395c5b90778fe4a569ecef61d43136e9432b006b9a'
ORIGIN = 'https://hamr.invalid/'
MAX_REQUESTS = 4096


@lru_cache(maxsize=1)
def program():
    if filehash(BUNDLE)!=BUNDLE_SHA256:
        raise Error('asset_integrity','The bundled AsciiDoc parser has changed.')
    parser = decompress(BUNDLE.read_bytes()).decode('utf-8')
    return parser+'\n'+(ASSETS/'asciidoc_bridge.js').read_text(encoding='utf-8')


def source_path(address,main):
    if isinstance(address,str) and address.startswith(ORIGIN):
        name = posixpath.normpath(address[len(ORIGIN):])
        return safe_name(name)
    if address==posixpath.basename(main):
        return main
    return None


def parse(text,main,read):
    from quickjs import Context, JSException

    ctx = Context()
    ctx.set_memory_limit(128*1024*1024)
    ctx.set_max_stack_size(1024*1024)
    ctx.set_time_limit(5)
    current = posixpath.dirname(main)
    files = {main:text}
    denied = {}
    remaining = 3*MAX_STRUCTURE
    deadline = monotonic()+45
    requests = 0
    try:
        ctx.module(program())
        ctx.set('hamrBase',(ORIGIN+current).rstrip('/'))
        ctx.set('hamrFile',ORIGIN+main)
        ctx.set('hamrInput',text)
        ctx.eval('hamrStart()')
        while monotonic()<deadline:
            if error:=ctx.eval('globalThis.hamrError || null'):
                raise Error('source_asciidoc',f'The AsciiDoc reader could not parse {main}: {error}')
            if result:=ctx.eval('globalThis.hamrResult || null'):
                return json.loads(result),files,denied
            if address:=ctx.eval('globalThis.hamrRequest || null'):
                requests += 1
                if requests>MAX_REQUESTS:
                    raise Error('source_structure_size','AsciiDoc includes exceed the source-member budget.')
                try:
                    name = source_path(address,main)
                    if name is None or read is None:
                        raise Error('asciidoc_include','The target is outside the saved AsciiDoc publication.')
                    if name not in files:
                        relative = posixpath.relpath(name,current or '.')
                        actual,val = read(main,relative)
                        if actual!=name:
                            raise Error('asciidoc_include','The target does not identify the saved member.')
                        files[name] = val
                    content = files[name]
                    remaining -= len(content)
                    if remaining<0:
                        raise Error('source_structure_size','Expanded AsciiDoc includes exceed the reading limit.')
                except Error as error:
                    if error.code=='source_structure_size':
                        raise
                    denied[address] = str(error)
                    content = None
                ctx.set('hamrReply',content)
                ctx.eval('hamrAccept(hamrReply)')
            elif not ctx.execute_pending_job():
                break
    except JSException as error:
        raise Error('source_asciidoc',f'The AsciiDoc parser stopped while reading {main}: {error}') from error
    raise Error('source_asciidoc','The AsciiDoc reader did not finish within its local reading budget.',
                'Divide the manuscript at a chapter boundary and try again.')
