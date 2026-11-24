"""Isolated structural conversion for formats with a qualified native helper."""
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from .core import Error, filehash, loadjson, syncdir, writejson

CALIBRE_VERSION = '9.19.0'
CALIBRE_NAME = f'calibre-{CALIBRE_VERSION}-x86_64.txz'
CALIBRE_URL = f'https://download.calibre-ebook.com/{CALIBRE_VERSION}/{CALIBRE_NAME}'
CALIBRE_BYTES = 241_350_288
CALIBRE_UNPACKED = 949_758_632
CALIBRE_SHA256 = '35c1cbda1a2548aceeb89cb0a4be47a36025b5dc102ac046f7eaf98ba46a452d'


def linux_sandbox():
    if not sys.platform.startswith('linux') or not (binary:=shutil.which('bwrap')):
        raise Error('source_helper_unavailable','This converter needs local Bubblewrap isolation.',
                    'Install Bubblewrap or export a readable copy to EPUB or ODT.')
    return binary


def converter_argv(path, work, binary, arguments, runtime=None):
    sandbox = linux_sandbox()
    if path.parent==work:
        raise Error('unsafe_path','The converter work directory must be separate from the original.')
    argv = [sandbox,'--die-with-parent','--unshare-net','--unshare-ipc','--unshare-pid',
            '--ro-bind','/usr','/usr','--ro-bind','/etc','/etc','--dev','/dev','--proc','/proc',
            '--tmpfs','/tmp','--tmpfs','/run','--dir','/input',
            '--ro-bind',str(path),'/input/'+path.name,'--bind',str(work),'/work',
            '--setenv','HOME','/work','--setenv','XDG_CONFIG_HOME','/work/config',
            '--setenv','XDG_CACHE_HOME','/work/cache','--setenv','QT_QPA_PLATFORM','offscreen',
            '--setenv','CALIBRE_CONFIG_DIRECTORY','/work/calibre','--chdir','/work']
    for alias in ('/bin','/lib','/lib64','/sbin'):
        if Path(alias).is_symlink():
            argv.extend(['--symlink',os.readlink(alias),alias])
        elif Path(alias).is_dir():
            argv.extend(['--ro-bind',alias,alias])
    if runtime is not None:
        argv.extend(['--ro-bind',str(runtime),'/reader'])
        invocation = '/reader/'+binary.relative_to(runtime).as_posix()
    else:
        invocation = str(binary)
    return [*argv,'--',invocation,*arguments]


def convert(path,work,binary,arguments,output,*,runtime=None):
    from .platform_support import spawn_owned,close_windows_job

    argv = converter_argv(path,work,binary,arguments,runtime)
    environment = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    with tempfile.TemporaryFile(dir=work) as log:
        process,job = spawn_owned(argv,parent_death=True,env=environment,stdin=subprocess.DEVNULL,
                                  stdout=log,stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=180)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise Error('source_helper_timeout','The local converter did not finish in time.') from exc
        finally:
            if job:
                close_windows_job(job)
            if process.poll() is None:
                process.kill()
                process.wait()
        if code or not output.is_file() or output.is_symlink() or output.stat().st_size==0:
            log.seek(0,os.SEEK_END)
            log.seek(max(0,log.tell()-2048))
            detail = log.read(2048).decode('utf-8','replace').strip()
            raise Error('source_helper_failed',f'The local converter produced no complete readable result. {detail}',
                        'Check the file and local converter, or supply a readable export.')


def legacy_document(path,source,builder,cache):
    tool = shutil.which('libreoffice') or shutil.which('soffice')
    if not tool:
        raise Error('source_helper_unavailable','This source needs an installed LibreOffice Writer.',
                    'Install a compatible Writer or supply an ODT export.')
    binary = Path(tool).resolve()
    if not binary.is_relative_to('/usr'):
        raise Error('source_helper_unavailable','The detected LibreOffice is outside the supported system runtime.')
    with tempfile.TemporaryDirectory(prefix='hamr-office-',dir=cache) as temporary:
        work = Path(temporary)
        output = work/(path.stem+'.odt')
        args = ['-env:UserInstallation=file:///work/profile','--headless','--convert-to','odt:writer8',
                '--outdir','/work','/input/'+path.name]
        convert(path,work,binary,args,output)
        from .document_intake import odt
        odt(output,builder)
        builder.doc.metadata['converter'] = {'engine':'LibreOffice Writer','binary_sha256':filehash(binary),
                                            'derivative_sha256':filehash(output),'intermediate':'ODT'}
    return builder.doc


def verified_helper(directory):
    executable = directory/'ebook-convert'
    try:
        receipt = loadjson(directory/'hamr-receipt.json')
        if (not directory.is_symlink() and not executable.is_symlink() and executable.is_file()
                and receipt['archive_sha256']==CALIBRE_SHA256
                and receipt['executable_sha256']==filehash(executable)
                and filehash(directory/'bin/ebook-convert')==receipt['worker_sha256']):
            return executable
    except (OSError,ValueError,KeyError,TypeError):
        pass
    return None


def calibre_helper(cache, offline):
    linux_sandbox()
    tool = shutil.which('ebook-convert')
    if tool and Path(tool).resolve().is_relative_to('/usr'):
        candidate = Path(tool).resolve()
        try:
            version = subprocess.check_output([str(candidate),'--version'],text=True,timeout=10)
        except (OSError,subprocess.SubprocessError):
            version = ''
        if re.search(r'\b'+re.escape(CALIBRE_VERSION)+r'\b',version):
            return candidate,None
    if not sys.platform.startswith('linux') or platform.machine().lower() not in ('x86_64','amd64'):
        raise Error('source_helper_unavailable','The managed legacy ebook helper is available for Linux x86-64.',
                    'Supply an EPUB export on this platform.')
    if cache is None:
        raise Error('source_helper_unavailable','This route needs a Hamr state directory for the converter.')
    root = cache.parent/'assets'/'intake'
    root.mkdir(exist_ok=True)
    if root.is_symlink():
        raise Error('unsafe_path','Converter assets must use the owned Hamr assets directory.')
    directory = root/f'calibre-{CALIBRE_VERSION}'
    if executable:=verified_helper(directory):
        return executable,directory
    if directory.exists():
        raise Error('asset_integrity',f'The managed converter did not match its receipt at {directory}.')
    if offline:
        raise Error('source_helper_offline','This rare ebook needs the local Calibre converter, which is not installed.',
                    'Run without --offline to acquire it, or supply an EPUB export.')
    from .backend import acquire, remaining_bytes
    from .core import Store
    from .setup_progress import SetupProgress, size_text

    source = root/CALIBRE_NAME
    app = Store(cache.parent)
    try:
        with app.lock('legacy-ebook-setup',wait=True):
            if executable:=verified_helper(directory):
                return executable,directory
            if directory.exists():
                raise Error('asset_integrity',f'The managed converter did not match its receipt at {directory}.')
            download = remaining_bytes(CALIBRE_URL,source,CALIBRE_SHA256,CALIBRE_BYTES)
            required = download+CALIBRE_UNPACKED+64*1024**2
            if shutil.disk_usage(root).free<required:
                raise Error('disk_space',f'Need {size_text(required)} free to acquire the legacy ebook converter.')
            progress = SetupProgress()
            progress.stage(f'Preparing the legacy ebook reader ({size_text(download)} download; {size_text(CALIBRE_UNPACKED)} installed).')
            acquire(CALIBRE_URL,source,CALIBRE_SHA256,CALIBRE_BYTES,progress=progress,label='Legacy ebook reader')
            with tempfile.TemporaryDirectory(prefix='calibre-stage-',dir=root) as temporary:
                staged = Path(temporary)/'runtime'
                staged.mkdir()
                with tarfile.open(source,'r:xz') as archive:
                    for member in archive:
                        # The verified upstream archive contains regular files and directories.
                        if not (member.isfile() or member.isdir()):
                            raise Error('asset_integrity','The portable reader contains an unexpected archive entry.')
                        archive.extract(member,staged,filter='data')
                for relative in ('ebook-convert','bin/ebook-convert'):
                    if not (staged/relative).is_file() or not os.access(staged/relative,os.X_OK):
                        raise Error('asset_integrity',f'The portable reader lacks {relative}.')
                writejson(staged/'hamr-receipt.json',{'archive_sha256':CALIBRE_SHA256,'version':CALIBRE_VERSION,
                    'source':CALIBRE_URL,'download_bytes':CALIBRE_BYTES,'unpacked_bytes':CALIBRE_UNPACKED,
                    'executable_sha256':filehash(staged/'ebook-convert'),
                    'worker_sha256':filehash(staged/'bin/ebook-convert')})
                os.rename(staged,directory)
                syncdir(root)
            source.unlink()
    finally:
        app.db.close()
    executable = verified_helper(directory)
    if executable is None:
        raise Error('asset_integrity','The installed portable reader failed verification.')
    return executable,directory


def legacy_ebook(path,source,sha,cache):
    from .importers import epub

    binary, runtime = calibre_helper(cache,source.offline)
    with tempfile.TemporaryDirectory(prefix='hamr-ebook-',dir=cache) as temporary:
        work = Path(temporary)
        output = work/'book.epub'
        convert(path,work,binary,['/input/'+path.name,'/work/book.epub'],output,runtime=runtime)
        doc = epub(output)
        doc.source_sha256 = sha
        doc.format = 'legacy-ebook'
        doc.metadata['converter'] = {'engine':'Calibre','version':CALIBRE_VERSION,
                                     'artifact_sha256':CALIBRE_SHA256 if runtime else None,
                                     'intermediate':'EPUB','derivative_sha256':filehash(output)}
        # EPUB locations below are evidence from the intermediate. The
        # portable published source carries the original legacy container.
        for item in doc.blocks:
            item.locator['converter_intermediate'] = True
    return doc


def print_replica(stream,first_record,records,sha,cache):
    from .importers import pdf
    from .source_bundle import CHUNK

    size = os.fstat(stream.fileno()).st_size
    stream.seek(first_record)
    palm = stream.read(10)
    if palm[:2]!=b'\0\1':
        raise Error('unreadable_replica','This compressed Print Replica rendition needs a PDF export.')
    text_size = int.from_bytes(palm[4:8],'big')
    text_records = int.from_bytes(palm[8:10],'big')
    if not 0<text_records<=len(records):
        raise Error('unreadable_replica','The Print Replica text record count is invalid.')
    ends = [*records[1:text_records+1],size][:text_records]
    records = records[:text_records]
    start = records[0]
    stream.seek(start)
    header = stream.read(min(20,ends[0]-start))
    if len(header)<20 or not header.startswith(b'%MOP'):
        raise Error('unreadable_replica','The Kindle edition has no complete Print Replica header.')
    offset,length = int.from_bytes(header[12:16],'big'),int.from_bytes(header[16:20],'big')
    if text_size!=ends[-1]-start or offset<20 or length<8 or offset+length>text_size:
        raise Error('unreadable_replica','The embedded PDF range exceeds the Print Replica records.')
    with tempfile.TemporaryDirectory(prefix='hamr-replica-',dir=cache) as folder:
        extracted = Path(folder)/'edition.pdf'
        if length>shutil.disk_usage(folder).free:
            raise Error('disk_space','There is insufficient working space for the embedded PDF.')
        with extracted.open('xb') as dst:
            remaining = length
            for left,right in zip(records,ends):
                if offset>=right-left:
                    offset -= right-left
                    continue
                stream.seek(left+offset)
                count = min(remaining,right-left-offset)
                offset = 0
                while count:
                    data = stream.read(min(count,CHUNK))
                    if not data:
                        raise Error('unreadable_replica','The edition ended while reading the embedded PDF.')
                    count -= len(data)
                    remaining -= len(data)
                    dst.write(data)
                if not remaining:
                    break
        with extracted.open('rb') as payload:
            if payload.read(5)!=b'%PDF-':
                raise Error('unreadable_replica','The stored Print Replica section is not a PDF.')
        doc = pdf(extracted,ocr_cache=cache/'pdf-observations' if cache else None)
        doc.format = 'print-replica'
        doc.source_sha256 = sha
        doc.metadata['converter'] = {'engine':'Print Replica record reader','intermediate':'PDF',
                                     'derivative_sha256':filehash(extracted)}
        for item in doc.blocks:
            item.locator['converter_intermediate'] = True
        return doc


def resident_mobi(path,source,sha,cache):
    from .importers import epub

    with path.open('rb') as stream:
        header = stream.read(86)
        if header[60:68]==b'TEXtREAd':
            from .palmdoc_intake import read_palmdoc

            return read_palmdoc(path,source,sha,path.stem)
        if len(header)!=86 or header[60:68]!=b'BOOKMOBI':
            raise Error('unreadable_mobi','The source does not contain a recognized Kindle header.')
        first = int.from_bytes(header[78:82],'big')
        count = int.from_bytes(header[76:78],'big')
        size = os.fstat(stream.fileno()).st_size
        table_end = 78+count*8
        if not count or first<table_end or first+14>size:
            raise Error('unreadable_mobi','The source has an invalid first record.')
        stream.seek(first+12)
        if stream.read(2)!=b'\0\0':
            raise Error('protected_source','The Kindle/Palm source is encrypted.','Supply a readable EPUB export.')
        if count>1:
            stream.seek(86)
            next_record = int.from_bytes(stream.read(4),'big')
            if first+14<=next_record<=size-4:
                stream.seek(next_record)
                if stream.read(4)==b'%MOP':
                    stream.seek(86)
                    entries = stream.read(table_end-86)
                    positions = [int.from_bytes(entries[i:i+4],'big') for i in range(0,len(entries),8)]
                    if any(left>=right for left,right in zip(positions,[*positions[1:],size])):
                        raise Error('unreadable_replica','The Print Replica record order is invalid.')
                    return print_replica(stream,first,positions,sha,cache)
        if path.suffix.lower()=='.azw4':
            raise Error('unreadable_replica','The file does not expose a readable Print Replica PDF.',
                        'Supply a readable PDF export.')
    machine = platform.machine().lower()
    tools = {
        'linux':('mobitool-linux-x86_64','05567860bd98a8393621183ba977c9e3c4d8abbb4747045711501a1d70bd8b34'),
        'win32':('mobitool-windows-x86_64.exe','1e63bece58cf2a71f7626c6025cba4c3c20973bce912978df47ebd7e5fe22dce'),
    }
    resource,sha256 = tools.get(sys.platform,('unavailable',''))
    tool = Path(__file__).with_name('intake_resident')/resource
    if machine not in ('x86_64','amd64') or not tool.is_file():
        installed = shutil.which('mobitool')
        if not installed:
            raise Error('source_helper_unavailable','The compact Kindle reader is unavailable on this platform.',
                        'Use an installed libmobi reader or export the book as EPUB.')
        tool = Path(installed)
    if tool.parent.name=='intake_resident' and filehash(tool)!=sha256:
        raise Error('asset_integrity','The shipped Kindle reader has changed.')
    with tempfile.TemporaryDirectory(prefix='hamr-mobi-',dir=cache) as temporary:
        work = Path(temporary)
        output = work/(path.stem+'.epub')
        if sys.platform=='linux' and shutil.which('bwrap'):
            # Large, unfamiliar input structures stay in the same isolated
            # conversion contract as the desktop-format helpers.
            convert(path,work,tool,['-e','-o','/work','/input/'+path.name],output,runtime=tool.parent)
        else:
            try:
                process = subprocess.run([str(tool),'-e','-o',str(work),str(path)],timeout=120,
                                         check=True,capture_output=True)
            except (OSError,subprocess.SubprocessError) as exc:
                raise Error('unreadable_mobi','The compact Kindle reader could not decode this variant.') from exc
            if not output.is_file():
                raise Error('unreadable_mobi','The compact Kindle reader returned no readable publication.')
        doc = epub(output)
        doc.format = 'mobi'
        doc.source_sha256 = sha
        doc.metadata['converter'] = {'engine':'libmobi','binary_sha256':filehash(tool),
                                     'derivative_sha256':filehash(output),'intermediate':'EPUB',
                                     'rendition_policy':'Prefer the KF8 part of a hybrid edition.'}
        for item in doc.blocks:
            item.locator['converter_intermediate'] = True
        return doc


def subtitles(data,builder):
    text = data.decode('utf-8-sig').replace('\r\n','\n')
    last = 0
    for part in re.split(r'\n[ \t]*\n',text.strip()):
        lines = part.splitlines()
        if not lines or lines[0].startswith(('WEBVTT','NOTE','STYLE','REGION')):
            continue
        timing = next((i for i,line in enumerate(lines[:2]) if ' --> ' in line),None)
        last += 1
        if timing is None or not lines[timing+1:]:
            builder.issue('A subtitle cue is missing text or timing.',f'cue:{last}')
            continue
        from bs4 import BeautifulSoup
        content = BeautifulSoup(' '.join(lines[timing+1:]),'html.parser').get_text(' ',strip=True)
        builder.add(content,where=f'cue:{last}',loc={'source_timing':lines[timing]})
