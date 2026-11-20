"""Read back audiobook presentation and metadata independently of encode exit."""
import io
import json
import zipfile
from pathlib import Path
from mutagen.mp4 import MP4,MP4Cover,MP4FreeForm
from PIL import Image
from .core import Error,RATE,run,filehash,digest

CHAPTER_PROFILE='quicktime-track-only'
FIELDS={'title','authors','narrator','series','series_part','cover'}


def merge_overrides(previous,update):
    if not isinstance(update,dict) or set(update)-FIELDS-{'inherit'}:raise Error('metadata_format','Metadata must contain supported fields or an inherit list.')
    inherited=update.get('inherit',[])
    if not isinstance(inherited,list) or any(not isinstance(k,str) or k not in FIELDS for k in inherited):raise Error('metadata_format','inherit must name supported metadata fields.')
    if set(inherited)&set(update):raise Error('metadata_format','A field cannot be set and inherited in the same update.')
    result=dict(previous)
    for field in inherited:result.pop(field,None)
    for field,value in update.items():
        if field=='inherit':continue
        valid=value is None or (isinstance(value,list) and all(isinstance(v,str) and v.strip() and '\0' not in v for v in value) if field=='authors' else isinstance(value,str) and '\0' not in value)
        if field=='cover':valid=value in (None,'source')
        if not valid:raise Error('metadata_format','Invalid metadata value for '+field)
        result[field]=value
    return result


def packet_hash(path):
    return run(['ffmpeg','-v','error','-nostdin','-i',path,'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-']).stdout.decode().strip()


def tag_publication(path,job):
    source=job['document'].get('metadata',{});overrides=job.get('metadata_overrides',{})
    default_title=job['document']['title']
    selected={'title':default_title,'authors':source.get('authors',[]),'narrator':job['voice']['name'],
              'series':source.get('series'),'series_part':source.get('series_part')}
    for field in selected:
        if field in overrides:selected[field]=overrides[field]
    if job.get('publication_label'):
        selected['title']=(selected['title']+' — ' if selected['title'] else '')+job['publication_label']
    if selected['series'] is None and 'series' in overrides and 'series_part' not in overrides:selected['series_part']=None
    before=packet_hash(path);audio=MP4(path);warnings=[]
    standard={'title':'\xa9nam','authors':'\xa9ART'}
    for field,tag in standard.items():
        audio.tags.pop(tag,None);value=selected[field]
        if value:audio[tag]=value if isinstance(value,list) else [value]
    freeform={'narrator':'NARRATOR','series':'SERIES','series_part':'SERIES-PART'}
    for field,name in freeform.items():
        tag='----:com.apple.iTunes:'+name;audio.tags.pop(tag,None)
        if selected[field] is not None:audio[tag]=[MP4FreeForm(str(selected[field]).encode('utf-8'))]
    audio['----:com.apple.iTunes:AUDIOBOOK_ID']=[MP4FreeForm(job['id'].encode())]
    audio['----:com.apple.iTunes:SOURCE_SHA256']=[MP4FreeForm(job['document']['source_sha256'].encode())]
    audio.tags.pop('covr',None);cover=source.get('cover');cover_sha=None
    if cover and overrides.get('cover','source') is not None:
        try:
            # Cover bytes are read only from the immutable, hash-bound EPUB.
            if filehash(Path(job['snapshot']))!=job['document']['source_sha256']:raise Error('snapshot_changed','Source changed before metadata tagging.')
            with zipfile.ZipFile(job['snapshot']) as archive:raw=archive.read(cover['path'])
            if digest(raw)!=cover['sha256']:raise Error('cover_changed','Cover differs from the source-bound asset.')
            with Image.open(io.BytesIO(raw)) as image:
                if image.width*image.height>20_000_000 or image.format not in ('PNG','JPEG'):raise ValueError('Unsupported embedded cover size/format')
                kind=MP4Cover.FORMAT_PNG if image.format=='PNG' else MP4Cover.FORMAT_JPEG
                image.verify()
            audio['covr']=[MP4Cover(raw,imageformat=kind)];cover_sha=digest(raw)
        except (KeyError,ValueError,OSError,Image.DecompressionBombError):
            warnings.append('Authored cover retained in the original source; this asset could not be embedded as a qualified PNG/JPEG cover.')
    audio.save();after=packet_hash(path)
    if before!=after:raise Error('tag_audio_changed','Metadata tagging changed encoded audio packets.')
    check=MP4(path)
    if check.get('\xa9nam',[])!=([selected['title']] if selected['title'] else []):raise Error('metadata_readback','Title metadata differs after tagging.')
    if check.get('\xa9ART',[])!=(selected['authors'] or []):raise Error('metadata_readback','Author metadata differs after tagging.')
    for field,name in freeform.items():
        values=[bytes(x).decode('utf-8') for x in check.get('----:com.apple.iTunes:'+name,[])]
        if values!=([str(selected[field])] if selected[field] is not None else []):raise Error('metadata_readback','Metadata readback failed for '+field)
    if cover_sha and digest(bytes(check['covr'][0]))!=cover_sha:raise Error('metadata_readback','Cover metadata differs after tagging.')
    return {**selected,'override_states':{k:'cleared' if overrides[k] is None else 'set' for k in overrides},'cover_sha256':cover_sha,'audio_packet_sha256':after,'chapter_profile':CHAPTER_PROFILE,'warnings':warnings}


def verify_container(path,chapters,samples):
    probe=json.loads(run(['ffprobe','-v','error','-show_chapters','-show_streams','-show_format','-of','json',path]).stdout)
    audio=[s for s in probe['streams'] if s['codec_type']=='audio']
    if len(audio)!=1 or audio[0]['codec_name']!='aac' or int(audio[0]['sample_rate'])!=RATE or audio[0]['channels']!=1:
        raise Error('container_validation','Audiobook audio format differs from uniform source PCM.')
    duration=float(probe['format']['duration']);actual=probe.get('chapters',[])
    if abs(duration-samples/RATE)>.1 or len(actual)!=len(chapters):raise Error('container_validation','M4B duration or chapter count differs from PCM timeline.')
    for expected,got in zip(chapters,actual):
        start,end=float(got['start_time']),float(got['end_time'])
        if end<=start or abs(start-expected['start_sample']/RATE)>.002 or abs(end-expected['end_sample']/RATE)>.002:
            raise Error('chapter_validation','Chapter range differs from the sample timeline after serialization.')
        if got.get('tags',{}).get('title')!=expected['title']:raise Error('chapter_validation','Chapter title differs after serialization.')
    # No second, silently truncated 255-entry/255-byte chapter representation.
    from struct import unpack
    def atoms(stream,start,end):
        position=start
        while position+8<=end:
            stream.seek(position);size,kind=unpack('>I4s',stream.read(8));header=8
            if size==1:size=unpack('>Q',stream.read(8))[0];header=16
            elif size==0:size=end-position
            if size<header or position+size>end:raise Error('container_validation','Invalid MP4 atom extent.')
            if kind==b'chpl':raise Error('chapter_validation','Conflicting Nero chapter representation is present.')
            if kind in (b'moov',b'udta'):atoms(stream,position+header,position+size)
            position+=size
    with Path(path).open('rb') as stream:atoms(stream,0,Path(path).stat().st_size)
    return probe
