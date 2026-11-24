"""Preserve classic Mac fork pairs and read them through the shared ODF visitor."""
from collections import defaultdict
import base64
import binascii
from dataclasses import replace
from pathlib import Path, PurePosixPath
import struct
import tempfile
from zipfile import ZipFile

from .archive_intake import containers
from .core import Error
from .document_intake import DocumentBuilder, MAX_STRUCTURE, bounded_read
from .publication_package import bind_member_block, capture_folder, member_bytes, member_location
from .source_bundle import relative_file, stamp


BINHEX = b'(This file must be converted with BinHex 4.0)'
APPLEDOUBLE = b'\0\x05\x16\x07\0\x02\0\0'
APPLESINGLE = b'\0\x05\x16\0\0\x02\0\0'
FINDER_TYPES = frozenset((b'TEXTNISI', b'WORDMACA', b'MW2DMWII', b'MWPdMWPR'))
BINHEX_ALPHABET = b"""!"#$%&'()*+,-012345689@ABCDEFGHIJKLMNPQRSTUVXYZ[`abcdefhijklmpqr"""
BASE64_ALPHABET = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


def matches_header(prefix):
    if prefix.startswith((BINHEX, APPLESINGLE)):
        return True
    if prefix.startswith(b'\0\x04\0\x04') and prefix[221:223]==b'MP':
        return True
    if len(prefix)>=40 and prefix.startswith(b'\0\x06'):
        paragraphs, headers, footers = struct.unpack_from('>hhh',prefix,2)
        free_list, = struct.unpack_from('>I',prefix,18)
        return paragraphs>0 and headers>=0 and footers>=0 and free_list>=0x100
    return False


def verified_binhex(data):
    header,marker,rest = data.partition(b':')
    encoded,end,trailer = rest.partition(b':')
    encoded = b''.join(encoded.split())
    invalid = 'The BinHex envelope has an invalid header, length or checksum.'
    if header.rstrip()!=BINHEX or not marker or not end or trailer.strip() or not encoded or encoded.translate(
            None,BINHEX_ALPHABET):
        raise Error('source_mac_fork',invalid)
    bits = encoded.translate(bytes.maketrans(BINHEX_ALPHABET,BASE64_ALPHABET))
    try:
        compressed = base64.b64decode(bits+b'='*(-len(bits)%4),validate=True)
    except binascii.Error as error:
        raise Error('source_mac_fork',invalid) from error
    decoded = bytearray()
    source = iter(compressed)
    for byte in source:
        if byte!=0x90:
            decoded.append(byte)
        else:
            repeat = next(source,None)
            if repeat is None or repeat and not decoded:
                raise Error('source_mac_fork',invalid)
            decoded.extend(bytes([decoded[-1]])*(repeat-1) if repeat else b'\x90')
        if len(decoded)>MAX_STRUCTURE:
            raise Error('source_structure_size','The expanded BinHex forks exceed the structured input bound.')
    if not decoded or not 1<=decoded[0]<=63:
        raise Error('source_mac_fork',invalid)
    name_length = decoded[0]
    header_end = name_length+20
    if len(decoded)<header_end+6 or decoded[name_length+1]!=0:
        raise Error('source_mac_fork',invalid)
    data_length,resource_length = struct.unpack_from('>II',decoded,header_end-8)
    data_start = header_end+2
    resource_start = data_start+data_length+2
    end = resource_start+resource_length+2
    if end>len(decoded) or len(decoded)-end>2 or any(decoded[end:]):
        raise Error('source_mac_fork',invalid)
    view = memoryview(decoded)
    for start,stop in ((0,header_end),(data_start,resource_start-2),(resource_start,end-2)):
        checksum, = struct.unpack_from('>H',view,stop)
        if binascii.crc_hqx(view[start:stop],0)!=checksum:
            raise Error('source_mac_fork',invalid)
    return BINHEX+b'\n:'+encoded+b':\n'


def fork_metadata(data):
    if not data.startswith(APPLEDOUBLE):
        return None
    if len(data)<26:
        raise Error('source_mac_fork','The AppleDouble header is truncated.')
    count, = struct.unpack_from('>H',data,24)
    table_end = 26+12*count
    if not count or table_end>len(data):
        raise Error('source_mac_fork','The AppleDouble entry table is truncated.')
    fields = {}
    occupied = []
    for entry,start,size in struct.iter_unpack('>III',data[26:table_end]):
        end = start+size
        if entry in fields or entry==1 or (size and (start<table_end or end>len(data))):
            raise Error('source_mac_fork','The AppleDouble entry table has an invalid fork range.')
        if size:
            occupied.append((start,end))
        fields[entry] = (start,size)
    occupied.sort()
    if any(end>start for (_,end),(start,_) in zip(occupied,occupied[1:])):
        raise Error('source_mac_fork','The AppleDouble entry ranges overlap.')
    start,size = fields.get(9,(0,0))
    finder = data[start:start+8] if size>=8 else b''
    return finder,fields.get(2,(0,0))[1]


def sidecar_names(name):
    path = PurePosixPath(name)
    sibling = path.with_name('._'+path.name)
    return sibling.as_posix(),(PurePosixPath('__MACOSX')/sibling).as_posix()


def original_name(sidecar):
    path = PurePosixPath(sidecar)
    if not path.name.startswith('._') or len(path.name)<3:
        return None
    if path.parts[0]=='__MACOSX':
        path = PurePosixPath(*path.parts[1:])
    return path.with_name(path.name[2:]).as_posix()


def loose_fork(path):
    candidates, examined = [], 0
    for name in sidecar_names(path.name):
        candidate = path.parent/name
        if not candidate.is_file() and not candidate.is_symlink():
            continue
        examined += 1
        with relative_file(path.parent,name).open('rb') as stream:
            if stream.read(8)!=APPLEDOUBLE:
                continue
            stream.seek(0)
            data = stream.read(MAX_STRUCTURE+1)
        if len(data)>MAX_STRUCTURE:
            raise Error('source_structure_size','The AppleDouble sidecar exceeds the structured input bound.')
        finder,_ = fork_metadata(data)
        if finder in FINDER_TYPES:
            candidates.append(name)
    if candidates and examined>1:
        raise Error('source_member_ambiguous','The Macintosh document has two matching AppleDouble sidecars.')
    return candidates[0] if candidates else None


def archive_books(archive,members):
    by_name = defaultdict(list)
    for member in members:
        by_name[member.name].append(member)
    paired = {}
    for item in members:
        target = original_name(item.name)
        if target not in by_name or item.size is not None and item.size>MAX_STRUCTURE:
            continue
        with archive.open_member(item) as stream:
            if stream.read(8)!=APPLEDOUBLE:
                continue
        try:
            finder,_ = fork_metadata(member_bytes(archive,item,MAX_STRUCTURE))
        except Error as error:
            if error.code in ('source_mac_fork','source_structure_size'):
                continue
            raise
        if finder in FINDER_TYPES:
            for member in by_name[target]:
                paired[member.index] = member
    return [('classic-mac',member,member.name) for member in paired.values()]


def capture_classic_mac(path,target_dir,source):
    if path.is_symlink():
        path = path.resolve()
    fork = loose_fork(path)
    if fork is None:
        return None
    root = path.parent
    paths = {name:stamp(relative_file(root,name).stat()) for name in (path.name,fork)}
    target = target_dir/'source.zip'
    capture_folder(root,target,lambda:paths,include_root=False,
                   verify=lambda _: {name:stamp(relative_file(root,name).stat()) for name in paths})
    return target,replace(source,kind='zip',entry=sorted(paths).index(path.name)+1)


def read_classic_mac(path,source,sha,cache,title):
    from .legacy_office_intake import read_legacy_odf

    builder = DocumentBuilder('classic-mac',title,sha)
    if source.kind=='file':
        with path.open('rb') as stream:
            binhex = stream.read(len(BINHEX))==BINHEX
        if not binhex:
            return read_legacy_odf(path,builder,cache,mac=True)
        data = verified_binhex(bounded_read(path))
        with tempfile.TemporaryDirectory(prefix='hamr-mac-binhex-',dir=cache) as work:
            transport = Path(work)/'document.hqx'
            transport.write_bytes(data)
            return read_legacy_odf(transport,builder,cache,mac=True)
    with containers(path,source.kind) as archive, tempfile.TemporaryDirectory(
            prefix='hamr-mac-forks-',dir=cache) as work:
        members = list(archive.members())
        part = next((member for member in members if member.index==source.entry),None)
        if part is None:
            raise Error('source_member_missing','The selected Macintosh data fork is unavailable.')
        sidecars = [member for member in members if member.name in sidecar_names(part.name)]
        if len(sidecars)!=1 or sum(member.name==part.name for member in members)!=1:
            raise Error('source_member_ambiguous','The selected Macintosh data fork has no unique AppleDouble partner.')
        sidecar, = sidecars
        data = member_bytes(archive,part,MAX_STRUCTURE)
        resource = member_bytes(archive,sidecar,MAX_STRUCTURE-len(data))
        finder,length = fork_metadata(resource) or (None,0)
        if finder not in FINDER_TYPES or finder==b'TEXTNISI' and not length:
            raise Error('source_mac_fork','The selected Macintosh document needs its matching AppleDouble resource fork.')
        converter_input = Path(work)/'forks.zip'
        with ZipFile(converter_input,'x') as pair:
            pair.writestr('document',data)
            pair.writestr('._document',resource)
        doc = read_legacy_odf(converter_input,builder,cache,mac=True)
        for item in doc.blocks:
            bind_member_block(item,part,1,sha,{},lambda _,href:href)
        doc.metadata['source_resources'] = [member_location(sha,sidecar,'resource fork')]
        return doc
