import pytest

from audiobook import performance, planner, quote_timing
from audiobook.core import Store, digest
from audiobook.importers import inspect


TEXT = 'The rain stopped. The room was empty. “Wait by the door,” she whispered. The lamps went out.'


@pytest.mark.parametrize(('source','expected'),[
    (TEXT, '“Wait by the door,”'),
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


def test_inline_calibration_recovers_fragmented_scope_after_interruption(tmp_path,monkeypatch):
    frame = quote_timing.FRAME
    text = 'The hall was empty. “Keep the lamp low,” he whispered. “We have to leave before dawn.”'
    unit = {'text':text,'quote_timing':quote_timing.scope(text,text,24)}
    store = Store(tmp_path/'state')
    job = {'id':'abc123','quote_timing_models':{'timing_algorithm':8},
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
                windows.append({'scope':2,'start_frame':10,'end_frame':12,'release_frames':0})
            receipt['quote_guidance'] = {'scopes':spans,'generated_frames':12,'windows':windows}
        if schedule:=options.get('frame_guidance'):
            assert [(row['start_frame'],row['end_frame']) for row in schedule['windows']]==[(1,5),(10,'speech')]
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
