"""Real FFmpeg/MP4 controls; tone PCM tests packaging, never TTS quality."""
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import numpy as np
import pytest
from mutagen.mp4 import MP4
from PIL import Image
from audiobook.backend import validate_pcm
from audiobook.core import Store,RATE,atomic,writejson,Error,filehash
from audiobook.jobs import create,clip_key,render,repair,valid_clip,verify_publication
from audiobook.media import packet_hash,verify_container,merge_overrides
from audiobook.importers import epub


def prepared(tmp_path,source):
    store=Store(tmp_path/'state');output=tmp_path/"author's output é";output.mkdir()
    job=store.get(create(store,source,output=output)['job']);job['voice']={'name':'Distinct narrator','test':True};job['backend']={'test':'tone packaging control'};job['audio_gate']=False;store.save(job)
    data=(np.sin(np.arange(RATE//4)*2*np.pi*440/RATE)*10000).astype('<i2').tobytes()
    for u in job['plan']['utterances']:
        key=clip_key(u,job);_,receipt=validate_pcm(data);receipt['key']=key
        atomic(store.root/'clips'/key/'audio.pcm',data);writejson(store.root/'clips'/key/'receipt.json',receipt)
    return store,job


@pytest.mark.parametrize('count',[254,255,256])
def test_all_chapters_and_long_utf8_title_survive_actual_muxing(tmp_path,count):
    source=tmp_path/"chapters' source.md";long_title='002 '+('é界'*80)
    source.write_text('\n\n'.join('# '+(long_title if i==1 else 'Same label')+'\n\nCANARY '+str(i)+'.' for i in range(count)))
    store,job=prepared(tmp_path,source);result=render(store,job['id'],allow_synthesis=False)
    path=Path(result['output']);companion=json.loads((path/'companion.json').read_text())
    probe=verify_container(path/'book.m4b',companion['chapters'],companion['samples'])
    assert len(probe['chapters'])==count and probe['chapters'][1]['tags']['title']==long_title
    assert all(c['tags']['title']=='Same label' for i,c in enumerate(probe['chapters']) if i!=1)
    assert result['metrics']['clips_rendered']==0


def metadata_source(path):
    png=io.BytesIO();Image.new('RGB',(12,12),'blue').save(png,'PNG');cover=png.getvalue()
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Source title</dc:title><dc:creator>Source author</dc:creator><meta name="calibre:series" content="Source series"/><meta name="calibre:series_index" content="2"/></metadata><manifest><item id="c" href="c.xhtml"/><item id="cover" href="cover.png" properties="cover-image"/></manifest><spine><itemref idref="c"/></spine></package>')
        z.writestr('c.xhtml','<html><body><h1>Equals = semi ; hash # slash \\ line<br/>é界</h1><p>Keep this passage.</p></body></html>')
        z.writestr('cover.png',cover)
    return cover


def test_epub_collection_and_contributor_roles_do_not_become_false_authorship(tmp_path):
    source=tmp_path/'metadata.epub';metadata_source(source)
    with zipfile.ZipFile(source) as archive:entries={n:archive.read(n) for n in archive.namelist()}
    package=entries['book.opf'].decode().replace('<dc:creator>Source author</dc:creator>',
        '<dc:creator xmlns:opf="http://www.idpf.org/2007/opf" opf:role="edt">Editor only</dc:creator><dc:creator id="a">Actual author</dc:creator><meta refines="#a" property="role">aut</meta>')
    package=package.replace('<meta name="calibre:series" content="Source series"/><meta name="calibre:series_index" content="2"/>',
        '<meta id="other" property="belongs-to-collection">Library collection</meta><meta refines="#other" property="collection-type">set</meta><meta refines="#other" property="group-position">99</meta><meta id="series" property="belongs-to-collection">Actual series</meta><meta refines="#series" property="collection-type">series</meta><meta refines="#series" property="group-position">3</meta>')
    entries['book.opf']=package.encode();data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        for name,raw in entries.items():archive.writestr(name,raw)
    metadata=epub(data.getvalue()).metadata
    assert metadata['authors']==['Actual author']
    assert metadata['contributors']==[{'name':'Editor only','role':'edt'}]
    assert (metadata['series'],metadata['series_part'])==('Actual series','3')


def test_author_narrator_series_cover_and_newline_titles_coexist(tmp_path):
    source=tmp_path/'metadata.epub';cover=metadata_source(source);store,job=prepared(tmp_path,source)
    result=render(store,job['id'],allow_synthesis=False);out=Path(result['output']);tags=MP4(out/'book.m4b')
    assert tags['\xa9ART']==['Source author']
    assert bytes(tags['----:com.apple.iTunes:NARRATOR'][0]).decode()=='Distinct narrator'
    assert bytes(tags['----:com.apple.iTunes:SERIES'][0]).decode()=='Source series'
    assert bytes(tags['covr'][0])==cover
    companion=json.loads((out/'companion.json').read_text());probe=verify_container(out/'book.m4b',companion['chapters'],companion['samples'])
    assert '\né界' in probe['chapters'][0]['tags']['title']


def test_real_cli_metadata_clear_remux_preserves_packets_clips_and_previous_book(tmp_path):
    source=tmp_path/'metadata.epub';metadata_source(source);store,job=prepared(tmp_path,source)
    original=Path(render(store,job['id'],allow_synthesis=False)['output']);before=filehash(original/'book.m4b');audio=packet_hash(original/'book.m4b')
    clips={clip_key(u,job):valid_clip(store,clip_key(u,job))['sha256'] for u in job['plan']['utterances']}
    metadata=tmp_path/'clear.json';writejson(metadata,{'series':None,'authors':None,'cover':None})
    process=subprocess.run([sys.executable,'-m','audiobook','--home',str(store.root),'repair',job['id'],'--metadata',str(metadata),'--json'],capture_output=True,text=True,check=True)
    assert json.loads(process.stdout)['revision']==2
    result=render(store,job['id'],allow_synthesis=False);out=Path(result['output']);tags=MP4(out/'book.m4b')
    assert '\xa9ART' not in tags and 'covr' not in tags and '----:com.apple.iTunes:SERIES' not in tags
    assert '----:com.apple.iTunes:SERIES-PART' not in tags
    assert packet_hash(out/'book.m4b')==audio and filehash(original/'book.m4b')==before
    assert all(valid_clip(store,k)['sha256']==sha for k,sha in clips.items())
    assert json.loads((out/'report.json').read_text())['publication_metadata']['audio_stream_copy'] is True
    repair(store,job['id'],metadata={'inherit':['series','authors','cover']});third=Path(render(store,job['id'],allow_synthesis=False)['output'])
    assert MP4(third/'book.m4b')['\xa9ART']==['Source author']
    assert MP4(third/'book.m4b')['covr']
    assert packet_hash(third/'book.m4b')==audio


def test_a_playable_but_shortened_container_is_rejected(tmp_path):
    source=tmp_path/'book.md';source.write_text('# One\n\nA source passage.\n\n# Two\n\nThe final canary remains.')
    store,job=prepared(tmp_path,source);out=Path(render(store,job['id'],allow_synthesis=False)['output']);c=json.loads((out/'companion.json').read_text());short=tmp_path/'short.m4b'
    subprocess.run(['ffmpeg','-v','error','-i',str(out/'book.m4b'),'-t','0.3','-map','0:a','-c','copy',str(short)],check=True)
    with pytest.raises(Error):verify_container(short,c['chapters'],c['samples'])
    manifest=json.loads((out/'manifest.json').read_text());manifest['files']={};writejson(out/'manifest.json',manifest)
    assert not verify_publication(out,store.get(job['id']))


@pytest.mark.parametrize('text',['Compare x < y, then x --> y.','A <c.red>word</c> & an ampersand.',
                                 'Literal &lt;i&gt;italic&lt;/i&gt; and &amp; ampersand.'])
def test_webvtt_round_trips_literal_narration(tmp_path,text):
    source=tmp_path/'book.md'
    source.write_text(text)
    store,job=prepared(tmp_path,source)
    result=render(store,job['id'],allow_synthesis=False)
    companion=Path(result['output'])/'companion.vtt'
    subtitles=subprocess.run(['ffmpeg','-v','error','-i',companion,'-f','srt','-'],
                             capture_output=True,text=True,check=True).stdout
    assert job['plan']['utterances'][0]['text'] in subtitles


def test_failed_full_audio_decode_does_not_publish(tmp_path,monkeypatch):
    from audiobook import jobs,media
    source=tmp_path/'book.md'
    source.write_text('A connected recording to package.')
    store,job=prepared(tmp_path,source)
    real_run=media.run

    def failed_decode(args,*a,**kw):
        if '-xerror' in args:raise Error('subprocess_failed','Fixture decoder failure.')
        return real_run(args,*a,**kw)

    monkeypatch.setattr(media,'run',failed_decode)
    with pytest.raises(Error) as failure:render(store,job['id'],allow_synthesis=False)
    assert failure.value.code=='audio_decode_failed'
    assert not jobs.final_path(job).exists()
    assert valid_clip(store,clip_key(job['plan']['utterances'][0],job))


def test_invalid_metadata_update_is_atomic_and_does_not_restore_cleared_values(tmp_path):
    assert merge_overrides({'series':None},{'narrator':'A'})=={'series':None,'narrator':'A'}
    assert merge_overrides({'series':None},{'inherit':['series']})=={}
    source=tmp_path/'book.md';source.write_text('Keep all source words.')
    store,job=prepared(tmp_path,source);before=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
    with pytest.raises(Error):repair(store,job['id'],metadata={'authors':'invalid scalar'})
    assert store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]==before


def test_reimport_of_unchanged_published_plan_has_valid_repackage_lineage(tmp_path):
    source=tmp_path/'book.md';source.write_text('# One\n\nKeep the source words.')
    store,job=prepared(tmp_path,source);original=Path(render(store,job['id'],allow_synthesis=False)['output'])
    before=packet_hash(original/'book.m4b')
    repair(store,job['id'],reimport=True)
    out=Path(render(store,job['id'],allow_synthesis=False)['output'])
    assert packet_hash(out/'book.m4b')==before


@pytest.mark.parametrize('remux',[False,True])
def test_owned_stage_hardlinks_are_unlinked_without_overwriting_neighbor_files(tmp_path,remux):
    import os
    source=tmp_path/'source.md';source.write_text('# Chapter\n\nSOURCE_CANARY.')
    store,job=prepared(tmp_path,source);prior=None
    if remux:
        prior=Path(render(store,job['id'],allow_synthesis=False)['output'])/'book.m4b';prior_hash=filehash(prior)
        repair(store,job['id'],metadata={'title':'Retagged'});job=store.get(job['id'])
    stage=Path(job['output_parent'])/f'.audiobook-{job["id"]}-r{job["revision"]}.partial';stage.mkdir()
    writejson(stage/'owner.json',{'job':job['id'],'revision':job['revision']})
    neighbors=[]
    for leaf in ('book.m4b','companion.json','encode.log','original.md'):
        sentinel=tmp_path/('neighbor-'+leaf);sentinel.write_bytes(('UNRELATED '+leaf).encode());neighbors.append((sentinel,filehash(sentinel)))
        os.link(sentinel,stage/leaf)
    result=render(store,job['id'],allow_synthesis=False)
    assert all(filehash(path)==sha and path.stat().st_nlink==1 for path,sha in neighbors)
    assert filehash(source)==job['document']['source_sha256'] and result['metrics']['clips_rendered']==0
    if prior:assert filehash(prior)==prior_hash




@pytest.mark.parametrize('field,value',[('sample_rate',48000),('samples',True),('seconds',float('nan'))])
def test_incompatible_or_invalid_pcm_receipts_are_not_reused(tmp_path,field,value):
    source=tmp_path/'book.md';source.write_text('Keep the source.')
    store,job=prepared(tmp_path,source);key=clip_key(job['plan']['utterances'][0],job);path=store.root/'clips'/key/'receipt.json'
    receipt=json.loads(path.read_text());receipt[field]=value;writejson(path,receipt)
    assert valid_clip(store,key) is None
