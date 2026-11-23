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
