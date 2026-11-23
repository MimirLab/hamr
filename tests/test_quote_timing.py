import pytest

from audiobook import planner, quote_timing
from audiobook.core import Store, digest
from audiobook.importers import inspect


TEXT = 'The rain stopped. The room was empty. “Wait by the door,” she whispered. The lamps went out.'


@pytest.mark.parametrize(('source','expected'),[
    (TEXT, '“Wait by the door,”'),
    ('The rain stopped. The room was empty. “Wait.” The trees whispered. The lamps went out.', None),
    ('The rain stopped. “Stand by,” he said. “Wait by the door,” she whispered. Now it was dark.',
     '“Wait by the door,”'),
    ('“Wait by the door,” he whispered. “Stay here,” she whispered. The lamps went out.', None),
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
    monkeypatch.setattr(quote_timing,'measurements',lambda *args:observed)
    _,receipt = quote_timing.render(store,job,unit,checkpoint,render)
    assert len(requests) == 2
    if uncertain:
        assert requests[-1] == {'seed':42}
        assert receipt['quote_timing']['selected'] == 'ordinary'
    else:
        assert requests[-1]['frame_guidance']['start_frame'] == 2
        assert receipt['quote_timing']['selected'] == 'directed'
