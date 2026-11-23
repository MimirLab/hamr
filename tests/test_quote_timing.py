import pytest

from audiobook import performance, planner, quote_timing
from audiobook.core import Store, digest
from audiobook.importers import inspect


TEXT = 'The rain stopped. The room was empty. “Wait by the door,” she whispered. The lamps went out.'


@pytest.mark.parametrize(('source','expected'),[
    (TEXT, '“Wait by the door,”'),
    ('“Wait by the door,” she whispered. The street was empty. She lowered the lamp.',
     '“Wait by the door,”'),
    ('The rain stopped. The room was empty. “Wait.” The trees whispered. The lamps went out.', None),
    ('The rain stopped. “Stand by,” he said. “Wait by the door,” she whispered. Now it was dark.',
     '“Wait by the door,”'),
    ('“Wait by the door,” he whispered. “Stay here,” she whispered. The lamps went out.', None),
    ('He laughed. “Lady Narborough,” he whispered. “She perfectly adores him.”',
     ('“Lady Narborough,”','“She perfectly adores him.”')),
])
def test_unique_owned_whisper_in_the_ordinary_plan(tmp_path,source,expected):
    document = tmp_path/'story.md'
    document.write_text(source)
    planned = planner.plan(inspect(document))
    unit, = planned['utterances']
    timing = unit.get('quote_timing')
    if expected is None:
        assert timing is None
    else:
        assert timing is not None
        if isinstance(expected,tuple):
            assert tuple(unit['text'][slice(*span)] for span in timing['quotations']) == expected
            backend = {'frame_guidance':performance.FRAME_GUIDANCE_CAPABILITY}
            job = {'backend':backend,'quote_timing_models':{'worker':'fixture'},
                   'voice':{'reference_sha256':'fixture'}}
            assert not quote_timing.active(job,unit)
            backend['frame_guidance_windows'] = performance.FRAME_WINDOWS_CAPABILITY
            assert quote_timing.active(job,unit)
        else:
            assert unit['text'][slice(*timing['quotation'])] == expected
        assert 'performance_candidate' not in unit
        plain, = planner.plan(inspect(document),{'performance':'off'})['utterances']
        assert 'quote_timing' not in plain


def test_refinement_preserves_the_onset_and_waits_for_the_actual_exit():
    frame = quote_timing.FRAME
    anchor = quote_timing.Edges(8*frame,10*frame,18*frame,20*frame)
    initial = anchor.proposal()
    directed = quote_timing.Edges(8*frame,10*frame,15*frame,16*frame)
    assert not directed.contains(initial)
    refined = directed.proposal(initial)
    assert (refined['start_frame'],refined['end_frame'],refined['release_frames']) == (10,15,1)
    assert directed.contains(refined)


def test_opening_whisper_uses_the_audio_boundary_and_preserves_old_plans():
    text = '“Stay here,” she whispered. The lamp went out. I heard the rain.'
    assert quote_timing.scope(text,text,24) is None
    target = quote_timing.scope(text,text,25)
    assert target['quotation'] == [0,text.index('”')+1]
    assert target['version'] == quote_timing.OPENING_VERSION
    expected = quote_timing.words(text)
    primary = []
    secondary = []
    independent = []
    intervals = [(0.14,0.44),(0.45,0.82),(0.94,1.05)]
    intervals.extend((1.1+i*.2,1.2+i*.2) for i in range(len(expected)-3))
    for word,(start,stop) in zip(expected,intervals,strict=True):
        primary.append({'word':word,'start':start,'end':stop})
        secondary.append({'word':word,'start':start-.04,'end':stop+.04})
        independent.append({'word':word,'first':start-.06,'last':stop+.06})
    span = target['quotation']
    samples = int((intervals[-1][1]+.5)*quote_timing.RATE)
    assert quote_timing.measurements(text,span,primary,secondary,samples=samples) == (
        quote_timing.Edges(0,2400,20640,21600))
    assert quote_timing.corroborated_edges(text,span,primary,independent,samples) == (
        quote_timing.Edges(0,1920,21120,21120))
    added = [{'word':'well','start':0,'end':.08},*secondary]
    assert quote_timing.measurements(text,span,primary,added,samples=samples) is None


def test_opening_quote_compares_with_the_following_narrator():
    sample = quote_timing.FRAME
    quiet = (100).to_bytes(2,'little')*sample
    narrator = (200).to_bytes(2,'little')*sample
    louder = (500).to_bytes(2,'little')*sample
    first = quote_timing.Edges(0,0,sample,sample)
    second = quote_timing.Edges(2*sample,2*sample,3*sample,3*sample)
    assert quote_timing.maximum_quote_power(quiet+narrator,[first]) == .25
    assert quote_timing.maximum_quote_power(quiet+narrator+quiet+louder,[first,second]) == .25


@pytest.mark.parametrize(('level','selected'),[(180,'ordinary'),(100,'directed')])
def test_opening_fallback_verifies_contrast_on_the_actual_take(tmp_path,monkeypatch,level,selected):
    frame = quote_timing.FRAME
    text = '“Wait by the lamp,” she whispered. The rain stopped. No one came.'
    unit = {'text':text,'quote_timing':quote_timing.scope(text,text,25)}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','backend':{},'quote_timing_models':{}}
    store.path(job).mkdir(exist_ok=True)
    measured = quote_timing.Edges(0,0,3*frame,4*frame)
    speech = (level.to_bytes(2,'little')*(3*frame) + bytes(2*frame)
              + (200).to_bytes(2,'little')*(2*frame))
    ordinary = (250).to_bytes(2,'little')*(6*frame)
    calls = []

    def synthesize(text,options):
        calls.append(options)
        audio = speech if 'frame_guidance' in options else ordinary
        return audio,{'sha256':digest(audio),'samples':len(audio)//2}

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'measure_take',lambda *args:([measured],[]))
    data,receipt = quote_timing.render(store,job,unit,'a'*64,synthesize)
    report = receipt['quote_timing']
    assert calls[0]['instruction'] == quote_timing.OPENING_INSTRUCTION
    assert report['selected'] == selected
    assert report['trials'][-1]['maximum_quote_power_ratio'] == (level/200)**2
    if selected == 'ordinary':
        assert report['reason'] == 'insufficient_contrast' and data == ordinary
    else:
        assert data == speech


@pytest.mark.parametrize(('quiet_final','causal_ok'),[(True,False),(False,False),(False,True)])
def test_opening_ordinary_anchor_is_reused_or_corrected(tmp_path,monkeypatch,quiet_final,causal_ok):
    frame = quote_timing.FRAME
    text = '“Wait by the lamp,” she whispered. The rain stopped. No one came.'
    unit = {'text':text,'quote_timing':quote_timing.scope(text,text,25)}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','backend':{},'quote_timing_models':{'timing_algorithm':11}}
    store.path(job).mkdir(exist_ok=True)
    measures = quote_timing.Edges(0,0,3*frame,5*frame)
    tail = bytes(4*frame)+(200).to_bytes(2,'little')*(2*frame)
    ordinary = (250).to_bytes(2,'little')*(7*frame)
    quiet = (100).to_bytes(2,'little')*(3*frame)+tail
    loud = (180).to_bytes(2,'little')*(3*frame)+tail
    requests = []
    causal_attempts = []

    def synthesize(text,options):
        requests.append(options)
        schedule = options.get('frame_guidance')
        audio = ordinary if schedule is None else quiet if quiet_final and schedule['end_frame']>3 else loud
        return audio,{'sha256':digest(audio),'samples':len(audio)//2}

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'measure_take',lambda *args:([measures],[]))
    def causal(*args):
        causal_attempts.append(len(requests))
        if causal_ok:
            return (quiet,{'sha256':digest(quiet),'samples':len(quiet)//2}),'decoder',{}
        return None,None,{}

    monkeypatch.setattr(quote_timing,'causal_take',causal)
    audio,receipt = quote_timing.render(store,job,unit,'a'*64,synthesize)
    assert requests[0] == {'seed':42}
    windows = [options['frame_guidance'] for options in requests[1:]]
    assert [(window['end_frame'],window['release_frames']) for window in windows] == (
        [(3,2),(4,1)] if quiet_final else [(3,2),(4,1),(5,0)])
    report = receipt['quote_timing']
    assert report['trials'][0]['anchor'] == 'ordinary' and report['trials'][0]['schedule'] is None
    assert report['selected'] == ('directed' if quiet_final or causal_ok else 'ordinary')
    assert report.get('controller') == ('decoder' if causal_ok else None)
    assert audio == (quiet if quiet_final or causal_ok else ordinary)
    assert causal_attempts == ([] if quiet_final else [len(requests)])
    requests.clear()
    replayed,receipt = quote_timing.render(store,job,unit,'a'*64,synthesize)
    assert not requests and replayed==audio
    assert all(trial['checkpoint_reused'] for trial in receipt['quote_timing']['trials'])


def test_opening_anchor_does_not_spend_the_contrast_retry_on_timing(tmp_path,monkeypatch):
    frame = quote_timing.FRAME
    text = '“Wait by the lamp,” she whispered. The rain stopped. No one came.'
    unit = {'text':text,'quote_timing':quote_timing.scope(text,text,25)}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','backend':{},'quote_timing_models':{'timing_algorithm':11}}
    store.path(job).mkdir(exist_ok=True)
    samples = (100).to_bytes(2,'little')*(8*frame)
    calls = []

    def synthesize(text,options):
        calls.append(options)
        return samples,{'sha256':digest(samples),'samples':len(samples)//2}

    def measure(recipe,trial,*args):
        end = (2+int(trial.name))*frame
        return [quote_timing.Edges(0,0,end,end+2*frame)],[]

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'measure_take',measure)
    _,receipt = quote_timing.render(store,job,unit,'a'*64,synthesize)
    assert len(calls)==3 and calls[0]=={'seed':42}
    assert [options['frame_guidance']['end_frame'] for options in calls[1:]]==[2,3]
    assert receipt['quote_timing']['selected']=='ordinary'


@pytest.mark.parametrize('window,accepted',[(3,True),(4,False)])
def test_decoder_only_adopts_its_measured_scope(tmp_path, monkeypatch, window, accepted):
    frame = quote_timing.FRAME
    left = TEXT.index('“')
    right = TEXT.index('”')+1
    target = {'quotation':[left,right]}
    unit = {'text':TEXT,'quote_timing':target}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','quote_timing_models':{},
           'backend':{'frame_guidance':performance.FRAME_GUIDANCE_CAPABILITY,'quote_guidance':1}}
    store.path(job).mkdir(exist_ok=True)
    pcm = (800).to_bytes(2,'little')*(2*frame)+bytes(2*4*frame)
    called = []

    def speech(text, options):
        called.append(options)
        receipt = {'sha256':digest(pcm),'samples':len(pcm)//2}
        if spans:=options.get('quote_guidance'):
            assert spans == [[left+3,right+1]]
            receipt['quote_guidance'] = {'scopes':spans,'generated_frames':6,'windows':[
                {'scope':1,'start_frame':1,'end_frame':window,'release_frames':0}]}
        return pcm,receipt

    def measure(recipe, take, *args):
        return ([quote_timing.Edges(frame,2*frame,3*frame,3*frame)],[]) if take.name=='decoder' else ([],[])

    monkeypatch.setattr(quote_timing,'measure_take',measure)
    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    _, result = quote_timing.render(store,job,unit,'a'*64,speech)
    timing = result['quote_timing']
    assert timing['selected']==('directed' if accepted else 'ordinary')
    assert len(called)==(1 if accepted else 3)
    if accepted:
        assert timing['controller']=='decoder'
        called.clear()
        _, replay = quote_timing.render(store,job,unit,'a'*64,speech)
        assert replay['quote_timing']['decoder_attempt']['checkpoint_reused'] and not called
    else:
        assert timing['decoder_attempt']['reason']=='guidance_outside_quote'


@pytest.mark.parametrize('weak_return',[False,True])
def test_directed_candidates_compare_reporting_words_before_publication(tmp_path,monkeypatch,weak_return):
    frame = quote_timing.FRAME
    target = quote_timing.scope(TEXT,TEXT,performance.CONTEXT_VERSION)
    unit = {'text':TEXT,'quote_timing':target}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','plan':{},'quote_timing_models':{'narrator_return':1},
           'backend':{'frame_guidance':performance.FRAME_GUIDANCE_CAPABILITY,'quote_guidance':1}}
    store.path(job).mkdir(exist_ok=True)
    ordinary,decoder,timed = (level.to_bytes(2,'little')*(8*frame) for level in (500,250,400))
    requests = []

    def speech(text,options):
        requests.append(options)
        data = ordinary if options.get('frame_guidance',quote_timing.ANCHOR)==quote_timing.ANCHOR else timed
        if spans:=options.get('quote_guidance'):
            data = decoder
        receipt = {'samples':len(data)//2,'sha256':digest(data)}
        if spans:
            receipt['quote_guidance'] = {'scopes':spans,'windows':[
                {'scope':1,'start_frame':2,'end_frame':4,'release_frames':0}]}
        return data,receipt

    def relative(text,spans,rows,data):
        assert [text[a:b] for a,b in spans]==['she whispered','The lamps went out']
        baseline = [.62,.66] if weak_return else [.20,.18]
        return {ordinary:baseline, decoder:[.11,.12], timed:[.4,.42]}[data]

    monkeypatch.setattr(quote_timing,'verified_models',lambda recipe:True)
    monkeypatch.setattr(quote_timing,'measure_take',lambda *args:([quote_timing.Edges(frame,2*frame,4*frame,6*frame)],[]))
    monkeypatch.setattr(quote_timing,'maximum_quote_power',lambda *args:.2)
    monkeypatch.setattr(quote_timing,'timed_transcripts',lambda *args:[[],[]])
    monkeypatch.setattr(quote_timing,'narrator_return_power',relative)
    selected,receipt = quote_timing.render(store,job,unit,'a'*64,speech)
    comparison = receipt['quote_timing']['decoder_attempt']['narrator_return']
    assert comparison['ordinary_pcm_sha256']==digest(ordinary)
    assert comparison['retained'] is not weak_return
    assert selected==(timed if weak_return else decoder)
    assert receipt['quote_timing']['selected']=='directed'
    assert requests[1]=={'seed':42}
    requests.clear()
    again,_ = quote_timing.render(store,job,unit,'a'*64,speech)
    assert again==selected and not requests


def test_reporting_measurement_uses_the_owned_clause_and_agreed_local_boundaries():
    text = '“Come away, Jim; come away,” she whispered. He followed her outside.'
    target = quote_timing.scope(text,text,performance.CONTEXT_VERSION)
    regions = quote_timing.narrator_return_regions(text,[target['quotation']],performance.CONTEXT_VERSION)
    assert [text[a:b] for a,b in regions]==['she whispered','He followed her outside']
    words = quote_timing.words(text)
    primary = [{'word':word,'start':i/5,'end':(i+1)/5} for i,word in enumerate(words)]
    # The secondary observer can differ elsewhere in the quote and still
    # corroborate the phrase. A nonoverlapping estimate cannot do that.
    secondary = [dict(row) for row in primary]
    secondary[3]['word'] = "i'm"
    levels = [100]*7+[200]*4
    data = b''.join(level.to_bytes(2,'little')*(quote_timing.RATE//5) for level in levels)
    assert quote_timing.narrator_return_power(text,regions,[primary,secondary],data)==[.25,.25]
    secondary[5].update(start=.1,end=.2)
    secondary[6].update(start=.2,end=.3)
    assert quote_timing.narrator_return_power(text,regions,[primary,secondary],data) is None


def test_ordinary_timing_uses_compiled_offsets_after_word_expansion(tmp_path):
    text = ('Dr. Hale waited by the door. He looked at the lamp. '
            '“Keep Dr. Hale quiet,” he whispered. “We have to leave before dawn.”')
    source = tmp_path/'story.md'
    source.write_text(text)
    document = inspect(source)
    unit, = planner.plan(document)['utterances']
    assert unit['text'].startswith('Doctor Hale')
    first, second = unit['quote_timing']['quotations']
    assert unit['text'][slice(*first)] == '“Keep Doctor Hale quiet,”'
    assert unit['text'][slice(*second)] == '“We have to leave before dawn.”'
    assert unit['quote_timing']['source_spellings'] == [[0,'dr','doctor'],[12,'dr','doctor']]
    prior, = planner.plan(document, delivery_context=21)['utterances']
    assert 'quote_timing' not in prior
    assert quote_timing.scope(text.replace('whispered', 'said'), unit['text'], 22) is None
    different_quotes = '“Stop,” she said. ' + unit['text']
    assert quote_timing.scope(text, different_quotes, 22) is None


def test_inline_cues_keep_exact_words_and_utf8_scope():
    text = 'The door creaked. “Écoute,” she whispered. “Wait here.”'
    target = quote_timing.scope(text,text,24)
    marked,scopes = quote_timing.inline_request(text,target['quotations'])
    assert marked.replace('(whispering) ','')==text
    packed = marked.encode('utf-8')
    assert [packed[start:end].decode('utf-8') for start,end in scopes]==[
        '(whispering) Écoute,','(whispering) Wait here.',
    ]


@pytest.mark.parametrize(('algorithm','second_start'),[(8,10),(9,9)])
def test_inline_calibration_recovers_fragmented_scope_after_interruption(tmp_path,monkeypatch,algorithm,second_start):
    frame = quote_timing.FRAME
    text = 'The hall was empty. “Keep the lamp low,” he whispered. “We have to leave before dawn.”'
    unit = {'text':text,'quote_timing':quote_timing.scope(text,text,24)}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','quote_timing_models':{'timing_algorithm':algorithm},
           'backend':{'frame_guidance':performance.FRAME_GUIDANCE_CAPABILITY,
                      'frame_guidance_windows':performance.FRAME_WINDOWS_CAPABILITY,
                      'quote_guidance':performance.QUOTE_GUIDANCE_VERSION,
                      'narration_conditioning':performance.NARRATION_CONDITIONING_VERSION}}
    store.path(job).mkdir(exist_ok=True)
    loud = (800).to_bytes(2,'little')*(12*frame)
    soft = loud[:2*frame]+(200).to_bytes(2,'little')*(11*frame)
    edges = [quote_timing.Edges(frame,2*frame,5*frame,5*frame),
             quote_timing.Edges(8*frame+1,10*frame,11*frame,12*frame)]
    calls = []

    def speak(delivery,options):
        calls.append(delivery)
        pcm = soft if '(whispering)' in delivery else loud
        receipt = {'sha256':digest(pcm),'samples':len(pcm)//2}
        if spans:=options.get('quote_guidance'):
            windows = [{'scope':1,'start_frame':1,'end_frame':5,'release_frames':0},
                       {'scope':2,'start_frame':9,'end_frame':11,'release_frames':0}]
            if pcm==soft:
                windows[0]['end_frame'] = 6
                windows[1]['start_frame'] = 8
                windows[1]['end_frame'] = 9
                windows.append({'scope':2,'start_frame':9,'end_frame':12,'release_frames':0})
            receipt['quote_guidance'] = {'scopes':spans,'generated_frames':12,'windows':windows}
        if schedule:=options.get('frame_guidance'):
            assert [(row['start_frame'],row['end_frame']) for row in schedule['windows']]==[(1,5),(second_start,'speech')]
            receipt['frame_guidance'] = schedule
        return pcm,receipt

    def measured(recipe,trial,receipt,spoken,quotes,spellings):
        assert spoken==text
        return edges,[]

    def interrupt(stage):
        if stage=='after_quote_inline':
            raise InterruptedError

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'measure_take',measured)
    monkeypatch.setattr(quote_timing,'fault',interrupt)
    with pytest.raises(InterruptedError):
        quote_timing.render(store,job,unit,'a'*64,speak)
    monkeypatch.setattr(quote_timing,'fault',lambda stage:None)
    data,receipt = quote_timing.render(store,job,unit,'a'*64,speak)
    report = receipt['quote_timing']
    assert report['selected']=='directed' and report['controller']=='inline' and data==soft
    assert report['decoder_attempt']['checkpoint_reused']
    assert report['inline_attempts'][0]['checkpoint_reused'] and len(calls)==3


def test_compiled_spellings_do_not_change_neighboring_or_unlicensed_words():
    expected = ['doctor','hale','said','doctor']
    spellings = [[0,'dr','doctor']]
    observed = [{'word':word} for word in ('dr','hale','said','dr')]
    compared = quote_timing.observation_spellings(observed,expected,spellings)
    assert [row['word'] for row in compared] == ['doctor','hale','said','dr']
    assert observed[0]['word'] == 'dr'
    assert quote_timing.observation_spellings(observed[1:],expected,spellings) == observed[1:]


def test_source_spelling_can_normalize_an_exact_prefix_without_hiding_extra_words():
    expected = ['doctor','hale','shone','the','lamp']
    spellings = [[0,'dr','doctor']]
    observed = [{'word':word} for word in ('dr','hale','shone','the','lamp','on')]
    normal = quote_timing.observation_spellings(observed,expected,spellings,allow_trailing=True)
    assert [row['word'] for row in normal]==[*expected,'on']
    for change in (['dr','hale','the','lamp'],['dr','hale','shone','the','light','on'],
                   ['dr','hale','then','shone','the','lamp','on']):
        wrong = [{'word':word} for word in change]
        assert quote_timing.observation_spellings(wrong,expected,spellings,allow_trailing=True)==wrong
    assert quote_timing.observation_spellings(observed,expected,spellings)==observed


def test_corroboration_requires_independent_words_near_all_quote_edges():
    text = 'He waited. “Stay here,” he whispered.'
    target = quote_timing.owned_whisper(text,23)[0]
    names = ['he','waited','stay','here','he','whispered']
    bounds = [(0,.2),(.25,.6),(1,1.1),(1.15,1.32),(1.55,1.65),(1.7,2.0)]
    primary = [{'word':name,'start':start,'end':end} for name,(start,end) in zip(names,bounds)]
    independent = quote_timing.emitted_words({
        'tokens':[' He',' waited','.',' Stay',' here',',',' he',' wh','ispered','.'],
        'timestamps':[0,.24,.64,.88,1.12,1.36,1.52,1.68,1.76,2.0],
        'text':'He waited. Stay here, he whispered.',
    })
    edges = quote_timing.corroborated_edges(text,target,primary,independent,52800)
    assert edges == quote_timing.Edges(14400,21120,31680,36480)
    independent[3]['word'] = 'there'
    assert quote_timing.corroborated_edges(text,target,primary,independent,52800) is None
    independent[3].update(word='here',first=1.48,last=1.56)
    assert quote_timing.corroborated_edges(text,target,primary,independent,52800) is None


def test_continuation_verifies_both_windows_and_keeps_a_still_valid_first_window():
    frame = quote_timing.FRAME
    first = quote_timing.Edges(frame,2*frame,5*frame,7*frame)
    second = quote_timing.Edges(10*frame,12*frame,18*frame,21*frame)
    initial = quote_timing.refine([first,second],None)
    shortened = quote_timing.Edges(11*frame,13*frame,16*frame,17*frame)
    new = quote_timing.refine([first,shortened],initial)
    assert new['windows'][0] == initial['windows'][0]
    assert new['windows'][1]['end_frame'] == 16
    assert new['windows'][1]['release_frames'] == 1
    assert shortened.contains(new['windows'][1])
    assert not shortened.contains(initial['windows'][1])
    terminal = quote_timing.refine([first,shortened],initial,ends_in_quote=True)
    assert terminal['windows'][0] == initial['windows'][0]
    assert terminal['windows'][1]['end_frame']=='speech'
    assert terminal['windows'][1]['release_frames']==0
    assert shortened.contains(terminal['windows'][1])


def test_short_taper_can_cover_the_quoted_tail_without_reaching_narration():
    frame = quote_timing.FRAME
    taper = {'start_frame': 2, 'end_frame': 5, 'release_frames': 2}
    edges = quote_timing.Edges(frame, 2*frame, 5*frame+frame//2, 7*frame)
    assert not edges.contains(taper)
    assert edges.contains(taper, allow_tapered_tail=True)
    assert not edges.contains({**taper, 'release_frames': 0}, allow_tapered_tail=True)
    assert not edges.contains({**taper, 'release_frames': 3}, allow_tapered_tail=True)
    too_early = quote_timing.Edges(frame, 2*frame, 6*frame+1, 7*frame)
    assert not too_early.contains(taper, allow_tapered_tail=True)


@pytest.mark.parametrize(('first_end','second_start','wrong_word','eligible'),[
    (4.34,4.30,False,True), (4.38,4.30,False,True), (4.39,4.30,False,False),
    (4.34,4.30,True,False), (4.28,4.30,False,False),
])
def test_exit_overlap_only_proposes_after_two_complete_transcripts(first_end,second_start,wrong_word,eligible):
    text = 'He waited by the door. “Keep the lamp low,” he whispered.'
    span = (text.index('“'),text.index('”')+1)
    labels = quote_timing.words(text)
    primary = [{'word':word,'start':index*.4,'end':index*.4+.2} for index,word in enumerate(labels)]
    primary[5].update(start=2.60,end=2.82)
    primary[6].update(start=2.85,end=3.08)
    primary[7].update(start=3.20,end=3.68)
    primary[8].update(start=3.98,end=first_end)
    primary[9].update(start=4.48,end=4.55)
    primary[10].update(start=4.56,end=4.80)
    secondary = [dict(row) for row in primary]
    secondary[8]['end'] = 4.24
    secondary[9]['start'] = second_start
    if wrong_word:
        secondary[10]['word'] = 'shouted'
    hint = quote_timing.exit_overlap(text,span,primary,secondary,120000)
    assert bool(hint)==eligible
    if hint:
        assert hint.proposal()['end_frame']==54
        assert not hint.proposal()['release_frames']
        assert quote_timing.measurements(text,span,primary,secondary,samples=120000) is None


@pytest.mark.parametrize('algorithm',[5,6])
@pytest.mark.parametrize(('verified_later','selected'),[(True,'directed'),(False,'ordinary')])
def test_render_uses_overlap_only_to_propose_a_new_take(tmp_path,monkeypatch,algorithm,verified_later,selected):
    text = 'He waited by the door. “Keep the lamp low,” he whispered.'
    store = Store(tmp_path/'home')
    job = {'id':'af23456789012345','quote_timing_models':{'timing_algorithm':algorithm}}
    store.path(job).mkdir()
    unit = {'text':text,'quote_timing':{'quotation':(text.index('“'),text.index('”')+1)}}
    requests = []
    frame = quote_timing.FRAME
    valid = quote_timing.Edges(frame,2*frame,5*frame,6*frame)

    def render(_,options):
        requests.append(options)
        pcm = bytes([len(requests),0])*19200
        return pcm, {'sha256':digest(pcm),'samples':19200}

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'timed_transcripts',lambda *args:([{}],[{}]))
    monkeypatch.setattr(quote_timing,'exit_overlap',
                        lambda *args:quote_timing.ExitOverlap(frame,2*frame,5*frame+1,5*frame))
    monkeypatch.setattr(quote_timing,'measure_take',
                        lambda *args:([valid if verified_later and len(requests)>1 else None],[]))
    _, receipt = quote_timing.render(store,job,unit,'a'*64,render)
    outcome = receipt['quote_timing']
    assert outcome['selected']==selected
    assert len(outcome['trials'])==(3 if algorithm==6 else 2)
    assert outcome['trials'][0]['edges_samples'] is None
    assert outcome['trials'][0]['exit_overlap_proposals']['0']['last_end']==5*frame+1
    assert requests[1]['seed']==(43 if algorithm==6 else 42)
    assert (requests[1]['frame_guidance']==quote_timing.ANCHOR)==(algorithm==6)
    assert ('frame_guidance' in requests[-1])==verified_later
    if not verified_later:
        assert requests[-1] == {'seed':42}


@pytest.mark.parametrize(('algorithm','safe_release','selected','directed_attempts'),[
    (3, True, 'ordinary', 4), (4, True, 'directed', 5), (4, False, 'ordinary', 5),
])
def test_repeated_schedule_gets_one_bounded_taper_retry(
        tmp_path,monkeypatch,algorithm,safe_release,selected,directed_attempts):
    text = 'The hall was empty. “Keep the lamp low,” he whispered. “We have to leave before dawn.”'
    store = Store(tmp_path/'home')
    job = {'id':'af23456789012345','quote_timing_models':{'timing_algorithm':algorithm}}
    store.path(job).mkdir()
    unit = {'text':text,'quote_timing':quote_timing.scope(text,text,23)}
    requests = []
    observed = iter([
        [(70560,80640,104640,105600),(113280,117120,144960,153600)],
        [(70560,84000,108000,113280),(125280,132480,165120,172800)],
        [(70560,84480,107040,107040),(120960,130560,164640,165120)],
        [(70560,84960,108000,116160),(127680,137280,168000,178560)],
        [(70560,82560,107520,113280 if safe_release else 108000),(122880,130560,158880,170880)],
    ])

    def render(_,options):
        requests.append(options)
        pcm = bytes([len(requests),0])*19200
        return pcm, {'sha256':digest(pcm),'samples':19200}

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'measure_take',
                        lambda *args:([quote_timing.Edges(*edge) for edge in next(observed)],[]))
    _, receipt = quote_timing.render(store,job,unit,'a'*64,render)
    outcome = receipt['quote_timing']
    assert outcome['selected']==selected
    assert len(outcome['trials'])==directed_attempts
    if algorithm==4:
        hard_cut = requests[3]['frame_guidance']
        taper = requests[4]['frame_guidance']
        assert hard_cut['windows'][0]['end_frame']==56
        assert hard_cut['windows'][0]['release_frames']==0
        assert taper['windows']==[{**hard_cut['windows'][0],'release_frames':1},hard_cut['windows'][1]]
    if selected=='ordinary':
        assert requests[-1] == {'seed':42}


def test_terminal_quote_edges_survive_printed_syllable_break_and_repeated_words():
    text = '“I now have it over,” he whispered. “He was deter-mined. Anne, now have it over.”'
    start, end = text.rindex('“'), len(text)
    transcript = 'i now have it over he whispered he was determined anne now have it over'

    def observe(heard):
        return [{'word': word, 'start': index * 0.2, 'end': (index * 0.2) + 0.1}
                for index, word in enumerate(heard.split())]

    primary = observe(transcript)
    secondary = observe(transcript.replace('anne', 'dan'))
    edges = quote_timing.measurements(text, (start, end), primary, secondary, samples=72000)
    assert edges == quote_timing.Edges(31200, 33600, 69600, 72000)
    assert quote_timing.measurements(text, (start, end), primary,
                                    observe(transcript.replace('determined', 'deter mined')),
                                    samples=76800)
    for wrong in ('he will', 'he said he was'):
        assert quote_timing.measurements(text, (start, end), primary,
                                        observe(transcript.replace('he was', wrong)), samples=72000) is None
    assert quote_timing.measurements(text, (start, end), observe(transcript.replace('determined', 'deterred')),
                                    secondary, samples=72000) is None


@pytest.mark.parametrize('uncertain',[False,True])
def test_restart_reuses_the_timing_anchor_and_retains_ordinary_fallback(tmp_path,monkeypatch,uncertain):
    store = Store(tmp_path/'home')
    job = {'id':'af23456789012345','quote_timing_models':{}}
    store.path(job).mkdir()
    target = quote_timing.scope(TEXT,TEXT,20)
    unit = {'text':TEXT,'quote_timing':target}
    requests = []

    def render(text,options):
        assert text == TEXT
        requests.append(options)
        pcm = bytes([len(requests),0])*19200
        return pcm, {'sha256':digest(pcm),'samples':19200}

    def interrupt(name):
        if name == 'after_quote_anchor':
            raise KeyboardInterrupt

    monkeypatch.setattr(quote_timing,'verified_models',lambda _:True)
    monkeypatch.setattr(quote_timing,'fault',interrupt)
    checkpoint = 'a'*64
    with pytest.raises(KeyboardInterrupt):
        quote_timing.render(store,job,unit,checkpoint,render)
    assert len(requests) == 1
    monkeypatch.setattr(quote_timing,'fault',lambda _:None)
    monkeypatch.setattr(quote_timing,'observe',lambda *args: ([{}],[{}]))
    frame = quote_timing.FRAME
    observed = None if uncertain else quote_timing.Edges(frame,2*frame,5*frame,7*frame)
    monkeypatch.setattr(quote_timing,'measurements',lambda *args,**kwargs:observed)
    _,receipt = quote_timing.render(store,job,unit,checkpoint,render)
    assert len(requests) == 2
    if uncertain:
        assert requests[-1] == {'seed':42}
        assert receipt['quote_timing']['selected'] == 'ordinary'
    else:
        assert requests[-1]['frame_guidance']['start_frame'] == 2
        assert receipt['quote_timing']['selected'] == 'directed'
