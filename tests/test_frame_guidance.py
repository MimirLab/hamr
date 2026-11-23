import httpx
import numpy as np
import pytest

from audiobook import backend, jobs, performance, planner, preview_compare
from audiobook.core import Error, filehash
from audiobook.importers import inspect


SCHEDULE = {'start_frame':1, 'end_frame':3, 'scale':1.5, 'release_frames':1}
PROFILE = {'runtime_revision':'frame-prototype', 'settings':{'device':'cpu'},
           'frame_guidance':performance.FRAME_GUIDANCE_CAPABILITY}


@pytest.fixture
def voice(tmp_path):
    reference = tmp_path / 'reference.wav'
    reference.write_bytes(b'fixture reference bytes')
    return {'reference':str(reference), 'reference_sha256':filehash(reference),
            'transcript':'Reference words.'}


@pytest.mark.parametrize('value', [
    {**SCHEDULE, 'start_frame':True}, {**SCHEDULE, 'end_frame':0},
    {**SCHEDULE, 'scale':float('nan')}, {**SCHEDULE, 'release_frames':1500},
])
def test_reject_invalid_schedule(value):
    with pytest.raises(Error) as error:
        performance.validated_frame_guidance(value)
    assert error.value.code=='frame_guidance'


def test_scheduled_passage_pins_its_clip_and_preview_options(tmp_path, voice):
    source = tmp_path / 'story.md'
    source.write_text('Wait by the door.\n\nThe sky was clear.')
    document = inspect(source)
    scope = {document.blocks[0].id:{'text':'Wait by the door.', 'instruction':'Whisper softly.',
                                    'frame_guidance':SCHEDULE}}
    policy = {'document':{'source_sha256':document.source_sha256,
                          'performance':{'passage_instructions':scope}}}
    with pytest.raises(Error) as error:
        performance.bind(planner.plan(document,policy), {'runtime_revision':'stock'}, voice)
    assert error.value.code=='frame_guidance_unavailable'

    book = planner.plan(document,policy)
    performance.bind(book,PROFILE,voice)
    first,neighbor = book['utterances']
    request = performance.request(first)
    assert request[1]['cfg_scale']==0
    assert request[1]['frame_guidance']==performance.validated_frame_guidance(SCHEDULE)
    baseline = {'voice':voice, 'backend':PROFILE}
    controls = {'saved':{}, 'ordinary':{'frame_guidance':None}}
    variants = list(preview_compare.requests(first,voice,PROFILE,controls))
    assert variants[0][1:]==request
    assert variants[1][2]['cfg_scale']==1 and 'frame_guidance' not in variants[1][2]
    scope[document.blocks[0].id]['frame_guidance'] = {**SCHEDULE, 'release_frames':2}
    changed = planner.plan(document,policy)
    performance.bind(changed,PROFILE,voice)
    updated,untouched = changed['utterances']
    assert jobs.clip_key(updated,baseline)!=jobs.clip_key(first,baseline)
    assert jobs.clip_key(neighbor,baseline)==jobs.clip_key(untouched,baseline)


@pytest.mark.parametrize('confirmed',[True,False])
def test_http_requires_agreement_before_accepting_scheduled_audio(voice,confirmed):
    samples = (8192*np.sin(np.arange(7680)*0.10)).astype('<i2').tobytes()
    headers = {'X-Sample-Rate':'24000', 'X-Sample-Format':'s16le',
               'X-Breeze-Guidance-Window':'1:3:1.5', 'X-Breeze-Guidance-Base':'0',
               'X-Breeze-Guidance-Release':'1' if confirmed else '2', 'X-Breeze-Guidance-Chunks':'10'}
    engine = backend.Engine.__new__(backend.Engine)
    engine.identity = PROFILE
    engine.health = {'frame_guidance':performance.FRAME_GUIDANCE_CAPABILITY}
    engine.read_log = lambda: 'backend: CPU\n'

    def server(request):
        assert b'1:3:1.5' in request.read()
        return httpx.Response(200,headers=headers,content=samples)

    with httpx.Client(base_url='http://127.0.0.1',transport=httpx.MockTransport(server)) as client:
        engine.client = client
        if confirmed:
            data, receipt = engine.speech('Wait by the door.',voice,frame_guidance=SCHEDULE)
            assert data==samples
            assert receipt['frame_guidance']['generated_frames']==4
        else:
            with pytest.raises(Error) as error:
                engine.speech('Wait by the door.',voice,frame_guidance=SCHEDULE)
            assert error.value.code=='frame_guidance_unconfirmed'
