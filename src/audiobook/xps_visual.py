"""Render a local XPS page and observe it through the shared OCR reader."""

from contextlib import ExitStack
import math
from pathlib import Path
import platform
import re
import shutil
import sys
import tarfile
import tempfile
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from PIL import Image

from .core import Error, filehash
from .intake_helpers import convert, linux_sandbox, sandbox_status
from .pdf_visual import MAX_PIXELS, Observer
from .source_bundle import CHUNK, safe_name


RUNTIME = Path(__file__).with_name('intake_resident')/'xps-renderer-linux-x86_64.tar.xz'
RUNTIME_SHA256 = '46e0fb5bb0be2764113111f62aa7d25529187f840618a2c9f283fc01fa474dc0'
MAX_RUNTIME = 27*1024**2
MAX_DIMENSION = 8192
MIN_GLIBC = (2,36)


def worker_available():
    libc,version = platform.libc_ver()
    return (sys.platform.startswith('linux') and platform.machine().lower() in ('x86_64','amd64')
            and libc=='glibc' and bool(re.fullmatch(r'\d+\.\d+(?:\.\d+)?',version))
            and tuple(map(int,version.split('.')[:2]))>=MIN_GLIBC
            and RUNTIME.is_file())


def readiness():
    if not worker_available():
        return {'ready':False,'reason':'source_helper_unavailable'}
    status = sandbox_status()
    return {field:status[field] for field in ('ready','reason','diagnostic') if field in status}


def resolution(size):
    w,h = size
    max_dpi = math.floor(96*min(math.sqrt(MAX_PIXELS/(w*h)),MAX_DIMENSION/max(w,h)))
    while max_dpi>0 and math.ceil(w*max_dpi/96)*math.ceil(h*max_dpi/96)>MAX_PIXELS:
        max_dpi -= 1
    primary = min(240,max_dpi)
    if primary<96:
        raise Error('ocr_pixels','This printed page exceeds the supported whole-page raster size.',
                    'Supply a smaller page or a searchable copy.')
    lower = round(primary*2/3)
    higher = min(300,max_dpi)
    if higher==primary:
        higher = round((lower+primary)/2)
    return primary,lower,higher


def unpack(directory):
    if not worker_available():
        raise Error('source_helper_unavailable',
                    'Graphics-only XPS rendering needs Linux x86-64, glibc 2.36 or newer, and Bubblewrap.',
                    'Use a compatible host or open an image or searchable PDF export.')
    linux_sandbox()
    if RUNTIME.is_symlink() or filehash(RUNTIME)!=RUNTIME_SHA256:
        raise Error('asset_integrity','The bundled XPS renderer did not match its checksum.',
                    'Reinstall the checked Hamr package.')
    expected = {'run','xpstopng','lib/libgxps.so.2'}
    seen = set()
    total = 0
    with tarfile.open(RUNTIME,'r|xz') as archive:
        for node in archive:
            name = node.name
            if name.startswith('notices/') and node.isfile():
                continue
            if (name in seen or not node.isfile() or name not in ('run','xpstopng')
                    and not re.fullmatch(r'lib/[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)+',name)):
                raise Error('asset_integrity','The bundled XPS runtime contains an unexpected entry.')
            total += node.size
            if total>MAX_RUNTIME:
                raise Error('asset_integrity','The bundled XPS runtime exceeds its declared size.')
            seen.add(name)
            destination = directory/name
            destination.parent.mkdir(exist_ok=True)
            with archive.extractfile(node) as stream,destination.open('xb') as output:
                shutil.copyfileobj(stream,output,CHUNK)
            destination.chmod(0o500 if name in ('run','xpstopng') else 0o400)
    if not expected<=seen:
        raise Error('asset_integrity','The bundled XPS runtime is incomplete.')


class VisualPages:
    def __init__(self, path, source, archive, parts, members, cache):
        self.path = path
        self.source = source
        self.archive = archive
        self.parts = parts
        self.members = members
        self.cache = cache
        self.stack = ExitStack()
        self.home = None
        self.failure = None
        self.observer = Observer(cache/'xps-observations' if cache else None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def stage(self):
        if any(len(group)>1 for group in self.parts.index.values()):
            raise Error('source_opc_relationships','The XPS renderer cannot choose between equivalent package part names.')
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='hamr-xps-',dir=self.cache)))
        runtime = root/'runtime'
        runtime.mkdir()
        unpack(runtime)
        path = self.path
        supported = (self.source.kind=='zip' and not self.parts.prefix and
                     all(info.compress_type in (ZIP_STORED,ZIP_DEFLATED) for info in self.archive.infolist() if not info.is_dir()))
        if not supported:
            path = root/'source.xps'
            members = [item for item in self.members if item.name.startswith(self.parts.prefix)]
            free = shutil.disk_usage(root).free
            if sum((item.size or 0)+1024 for item in members)>free:
                raise Error('disk_space','The selected XPS package cannot fit in the renderer workspace.')
            with ZipFile(path,'x',compression=ZIP_STORED,allowZip64=True) as output:
                for item in members:
                    name = safe_name(self.parts.local(item))
                    with self.archive.open_member(item) as data,output.open(name,'w',force_zip64=True) as target:
                        while chunk:=data.read(CHUNK):
                            free -= len(chunk)
                            if free<0:
                                raise Error('disk_space','The selected XPS package exceeds the renderer workspace.')
                            target.write(chunk)
        self.home,self.runtime,self.input = root,runtime,path

    def draw(self, document, page, total, size, dpi):
        if self.home is None:
            if self.failure is None:
                try:
                    self.stage()
                except Error as error:
                    self.failure = error
                except (OSError,tarfile.TarError,BadZipFile):
                    self.failure = Error('source_helper_failed','Hamr could not prepare the XPS renderer workspace.')
            if self.failure is not None:
                raise self.failure
        with tempfile.TemporaryDirectory(prefix='page-',dir=self.home) as directory:
            working = Path(directory)
            output = working/f'view-{page:0{len(str(total))}d}.png'
            argv = ['-d',str(document),'-f',str(page),'-l',str(page),'-r',str(dpi),
                    '/input/'+self.input.name,'/work/view']
            convert(self.input,working,self.runtime/'run',argv,output,runtime=self.runtime)
            try:
                with Image.open(output) as image:
                    width,height = image.size
                    if (image.format!='PNG' or width*height>MAX_PIXELS or max(width,height)>MAX_DIMENSION
                            or any(abs(measured-original*dpi/96)>2 for measured,original in zip(image.size,size))):
                        raise Error('ocr_geometry','The renderer returned unexpected XPS page geometry.')
                    return image.convert('RGB')
            except OSError as error:
                raise Error('source_helper_failed','The rendered XPS PNG is unreadable.') from error

    def read(self, member, builder, evidence):
        size = evidence['size']
        document,page,total = (evidence[k] for k in ('document','document_page','document_pages'))
        try:
            dpi,lower,higher = resolution(size)
            def view(dpi):
                return self.draw(document,page,total,size,dpi)
            with view(dpi) as image:
                width,height = size
                rect = [0,0,width,height]
                parts,observed = self.observer.read_raster(lambda:image,None,
                    {**evidence,'native_geometry_qualified':False},rect,format='xps',scale=dpi/96,
                    verification=((f'{lower} dpi',lambda:view(lower)),(f'{higher} dpi',lambda:view(higher))))
                observed['raster_size'] = list(image.size)
            if not parts:
                raise Error('ocr_geometry','The renderer returned a blank image for an XPS page with drawing commands.')
        except Error as error:
            reason = ' '.join(filter(None,(error.message,error.action)))
            evidence.update(status='needs_review',visual_failure=error.code)
            builder.add('','unreadable',member.name,loc={'page':evidence['page']},issue=reason)
            return
        observed['coordinate_space'] = 'xps_device_independent_units'
        evidence.update(status=observed['status'],route='visual_ocr',
                        renderer={'engine':'libgxps','dpi':dpi,'runtime_sha256':RUNTIME_SHA256},
                        visual_observation=observed)
        for part in parts:
            builder.add(part['text'],part.get('role','body'),member.name,
                        loc=part['locator'],issue=part['issue'])
