"""Delivery, cache, and recovery controls. Tone PCM only tests the workflow."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from audiobook import backend, cli, jobs, performance, pipeline, planner, qa
from audiobook.core import RATE, Store, digest, key, loadjson, writejson
from audiobook.importers import inspect


SIGH = '[sigh] “I thought we had more time,” she said.'
WHISPER = '“Keep your voice down,” he whispered.'
IDENTITY = {'profile':'cpu-q8','runtime_revision':'fixture','runtime_archive_sha256':'f'*64,
            'model_sha256':'a'*64,'settings':{'device':'cpu'}}
VOICE = {'name':'Tone transport control'}
TONE = (np.sin(np.arange(6000)*2*np.pi*440/RATE)*10000).astype('<i2').tobytes()


def plan_source(tmp_path,text,performance_mode='experimental',**overrides):
    source=tmp_path/'book.md'
    source.write_text(text)
    return planner.plan(inspect(source),{'performance':performance_mode,**overrides})


def test_planner_owns_literal_events_and_attributed_direction_without_shortening_sentence(tmp_path):
    result=plan_source(tmp_path,f'# Scene\n\n[sigh]\n\n“I thought we had more time,” she said.\n\n{WHISPER}\n\n'
                       '“Try again!” she shouted.\n\n“Perhaps tomorrow,” she said through tears.\n\n'
                       '“A fine mess,” she said with a smile.\n\n“You call that a plan?” she said [laugh].')
    assert not result['issues'] and result['performance']['candidates']==7
    assert performance.bind(result,IDENTITY,VOICE)
    acted=[u for u in result['utterances'] if 'performance' in u]
    assert [u['performance']['recipe'] for u in acted]==['heading','sigh','whisper','forceful','tearful','amused','laugh']
    assert acted.pop(0)['text']=='Scene'
    assert acted[0]['text']=='“I thought we had more time,” she said.'
    assert performance.request(acted[0])[0]=='(sigh) '+acted[0]['text']
    assert performance.request(acted[-1])[0]==acted[-1]['text']+' (laugh)'
    assert '[sigh]' in {row['source'] for row in result['decisions']}
    assert acted[1]['text']==WHISPER and 'whisper' in performance.request(acted[1])[1]['instruction'].lower()
    assert not performance.bind(result,IDENTITY,VOICE)


@pytest.mark.parametrize('text',[
    'She sighed and laughed as the train pulled away.',
    'Write the value `[laugh]` next to the pressure reading.',
    '[sigh] The reflex can start with a short pause.',
    '“I wrote [laugh] in the margin,” she said.',
    '“Go now,” she whispered. “I will,” he replied.',
    '“Go now,” she whispered. He opened the door. He shut it again.',
    'WOULD YOU PLEASE WAIT!!!',
])
def test_literal_mentions_and_mixed_requests_stay_plain(tmp_path,text):
    result=plan_source(tmp_path,text)
    assert result['performance']['candidates']==0


def test_other_model_recognized_markers_use_an_ordinary_reading(tmp_path):
    result=plan_source(tmp_path,'He wrote [pause] beside (groan), then drew a square.')
    words=result['utterances'][0]['text']
    assert 'pause' in words and 'open parenthesis groan close parenthesis' in words
    assert '[pause]' not in words and result['performance']['candidates']==0


@pytest.mark.parametrize('text',[
    '“Keep close,” Ada whispered, “until the door opens.”',
    '“Keep close,” whispered Dr. Hale, “until the door opens.”',
    '‘Keep close,’ Ada whispered, ‘until the door opens.’',
    "'Don't move,' Ada whispered, 'until the door opens.'",
])
def test_interrupted_dialogue_keeps_both_spans_and_attribution_in_one_request(tmp_path,text):
    plan=plan_source(tmp_path,text)
    assert performance.bind(plan,IDENTITY,VOICE)
    unit,=plan['utterances']
    assert performance.request(unit)[0]==plan_source(tmp_path,text,'off')['utterances'][0]['text']
    active=unit['performance']
    assert active['recipe']=='whisper' and 'continuing turn' in active['instruction']
    evidence,=active['evidence']
    assert evidence['form']=='interrupted_quotation'
    assert [text[start:end] for start,end in evidence['quotation_spans']][1][1:-1]=='until the door opens.'


@pytest.mark.parametrize(('text','recipe'),[
    ('“Bring me the brass key,” Ada said, stressing the word “brass”.','contrastive_emphasis'),
    ('“We can wait. We can still turn back. We have to go now,” Ada said, beginning softly and growing louder.','growing_force'),
    ('“He can hear us. Wait until he leaves,” Ada said in a voice that grew quieter.','fading_force'),
])
def test_authored_focus_and_progression_preserve_the_complete_line(tmp_path,text,recipe):
    plan=plan_source(tmp_path,text)
    performance.bind(plan,IDENTITY,VOICE)
    unit,=plan['utterances']
    active=unit['performance']
    assert active['recipe']==recipe and unit['text']==performance.request(unit)[0]==text
    if recipe=='contrastive_emphasis':
        start,end=active['evidence'][0]['target_span']
        assert text[start:end]=='brass' and 'phrase brass' in active['instruction']


@pytest.mark.parametrize('text',[
    '“Bring the brass key and the brass lock,” Ada said, stressing “brass”.',
    '“Bring the wooden key,” Ada said, stressing “brass”.',
    '“Bring me the brass key,” Ada said in a voice that grew quieter.',
    '“Leave now,” Ada whispered. “I will,” Clara said.',
])
def test_delivery_abstains_when_scope_cannot_be_proven(tmp_path,text):
    assert plan_source(tmp_path,text)['performance']['candidates']==0


def test_delivery_carries_once_only_for_an_explicit_named_continuation(tmp_path):
    first='“We cannot stay,” Ada whispered.'
    second='“Wait near the door,” Ada continued.'
    third='“I will tell you when,” Ada continued.'
    other='“Read the letter first,” Clara continued.'
    plan=plan_source(tmp_path,'\n\n'.join((first,second,third,other)))
    performance.bind(plan,IDENTITY,VOICE)
    start,carry,unqualified,other_speaker=plan['utterances']
    assert carry['performance']['anchor']==start['performance']['id']
    evidence,=carry['performance']['evidence']
    assert evidence['kind']=='continued_delivery' and evidence['previous_block']==start['sources'][0]
    assert second[slice(*evidence['span'])]=='continued'
    assert first[slice(*evidence['previous_attribution_span'])]=='whispered'
    assert performance.request(carry)[0]==second
    assert all('performance_candidate' not in u for u in (unqualified,other_speaker))


def test_new_quoted_sentence_can_be_a_reply_instead_of_a_continuation():
    source='“Your tea will be here quite soon,” Mr. Hale replied, “I can wait.”'
    assert performance.dialogue_attribution(source,'replied') is None


@pytest.mark.parametrize(('text','recipe','form'),[
    ('“Stay here,” Ada answered quietly.','murmured',None),
    ('“Read the numbers,” she said slowly.','measured',None),
    ('Rain struck the shutters. “Stay here,” Ada whispered.','whisper','embedded_quotation'),
    ('“Go now,” she whispered. The latch clicked.','whisper','embedded_quotation'),
    ('The speaker banged the table. “Never!” she shouted.','forceful','embedded_quotation'),
    ('“Go now,” she said. Her voice was scarcely above a whisper.','whisper','trailing_voice_description'),
    ('“I can wait,” Ada said. Ada’s words came one at a time.','measured','trailing_voice_description'),
])
def test_embedded_delivery_preserves_narration_and_locates_evidence(tmp_path,text,recipe,form):
    plan=plan_source(tmp_path,text)
    unit,=plan['utterances']
    assert performance.bind(plan,IDENTITY,VOICE)
    candidate=unit['performance']
    evidence,=candidate['evidence']
    assert candidate['recipe']==recipe
    assert performance.request(unit)[0]==unit['text']==text
    start,end=evidence['span']
    assert text[start:end]
    if form:
        assert form in (evidence['kind'],evidence.get('form'))
        (a,b),=evidence['quotation_spans']
        assert text[a:b].startswith('“') and text[a:b].endswith('”')
        assert 'usual reference voice' in candidate['instruction']
    legacy=planner.plan(inspect(tmp_path/'book.md'),{'performance':'experimental'},delivery_context=1)
    assert legacy['performance']['candidates']==0


@pytest.mark.parametrize('text',[
    '“Go now,” she said. His voice was scarcely above a whisper.',
    '“Go now,” Ada said. His voice was scarcely above a whisper.',
    '“Go now,” Ada said. Lee’s voice was barely audible.',
    '“Go now,” she said. Her voice fell to a whisper.',
    '“Go now,” she said. Her voice was hardly quiet.',
    '“Go now,” she said. Her voice was barely audible. “Where?” Ada asked.',
    '“Go now,” she replied suddenly.',
])
def test_after_quote_cues_require_the_delivered_voice_and_same_owner(tmp_path,text):
    assert plan_source(tmp_path,text)['performance']['candidates']==0


def test_recipe_tuning_changes_only_the_affected_render_identity(tmp_path):
    source=tmp_path/'book.md'
    source.write_text(f'{WHISPER}\n\nAn ordinary line remained outside the conversation.')
    doc=inspect(source)
    policy={'performance':'experimental'}
    plain=planner.plan(doc,policy)
    before=planner.plan(doc,policy)
    performance.bind(before,IDENTITY,VOICE)
    tuned=planner.plan(doc,{**policy,'document':{'source_sha256':doc.source_sha256,
        'performance':{'tuning':{'whisper':{'cfg_scale':2,'top_k':48,'temperature':.7}}}}})
    assert performance.bind(tuned,IDENTITY,VOICE,previous={
        unit['performance_candidate']['id']:unit for unit in before['utterances'] if 'performance_candidate' in unit})
    assert [u['text'] for u in tuned['utterances']]==[u['text'] for u in plain['utterances']]
    first,second=tuned['utterances']
    assert performance.request(first)[1]['sampling']=={'top_k':48,'temperature':.7}
    assert performance.request(first)[1]['cfg_scale']==2
    assert performance.request_identity(first)!=performance.request_identity(before['utterances'][0])
    assert performance.request(second)==performance.request(before['utterances'][1])
    with pytest.raises(backend.Error,match='serial'):
        parallel=planner.plan(doc,tuned['policy'])
        performance.bind(parallel,{**IDENTITY,'settings':{'parallel':2}},VOICE)
    ordinary=planner.plan(doc,{**tuned['policy'],'performance':'auto'})
    assert ordinary['performance']['tuning_state']=='active'
    assert ordinary['utterances'][0]['performance_candidate']['tuning']=={
        'cfg_scale':2,'top_k':48,'temperature':.7,
    }
    paused=planner.plan(doc,{**tuned['policy'],'performance':'off'})
    assert paused['performance']['tuning_state']=='inactive'
    assert all('performance_candidate' not in u for u in paused['utterances'])


@pytest.mark.parametrize(('text','recipe','kind'),[
    ('She lowered her voice. “Stay close,” she said.','whisper','same_speaker_setup'),
    ('Mia lowered her voice. “Stay close,” Mia said.','whisper','same_speaker_setup'),
    ('He raised his voice. “Stay beside me,” he said.','forceful','same_speaker_setup'),
    ('Ada blinked back tears. “I kept the letter,” Ada said.','tearful','same_speaker_setup'),
    ('The room was silent. Mia held her breath and watched the door.','quiet_scene','scene_setting'),
    ('“I like your new hat,” he teased affectionately.','affectionate','direct_attribution'),
    ('“The ice thins beyond those stones,” she warned.','warning','direct_attribution'),
    ('“I’ll stay here until you’re ready,” he said reassuringly.','reassuring','direct_attribution'),
    ('“I’ll write again tomorrow,” she said between sobs.','sobbing','direct_attribution'),
    ('“Do leave some for the rest of us,” she said sarcastically.','ironic','direct_attribution'),
])
def test_context_can_direct_intact_standard_prose(tmp_path,text,recipe,kind):
    result=plan_source(tmp_path,text)
    performance.bind(result,IDENTITY,VOICE)
    assert len(result['utterances'])==1
    unit=result['utterances'][0]
    direction=unit['performance']
    assert direction['recipe']==recipe and direction['evidence'][0]['kind']==kind
    synthesis=performance.request(unit)[0]
    assert synthesis.replace('(whispering) ','')==text
    if recipe=='whisper':
        assert synthesis==text.replace('“','“(whispering) ',1)
        assert performance.label_spoken(unit,qa.tokens(text),qa.tokens(text+' whispering'))
    assert 'event' not in direction


@pytest.mark.parametrize('text',[
    'She lowered her voice. “Stay close,” he said.',
    'She never lowered her voice. “Stay close,” she said.',
    'The room was silent. She held her breath. He yelled from the stairs.',
    'She doubted the room was silent. She held her breath and listened.',
    'The empty room was silent yesterday.',
    '“The room was silent. Mia held her breath and listened.',
])
def test_context_requires_same_speaker_or_two_consistent_scene_cues(tmp_path,text):
    assert plan_source(tmp_path,text)['performance']['candidates']==0


@pytest.mark.parametrize(('stage','recipe'),[
    ('groan','groan'), ('sniffle','sniffle'), ('lip smack','lip_smack'),
    ('hiccup','hiccup'), ('sneeze','sneeze'), ('sob','sob'),
])
def test_explicit_event_can_act_while_catalog_mentions_remain_literal(tmp_path,stage,recipe):
    text='“I thought we had more time,” she said.'
    plan=plan_source(tmp_path,f'[{stage}] {text}\n\nThe reference describes ({stage}).')
    assert not plan['issues'] and plan['performance']['candidates']==1
    performance.bind(plan,IDENTITY,VOICE)
    acted,plain=plan['utterances']
    assert acted['text']==text and acted['performance']['recipe']==recipe
    request, options = performance.request(acted)
    assert request==f'({stage}) {text}'
    if recipe=='sneeze':
        assert 'clear consonants' in options['instruction'] and options['cfg_scale']==1
    assert f'open parenthesis {stage} close parenthesis' in plain['text']
    assert 'performance' not in plain


def test_structural_direction_preserves_compiled_speech_and_pauses(tmp_path):
    source='# The Pressure Chamber\n\n| State | Reading |\n| --- | --- |\n| First | 12 kPa |\n\nThe valve is closed.'
    study=plan_source(tmp_path,source,mode='study')
    performance.bind(study,IDENTITY,VOICE)
    plain=plan_source(tmp_path,source)
    ordinary=plan_source(tmp_path,source,'off',mode='study')
    assert [unit['performance']['recipe'] for unit in study['utterances'] if 'performance' in unit]==['heading','technical']
    assert [unit['performance_candidate']['recipe'] for unit in plain['utterances']
            if 'performance_candidate' in unit]==['heading']
    assert [(u['text'],u['pause_samples']) for u in study['utterances']]==[
        (u['text'],u['pause_samples']) for u in ordinary['utterances']]


def test_only_the_adjacent_matching_speaker_supplies_prose_context(tmp_path):
    source='She lowered her voice.\n\n“Stay close,” she said.\n\n“Wait here,” she said.'
    result=plan_source(tmp_path,source)
    performance.bind(result,IDENTITY,VOICE)
    lead,quote,after=result['utterances']
    assert 'performance' not in lead and 'performance' not in after
    assert quote['performance']['evidence']==[
        {'kind':'preceding_speaker_setup','block':lead['sources'][0],'span':[0,21],'signal':'lowered_voice'}]
    assert performance.request(quote)[0]=='“(whispering) Stay close,” she said.'
    chapter_break='She lowered her voice.\n\n# A new chapter\n\n“Wait here,” she said.'
    across_chapters=plan_source(tmp_path,chapter_break)
    assert [u['performance_candidate']['recipe'] for u in across_chapters['utterances']
            if 'performance_candidate' in u]==['heading']


@pytest.mark.parametrize(('setup','attribution','recipe','signals'),[
    ('Mia leaned closer. Her words were barely audible.', 'Mia said.', 'whisper', ['proximity','low_audibility']),
    ('Her eyes filled with tears. Her voice broke.', 'she said.', 'tearful', ['physical_tears','breaking_voice']),
    ('Mia’s voice dropped to a whisper.', 'Mia said.', 'whisper', ['lowered_voice']),
    ('Lee was still out of breath.', 'Lee said.', 'breathless', ['breath_between_words']),
    ('Mia leaned closer.', 'Mia said.', None, []),
    ('Mia leaned closer. Mia bent closer.', 'Mia said.', None, []),
    ('Mia leaned closer. His voice was barely audible.', 'Jo said.', None, []),
    ('Mia leaned closer. Her voice was never barely audible.', 'Mia said.', None, []),
    ('Mia lowered her voice. Mia raised her voice.', 'Mia said.', None, []),
    ('Mia lowered her voice. The door opened.', 'Mia said.', None, []),
])
def test_dialogue_context_uses_precise_local_cues(tmp_path,setup,attribution,recipe,signals):
    text=f'{setup} “Stay here,” {attribution}'
    result=plan_source(tmp_path,text)
    unit, = result['utterances']
    assert unit['text']==text
    candidate=unit.get('performance_candidate',{})
    assert candidate.get('recipe')==recipe
    assert [evidence['signal'] for evidence in candidate.get('evidence',[]) if 'signal' in evidence]==signals
    if recipe:
        assert candidate['instruction'].startswith('Read the opening narration in the usual reference voice.')
        assert all(text[start:end] in setup for start,end in (e['span'] for e in candidate['evidence']))


def test_contextual_dialogue_policy_leaves_direct_attribution_and_scene_tone_available(tmp_path):
    source=tmp_path/'book.md'
    source.write_text('Mia leaned closer. Her words were barely audible. “Stay here,” Mia said.\n\n'
                      'Mia lowered her voice. “Wait for me,” Mia said.\n\n'+WHISPER)
    book=inspect(source)
    for preference,count in (('off',1),('explicit',2),('corroborated',3)):
        policy={'performance':'experimental','document':{'source_sha256':book.source_sha256,
                'performance':{'contextual_dialogue':preference}}}
        compiled=planner.plan(book,policy)
        assert compiled['performance']['candidates']==count


def test_verb_first_and_other_reporting_verbs_are_auditionable(tmp_path):
    text='“Stay here,” whispered Emma.\n\nEmma lowered her voice. “Follow me,” she answered.'
    result=plan_source(tmp_path,text)
    whisper,_=result['utterances']
    assert whisper['performance_candidate']['evidence'][0]['form']=='verb_first'
    assert whisper['performance_candidate']['contextual']
    result=plan_source(tmp_path,'Emma lowered her voice. “Follow me,” Emma replied.')
    assert result['utterances'][0]['performance_candidate']['recipe']=='whisper'
    result=plan_source(tmp_path,'[sigh] “Follow me,” said Emma.')
    assert result['utterances'][0]['performance_candidate']['contextual']


def test_multiword_authored_cue_and_written_laughter_are_handled_separately(tmp_path):
    result=plan_source(tmp_path,'[clears throat] “I have a few words to say,” she said.')
    performance.bind(result,IDENTITY,VOICE)
    unit=result['utterances'][0]
    assert performance.request(unit)[0].startswith('(clears throat) ')
    assert performance.label_spoken(unit,qa.tokens(unit['text']),qa.tokens('clears throat '+unit['text']))
    result=plan_source(tmp_path,'[laugh] “Heh, you had a plan,” he said.')
    performance.bind(result,IDENTITY,VOICE)
    unit=result['utterances'][0]
    text, options=performance.request(unit)
    assert text.startswith('(chuckle) ')
    assert 'one short natural chuckle' in options['instruction']
    assert unit['performance']['event']['kind']=='laugh'
    valid='Heh, Heh, you had a plan, he said.'
    assert performance.lexical_observation(unit,valid)=='Heh, you had a plan, he said.'
    missing='Heh, Heh, you had a plan.'
    assert performance.lexical_observation(unit,missing)==missing
    literal='Laugh. Heh, you had a plan, he said.'
    assert performance.label_spoken(unit,qa.tokens(unit['text']),qa.tokens(literal))
    assert not performance.extra_event_words(unit,performance.lexical_observation(unit,valid))
    nigel_laugh='Mm-hmm. Heh, you had a plan, he said.'
    assert performance.lexical_observation(unit,nigel_laugh)=='Heh, you had a plan, he said.'
    assert not performance.extra_event_words(unit,performance.lexical_observation(unit,nigel_laugh))
    assert performance.extra_event_words(unit,'I suppose. Heh, you had a plan, he said.')==('i','suppose')
    assert not performance.extra_event_words(unit,'Heh, you surely had a plan, he said.')
    misplaced='Heh, you had a plan, he said. Mm-hmm.'
    assert performance.lexical_observation(unit,misplaced)==misplaced
    assert performance.lexical_observation(unit,'Mm-hmm. Heh, you had a plan.')=='Mm-hmm. Heh, you had a plan.'


def test_off_and_source_bound_disable(tmp_path):
    source=tmp_path/'book.md'
    source.write_text(SIGH)
    doc=inspect(source)
    assert not any('performance_candidate' in u for u in planner.plan(doc,{'performance':'off'})['utterances'])
    policy={'performance':'experimental','document':{'source_sha256':doc.source_sha256,
             'performance':{'disabled_blocks':[doc.blocks[0].id]}}}
    assert planner.plan(doc,policy)['performance']['candidates']==0


def test_experimental_gentle_entrances_stay_with_their_quiet_join(tmp_path):
    text=(f'{WHISPER}\n\nThe room was quiet.\n\n# Later\n\n{SIGH}\n\n'
          'The sun set.\n\n# Morning\n\nThe sun rose.')
    result=plan_source(tmp_path,text)
    ordinary=plan_source(tmp_path,text,'off')
    rows=result['utterances']
    assert [(u['text'],u['pause_samples']) for u in rows]==[
        (u['text'],u['pause_samples']) for u in ordinary['utterances']]
    assert performance.bind(result,IDENTITY,VOICE)
    assert [u['text'] for u in rows if u.get('performance',{}).get('recipe')=='gentle_entry']==[
        'The room was quiet.','Later']
    const=next(u['performance']['id'] for u in rows if u.get('performance',{}).get('recipe')=='whisper')
    suppressed=plan_source(tmp_path,text)
    performance.bind(suppressed,IDENTITY,VOICE,fallbacks=[{'candidate':const}])
    assert next(u for u in suppressed['utterances'] if u['text']=='The room was quiet.')['performance_state']=='anchor_neutral'
    automatic=plan_source(tmp_path,text,'auto')
    performance.bind(automatic,IDENTITY,VOICE)
    assert any(u.get('performance_candidate',{}).get('recipe')=='gentle_entry' for u in automatic['utterances'])
    assert not any(u.get('performance') for u in automatic['utterances'])


def test_tearful_register_instruction_is_specific_to_cave(tmp_path):
    text='“I kept your letter,” she said through tears.'
    cave=plan_source(tmp_path,text)
    nigel=plan_source(tmp_path,text)
    performance.bind(cave,IDENTITY,{**VOICE,'character':'cave-johnson'})
    performance.bind(nigel,IDENTITY,{**VOICE,'character':'nigel-thornberry'})
    a,b=(performance.request(p['utterances'][0]) for p in (cave,nigel))
    assert a[0]==b[0]==text
    assert a[1]['instruction']==b[1]['instruction']+' Keep the reference narrator’s usual register and timbre throughout.'


def test_auto_requires_a_matching_profile_qualification_and_keeps_neutral_clip_identity(tmp_path,monkeypatch):
    registry=loadjson(performance.RULES_FILE)
    registry['qualified'].append({'profile':'cpu-q8','runtime':'fixture','archive_sha256':'f'*64,
                                  'model_sha256':'a'*64,'platform':sys.platform,'recipe':'sigh','placement':'before'})
    private=tmp_path/'registry.json'
    writejson(private,registry)
    monkeypatch.setattr(performance,'RULES_FILE',private)
    text=SIGH+'\n\nThe room was quiet.'
    known=plan_source(tmp_path,text,'auto')
    other=plan_source(tmp_path,text,'auto')
    disabled=plan_source(tmp_path,text,'off')
    job={'voice':VOICE,'backend':IDENTITY,'speech_contract':2}
    performance.bind(known,IDENTITY,VOICE)
    performance.bind(other,{**IDENTITY,'model_sha256':'b'*64},VOICE)
    assert known['performance']['active']==1 and other['performance']['active']==0
    directed,neutral=known['utterances']
    original,ordinary=disabled['utterances']
    assert jobs.clip_key(directed,job)!=jobs.clip_key(original,job)
    assert jobs.clip_key(neutral,job)==jobs.clip_key(ordinary,job)
    assert jobs.clip_key(other['utterances'][0],job)==jobs.clip_key(original,job)
    assert jobs.take_selection(directed)!=jobs.take_selection(original)
    trailing=plan_source(tmp_path,'“I thought we had more time,” she said [sigh].','auto')
    assert trailing['performance']['candidates']==1
    performance.bind(trailing,IDENTITY,VOICE)
    assert trailing['performance']['active']==0


@pytest.mark.parametrize('character',['nigel-thornberry','cave-johnson'])
def test_sigh_runs_automatically_and_reports_its_exact_qualification(tmp_path,monkeypatch,character):
    from audiobook.cast import ASSETS

    monkeypatch.setattr(performance.sys,'platform','linux')
    recipe=loadjson(performance.RULES_FILE)['qualified'][0]
    backend_identity={'profile':recipe['profile'],'runtime_revision':recipe['runtime'],
                      'runtime_archive_sha256':recipe['archive_sha256'],'model_sha256':recipe['model_sha256'],
                      'settings':{'device':'cpu'}}
    saved=loadjson(ASSETS/character/'voice.json')
    voice={'reference_sha256':saved['reference_sha256'],'transcript':saved['transcript'].strip()}
    plan=plan_source(tmp_path,SIGH,'auto')
    assert performance.bind(plan,backend_identity,voice)
    assert plan['performance']['active']==1
    assert plan['utterances'][0]['performance_state']=='qualified'
    for engine,reference in (({**backend_identity,'settings':{'device':'cpu','parallel':2}},voice),
                             ({**backend_identity,'settings':{}},voice),
                             ({**backend_identity,'profile':'vulkan-q8'},voice),
                             (backend_identity,{**voice,'transcript':'A different exact transcript.'})):
        plan=plan_source(tmp_path,SIGH,'auto')
        performance.bind(plan,engine,reference)
        assert plan['performance']['active']==1
        assert plan['utterances'][0]['performance_state']=='automatic'


def test_automatic_quiet_scene_and_laugh_keep_reference_qualification_visible(tmp_path,monkeypatch):
    from audiobook.cast import ASSETS

    monkeypatch.setattr(performance.sys,'platform','linux')
    recipe=next(row for row in loadjson(performance.RULES_FILE)['qualified'] if row['recipe']=='quiet_scene')
    backend_identity={'profile':recipe['profile'],'runtime_revision':recipe['runtime'],
                      'runtime_archive_sha256':recipe['archive_sha256'],'model_sha256':recipe['model_sha256']}
    quiet='Everything went still. Behind the curtain, Lila held her breath.'
    for source,character,state in (
        (quiet,'nigel-thornberry','qualified'),
        (quiet,'cave-johnson','automatic'),
        ('No one spoke. Peter dared not move.','nigel-thornberry','automatic'),
        ('[laugh] “It was in my pocket all along,” he said.','nigel-thornberry','qualified'),
        ('[laugh] “It was in my pocket all along,” he said.','cave-johnson','instruction_only'),
        ('“It was in my pocket all along,” he said [laugh].','nigel-thornberry','automatic'),
    ):
        plan=plan_source(tmp_path,source,'auto')
        assert plan['performance']['candidates']==1
        voice=loadjson(ASSETS/character/'voice.json')
        performance.bind(plan,backend_identity,voice)
        assert plan['performance']['active']==1
        assert plan['utterances'][0]['performance_state']==state


def pinned_linux_profile(profile):
    runtime, models, archives=performance.pinned_backends()
    return {'profile':profile,'runtime_revision':runtime,'model_sha256':models[profile],
            'runtime_archive_sha256':archives['linux_x86_64'],'settings':{}}


def test_normal_book_directs_clear_prose_without_scope_leaks(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(performance.sys,'platform','linux')
    s=Store(tmp_path/'state')
    writejson(s.root/'settings.json',pinned_linux_profile('vulkan-f16'))
    source=tmp_path/'book.md'
    source.write_text('\n\n'.join((
        '“Stay by the door,” she whispered, “until the footsteps fade.”',
        'The rain fell steadily.',
        '“Bring the brass key,” Ada said, stressing the word “brass”.',
        '“Wait. We have to go now,” she said, beginning softly and growing louder.',
        'Lee was still out of breath. “I just made it in time,” Lee said.',
        '“He can hear us. Wait until he leaves,” Ada said in a voice that grew quieter.',
        'Her eyes filled with tears. Her voice broke. “I saved your letter,” she said.',
        '“I saved your letter,” she said through tears.',
        '[laugh] “The hat was on the wrong peg,” she said.',
    )))
    a=['--home',str(s.root),'queue',str(source),'--voice','cast:nigel-thornberry','--json']
    assert cli.main(a)==0
    saved=s.get(json.loads(capsys.readouterr().out)['job'])
    assert saved['plan']['performance']['mode']=='auto'
    rows=saved['plan']['utterances']
    assert [u.get('performance',{}).get('recipe') for u in rows]==[
        'whisper','gentle_entry','contrastive_emphasis','growing_force','breathless','fading_force','tearful','tearful','laugh']
    assert 'until the footsteps fade' in performance.request(rows[0])[0]
    assert all(performance.request(u)[0]==u['text'] for u in rows[:-1])
    assert performance.request(rows[-1])[0].startswith('(chuckle) ')
    wrong={**saved['backend'],'runtime_archive_sha256':'0'*64}
    invalid=planner.plan(inspect(source))
    assert performance.bind(invalid,wrong,saved['voice']) and invalid['performance']['active']==0


@pytest.mark.parametrize(('text','recipe','cue'),[
    ('“Stay inside the gate,” Ada replied firmly.','firm','replied firmly'),
    ('“We have plenty of water,” answered Theo reassuringly.','reassuring','reassuringly'),
    ('The bell rang. “We have time,” said Ada calmly. The pony settled.','composed','calmly'),
    ('“Mind the echo,” asked Ada anxiously.','apprehensive','anxiously'),
])
def test_normal_attribution_directs_only_the_owned_sentence(tmp_path,text,recipe,cue):
    source=tmp_path/'book.md'
    source.write_text(text)
    older=planner.plan(inspect(source),delivery_context=3)
    assert 'performance_candidate' not in older['utterances'][0]
    default=planner.plan(inspect(source))
    unit,=default['utterances']
    candidate=unit['performance_candidate']
    assert candidate['recipe']==recipe and candidate['speech']==text
    assert text[slice(*candidate['source']['span'])]==cue
    if 'said Ada' in text:
        evidence,=candidate['evidence']
        assert text[slice(*evidence['reporting_span'])]=='said'
        assert 'around the quotation in the usual reference voice' in candidate['instruction']


@pytest.mark.parametrize(('text','cue','reporting','recipe'),[
    ('“Stay by the door,” she said, her voice barely audible.', 'her voice barely audible', 'said', 'murmured'),
    ('“Stay by the door,” said Ada, her voice barely audible.', 'her voice barely audible', 'said', 'murmured'),
    ('“Use the green key,” said Ada, Ada’s words barely audible.', 'Ada’s words barely audible', 'said', 'murmured'),
    ('The hall was empty. Then she said, in a barely audible voice: “Come this way.”',
     'in a barely audible voice', 'said', 'murmured'),
    ('“Leave enough for the others,” replied the old man, in a barely audible tone.',
     'in a barely audible tone', 'replied', 'murmured'),
    ('The steps creaked. She said, in a whisper: “Someone is moving below us.”',
     'in a whisper', 'said', 'whisper'),
    ('The clerk drew the curtain aside and said in a whisper, “They have found us.”',
     'in a whisper', 'said', 'whisper'),
    ('Before anyone left, he said, in a whisper to Ada, “Leave the gate open.”',
     'in a whisper', 'said', 'whisper'),
])
def test_default_attached_voice_description_keeps_the_complete_turn(tmp_path,text,cue,reporting,recipe):
    source=tmp_path/'book.md'
    source.write_text(text)
    document=inspect(source)
    assert all('performance_candidate' not in u for u in planner.plan(document,delivery_context=4)['utterances'])
    unit,=planner.plan(document)['utterances']
    candidate=unit['performance_candidate']
    assert candidate['recipe']==recipe
    assert candidate['speech']==candidate['synthesis']==text
    evidence,=candidate['evidence']
    assert evidence['kind']=='attached_voice_description'
    assert text[slice(*evidence['span'])]==cue
    assert text[slice(*evidence['reporting_span'])]==reporting
    quote,=evidence['quotation_spans']
    assert text[slice(*quote)].startswith('“')
    assert text[slice(*quote)].endswith('”')


@pytest.mark.parametrize(('text','actor','recipe'),[
    ('“Wait until the light goes out,” whispered the night watchman.', 'the night watchman', 'whisper'),
    ('The night watchman whispered, “Wait until the light goes out.”', 'The night watchman', 'whisper'),
    ('“Hold it steady,” replied the doctor firmly.', 'the doctor', 'firm'),
    ('The door closed. “Stay here,” her brother answered quietly. The steps creaked.', 'her brother', 'murmured'),
])
def test_default_delivery_owns_a_locally_described_reporter(tmp_path,text,actor,recipe):
    source=tmp_path/'book.md'
    source.write_text(text)
    document=inspect(source)
    assert planner.plan(document,delivery_context=7)['performance']['candidates']==0
    unit,=planner.plan(document)['utterances']
    cue=unit['performance_candidate']
    assert cue['recipe']==recipe and cue['speech']==text
    evidence,=cue['evidence']
    assert text[slice(*evidence['speaker_span'])]==actor
    assert text[slice(*evidence['quotation_spans'][0])].startswith('“')


@pytest.mark.parametrize('text',[
    '“Stay there,” the guard never whispered.',
    '“Stay there,” whispered the guard and walked away.',
    '“Stay there,” asked the Mock Turtle a little anxiously.',
    '“Stay there,” replied the doctor very firmly.',
    '“Stay there,” the guard said. “What?” her brother asked quietly.',
])
def test_role_parser_preserves_reporting_modifiers_and_turn_boundaries(tmp_path,text):
    source=tmp_path/'book.md'
    source.write_text(text)
    assert planner.plan(inspect(source))['performance']['candidates']==0


@pytest.mark.parametrize('text',[
    '“Look outside,” he said, her voice barely audible.',
    '“Look outside,” Ada said, Lee’s voice barely audible.',
    'Lee leaned toward Ada. “Look outside,” Ada said, her voice barely audible.',
    '“Look outside,” she said, her voice no longer barely audible.',
    '“Look outside,” she said in a low, even voice.',
    'She said in a barely audible voice: “Look outside.” “Where?” Ada asked.',
    'He denied that Ada came back and said in a whisper: “Look outside.”',
])
def test_attached_voice_direction_requires_owned_unambiguous_description(tmp_path,text):
    source=tmp_path/'book.md'
    source.write_text(text)
    plan=planner.plan(inspect(source))
    assert all('performance_candidate' not in u for u in plan['utterances'])


def test_inline_authored_sigh_is_one_automatic_request(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(performance.sys,'platform','linux')
    store=Store(tmp_path/'state')
    writejson(store.root/'settings.json',pinned_linux_profile('vulkan-q8'))
    source=tmp_path/'book.md'
    source.write_text('“The last box is empty. [sigh] At least we found the key,” she said.')
    result=planner.plan(inspect(source),delivery_context=5)
    assert result['performance']['candidates']==0
    command=['--home',str(store.root),'queue',str(source),'--voice','cast:nigel-thornberry','--json']
    assert cli.main(command)==0
    saved=store.get(json.loads(capsys.readouterr().out)['job'])
    unit,=saved['plan']['utterances']
    assert unit['text']=='“The last box is empty. At least we found the key,” she said.'
    active=unit['performance']
    assert active['event']['position']=='within'
    assert active['event']['word_offset']==5
    assert source.read_text()[slice(*active['source']['span'])]=='[sigh]'
    spoken,opts=performance.request(unit)
    assert spoken=='“The last box is empty. (sigh) At least we found the key,” she said.'
    assert 'between the two quoted sentences' in opts['instruction']
    correct='The last box is empty. At least we found the key, she said.'
    heard='The last box is empty. [sighing] At least we found the key, she said.'
    assert performance.lexical_observation(unit,heard)==correct
    for uncertain in ('[sigh] '+correct,heard.replace('the key','it'),
                      heard.replace('[sighing]','extra speech')):
        assert performance.lexical_observation(unit,uncertain)==uncertain


def test_inline_cough_uses_the_same_continuous_request_at_the_new_context_version(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(performance.sys,'platform','linux')
    store=Store(tmp_path/'state')
    writejson(store.root/'settings.json',pinned_linux_profile('vulkan-q8'))
    source=tmp_path/'book.md'
    source.write_text('“The powder gets everywhere. [cough] We need to open a window,” she said.')
    older=planner.plan(inspect(source),delivery_context=6)
    assert older['performance']['candidates']==0
    command=['--home',str(store.root),'queue',str(source),'--voice','cast:nigel-thornberry','--json']
    assert cli.main(command)==0
    saved=store.get(json.loads(capsys.readouterr().out)['job'])
    unit,=saved['plan']['utterances']
    assert unit['text']==source.read_text().replace('[cough] ','')
    actual,opts=performance.request(unit)
    assert actual==source.read_text().replace('[cough]','(cough)')
    assert opts['instruction']==loadjson(performance.RULES_FILE)['events']['cough']['inline_instruction']
    event=unit['performance']['event']
    assert event['position']=='within' and event['word_offset']==4
    heard='The powder gets everywhere. [coughing] We need to open a window, she said.'
    assert performance.lexical_observation(unit,heard)==heard.replace('[coughing] ','')
    assert performance.lexical_observation(unit,heard.replace('open a window','open it'))==heard.replace('open a window','open it')


@pytest.mark.parametrize('text',[
    '“The last box is [sigh] empty,” she said.',
    '“Ask Dr. [sigh] Allen about it,” she said.',
    '“Nobody stayed. [sigh] We left together.”',
    '“Nobody stayed. [sigh] We left together,” she said. “I did,” Ada said.',
    '“Nobody stayed. [sigh] We left together. [sigh] The gate shut,” she said.',
])
def test_inline_sound_requires_one_owned_sentence_break(tmp_path,text):
    source=tmp_path/'book.md'
    source.write_text(text)
    assert all('performance_candidate' not in u for u in planner.plan(inspect(source))['utterances'])


@pytest.mark.parametrize(('first_line','second_line','prior_version','cue'),[
    ('“The last box is empty.','[sigh] At least we found the key,” she said.',5,'[sigh]'),
    ('“The powder gets everywhere.','[cough] We need to open a window,” she said.',6,'[cough]'),
    ('“The powder gets everywhere.','[cough] Open the top window,” the caretaker said.',7,'[cough]'),
])
def test_inline_event_keeps_both_native_pdf_fragments(tmp_path,first_line,second_line,prior_version,cue):
    canvas=pytest.importorskip('reportlab.pdfgen.canvas')
    source=tmp_path/'story.pdf'
    pdf=canvas.Canvas(str(source))
    pdf.setFont('Helvetica',16)
    pdf.drawString(72,710,first_line)
    pdf.drawString(72,688,second_line)
    pdf.save()
    document=inspect(source)
    assert len(planner.plan(document,delivery_context=prior_version)['utterances'])==2
    unit,=planner.plan(document)['utterances']
    event=unit['performance_candidate']
    first,second=unit['sources']
    evidence,=event['evidence']
    assert [part['block'] for part in evidence['quotation_parts']]==[first,second]
    assert event['source']=={'block':second,'span':[0,len(cue)]}
    assert event['speech']==first_line+' '+second_line.removeprefix(cue+' ')


def test_book_policy_adds_an_exact_attribution_without_changing_other_requests(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(performance.sys,'platform','linux')
    store=Store(tmp_path/'state')
    writejson(store.root/'settings.json',pinned_linux_profile('vulkan-q8'))
    source=tmp_path/'book.md'
    source.write_text('“Leave the field,” Ada replied carefully.\n\n'
                      '“Come by the gate,” replied Ada carefully.\n\n'
                      '“The bell will ring,” said Ada firmly and loudly.\n\n'
                      'Ada walked carefully beside the fence.\n\n'
                      '“I know,” Ada answered quickly.')
    policy=tmp_path/'policy.json'
    writejson(policy,{'source_sha256':inspect(source).source_sha256,
                      'performance':{'attribution_cues':{'replied carefully':'measured'}}})
    command=['--home',str(store.root),'queue',str(source),'--voice','cast:cave-johnson','--json']
    assert cli.main(command)==0
    baseline=store.get(json.loads(capsys.readouterr().out)['job'])
    assert cli.main([*command,'--document-policy',str(policy)])==0
    directed=store.get(json.loads(capsys.readouterr().out)['job'])
    before=baseline['plan']['utterances']
    after=directed['plan']['utterances']
    assert [row.get('performance',{}).get('recipe') for row in after]==['measured','measured',None,None,None]
    assert all(performance.request(left)[0]==performance.request(right)[0] for left,right in zip(before,after))
    assert all(jobs.clip_key(left,baseline)==jobs.clip_key(right,directed) for left,right in zip(before[2:],after[2:]))
    assert all(row['performance']['evidence'][0]['policy_cue']=='replied carefully' for row in after[:2])


def test_default_pdf_directs_a_quote_across_native_line_wraps(tmp_path,monkeypatch,capsys):
    from reportlab.pdfgen.canvas import Canvas

    monkeypatch.setattr(performance.sys,'platform','linux')
    store=Store(tmp_path/'state')
    writejson(store.root/'settings.json',pinned_linux_profile('vulkan-q8'))
    source=tmp_path/'book.pdf'
    pdf=Canvas(str(source))
    pdf.setFont('Helvetica',14)
    pdf.drawString(72,700,'"Stay by the gate until the last')
    pdf.drawString(72,681,'footstep fades," Ada whispered.')
    pdf.save()
    command=['--home',str(store.root),'queue',str(source),'--voice','cast:cave-johnson','--json']
    assert cli.main(command)==0
    job=store.get(json.loads(capsys.readouterr().out)['job'])
    unit,=job['plan']['utterances']
    assert job['plan']['performance']['mode']=='auto'
    assert unit['performance']['recipe']=='whisper'
    assert performance.request(unit)[0]==unit['text']
    blocks={block['id']:block for block in job['document']['blocks']}
    evidence,=unit['performance']['evidence']
    def from_source(part):
        return blocks[part['block']]['source'][slice(*part['span'])]
    assert [from_source(part) for part in evidence['cue_parts']]==['whispered']
    assert [from_source(part) for part in evidence['quotation_parts']]==[
        '"Stay by the gate until the last','footstep fades,"',
    ]
    assert cli.main([*command,'--performance','off'])==0
    plain=store.get(json.loads(capsys.readouterr().out)['job'])['plan']['utterances'][0]
    assert plain['text']==unit['text'] and 'performance' not in plain


def test_unrelated_repair_preserves_legacy_delivery_until_explicit_upgrade(tmp_path,monkeypatch):
    monkeypatch.setattr(performance.sys,'platform','linux')
    s=Store(tmp_path/'state')
    writejson(s.root/'settings.json',pinned_linux_profile('vulkan-q8'))
    source=tmp_path/'book.md'
    source.write_text(f'{WHISPER}\n\nThe room was quiet.')
    old=jobs.create(s,source,'cast:nigel-thornberry')
    job=s.get(old['job'])
    job['plan']=planner.plan(jobs.document(job),automatic_prose=False)
    performance.bind(job['plan'],job['backend'],job['voice'])
    s.save(job)
    assert job['plan']['performance']['active']==0
    repaired=jobs.repair(s,old['job'],retake='u00002')
    assert repaired['performance']['active']==0
    upgraded=jobs.repair(s,old['job'],performance_mode='auto')
    assert upgraded['performance']['active']==2


def test_saved_041_plan_binds_its_original_directions_after_setup(tmp_path,monkeypatch):
    monkeypatch.setattr(performance.sys,'platform','linux')
    store=Store(tmp_path/'state')
    source=tmp_path/'book.md'
    source.write_text(f'{WHISPER}\n\n“Set down the lantern,” she replied quietly.')
    created=jobs.create(store,source,'cast:nigel-thornberry')
    job=store.get(created['job'])
    legacy=planner.plan(jobs.document(job),delivery_context=1)
    assert not legacy['performance']['bound']
    legacy['performance']['registry_sha256']='c46c5f9b93db2aac04799dc9343e6da9570f7ada67aa8934dc20b4cb42d06bab'
    job['plan']=legacy
    store.save(job)
    writejson(store.root/'settings.json',pinned_linux_profile('vulkan-q8'))
    current=store.get(created['job'])
    assert jobs.bind_speech(store,current)
    assert [unit.get('performance',{}).get('recipe') for unit in current['plan']['utterances']]==[
        'whisper',None,
    ]


def test_native_transport_forwards_clone_instruction_and_hashes_exact_fields(tmp_path):
    received=[]
    reference=tmp_path/'voice.wav'
    reference.write_bytes(b'fixture waveform identity')
    voice={'reference':str(reference),'reference_sha256':digest(reference.read_bytes()),'transcript':'Fixture.'}

    class Client:
        @contextmanager
        def stream(self,method,endpoint,*,data,files):
            received.append((dict(data),files['ref_audio'][1].read()))
            yield self
        status_code=200
        headers={'x-sample-rate':str(RATE),'x-sample-format':'s16le'}
        def iter_bytes(self):return iter([TONE])

    engine=backend.Engine.__new__(backend.Engine)
    engine.identity=IDENTITY
    engine.client=Client()
    engine.read_log=lambda:''
    engine.verify_device=lambda _: 'fixture'
    _,base=engine.speech('A short control.',voice)
    _,directed=engine.speech('A short control.',voice,instruction='Use a quiet voice.')
    assert 'instruction' not in received[0][0] and received[1][0]['instruction']=='Use a quiet voice.'
    assert received[0][1]==received[1][1]==reference.read_bytes()
    assert received[0][0]['ref_text']==received[1][0]['ref_text']==voice['transcript']
    assert received[1][0]['cfg_scale']=='1' and directed['request_sha256']!=base['request_sha256']
    assert directed['request_sha256']==key(directed['request'])


class FakeEngine:
    def __init__(self,*args):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def speech(self,text,voice,**options):
        data,receipt=backend.validate_pcm(TONE)
        receipt['control_request']={'text':text,**options}
        return data,receipt


def observation(store,job,utterances,python,model,*,prefix='Sigh. '):
    rows=[]
    for unit in utterances:
        pcm=jobs.valid_clip(store,jobs.clip_key(unit,job))
        text=unit['text']
        if 'performance' in unit:text=prefix+text
        rows.append({'utterance':unit['id'],'audio_seconds':pcm['seconds'],'audio_samples':pcm['samples'],
                     'pcm_sha256':pcm['sha256'],'text':text,'segments':[{'text':text,'start':0,'end':pcm['seconds']}]})
    return rows,{'model_files':{}},{}


def queued(tmp_path,passage=SIGH):
    source=tmp_path/'book.md'
    source.write_text(passage+'\n\nThe cabin grew quiet.')
    store=Store(tmp_path/'state')
    output=tmp_path/'books'
    output.mkdir()
    result=jobs.create(store,source,policy={'performance':'experimental'},output=output)
    job=store.get(result['job'])
    job['voice']=VOICE
    store.save(job)
    writejson(store.root/'settings.json',IDENTITY)
    return store,job


def test_single_observer_cannot_remove_whisper_direction(tmp_path,monkeypatch):
    store,job=queued(tmp_path,WHISPER)
    monkeypatch.setattr(jobs,'Engine',FakeEngine)
    monkeypatch.setattr(pipeline.qa_cache,'observer_identity',lambda *args:{'model_files':{}})

    def check(store,job,utterances,python,model):
        rows,identity,checkpoint=observation(store,job,utterances,python,model)
        for unit,row in zip(utterances,rows):
            text='Keep your voice down.' if unit.get('performance',{}).get('control_labels') else unit['text']
            row['text']=text
            row['segments'][0]['text']=text
        return rows,identity,checkpoint

    monkeypatch.setattr(pipeline.qa_cache,'observe',check)
    result=pipeline.run(store,job['id'],Path('/fixture'),Path('/fixture'))
    assert result['status']=='needs_review' and result['performance']['fallbacks']==0
    assert result['performance']['instruction_only_retries']==0
    current=store.get(job['id'])
    recovered=current['plan']['utterances'][0]
    assert recovered['performance_state']=='experimental' and current['revision']==1
    spoken,options=performance.request(recovered)
    assert spoken.startswith('“(whispering) ') and options['instruction']
    assert jobs.valid_clip(store,jobs.clip_key(recovered,current))


@pytest.mark.parametrize(('prefix','status','assessment'),[
    ('Sigh. ','ready','ambiguous_vocalization'),
    ('Ah. ','ready','ambiguous_vocalization'),
    ('I suppose. ','needs_review',None),
])
def test_pipeline_preserves_directed_takes_at_authored_sound(tmp_path,monkeypatch,prefix,status,assessment):
    store,job=queued(tmp_path)
    monkeypatch.setattr(jobs,'Engine',FakeEngine)
    monkeypatch.setattr(pipeline.qa_cache,'observer_identity',lambda *args:{'model_files':{}})
    monkeypatch.setattr(pipeline.qa_cache,'observe',lambda *args:observation(*args,prefix=prefix))
    result=pipeline.run(store,job['id'],Path('/fixture'),Path('/fixture'))
    current=store.get(job['id'])
    assert result['status']==status
    assert not current.get('performance_fallbacks') and current['revision']==1
    unit=current['plan']['utterances'][0]
    assert unit['performance_state']=='experimental' and performance.request(unit)[0].startswith('(sigh) ')
    previous=loadjson(store.path(job)/'qa-r1.json')['rows'][0]
    assert previous['expected_intentional_event']=={'kind':'sigh','position':'before'}
    assert previous.get('authored_sound_observation',{}).get('status')==assessment
    assert bool(previous['flags'])==(status=='needs_review')
    if status=='ready':
        companion=loadjson(Path(result['output'])/'companion.json')
        assert companion['segments'][0]['text']==unit['text']
        assert companion['decisions'][0]['source']==SIGH
    jobs.cleanup(store,apply=True)
    assert jobs.valid_clip(store,jobs.clip_key(unit,current))


def test_queue_binds_preview_and_render_to_same_directed_request(tmp_path,monkeypatch,capsys):
    store,job=queued(tmp_path)
    monkeypatch.setattr(jobs,'Engine',FakeEngine)
    monkeypatch.setattr(cli,'Engine',FakeEngine)
    request=job['plan']['utterances'][0]['id']
    assert cli.main(['--home',str(store.root),'preview',job['id'],'--utterance',request,'--json'])==0
    preview=json.loads(capsys.readouterr().out)['receipt']['control_request']
    assert preview['text'].startswith('(sigh) ') and store.get(job['id'])['backend'] is None
    jobs.render(store,job['id'],max_new_takes=1)
    saved=store.get(job['id'])
    clip=jobs.valid_clip(store,jobs.clip_key(saved['plan']['utterances'][0],saved))
    assert clip['control_request']==preview


def test_transcribe_compares_exact_take_without_changing_job(tmp_path,monkeypatch,capsys):
    from audiobook import asr_diagnostic, asr_fluid

    store, job = queued(tmp_path,WHISPER)
    monkeypatch.setattr(jobs,'Engine',FakeEngine)
    jobs.render(store,job['id'],max_new_takes=1)
    before = store.get(job['id'])
    unit = before['plan']['utterances'][0]
    committed = jobs.valid_clip(store,jobs.clip_key(unit,before))
    monkeypatch.setattr(asr_diagnostic,'observer',lambda *args: (Path('python'),Path('base.en'),Path('small.en')))
    monkeypatch.setattr(pipeline.qa_cache,'observer_identity',
                        lambda python,model,worker:{'model_files':{'model.bin':str(model)}})
    order = []

    def decode(python,model,clips):
        order.append(model.name)
        request, = clips
        assert set(request)=={'utterance','path'}
        assert digest(Path(request['path']).read_bytes())==committed['sha256']
        text = 'Keep your voice down, he whispered.' if model.name=='base.en' else 'Keep your voice down.'
        return [{'text':text,'seconds':1,'segments':[{'start':0,'end':1,'text':text,'avg_logprob':-.4}]}]

    monkeypatch.setattr(pipeline.qa_cache,'recognize',decode)
    monkeypatch.setattr(asr_fluid,'configured',lambda *args:{'profile':'fluid-int8'})

    def challenger(profile,path,receipt):
        order.append(profile['profile'])
        assert digest(path.read_bytes())==receipt['sha256']==committed['sha256']
        return {'text':'Keep your voice down, he whispered.','status':'observed','timing_kind':'unavailable'}

    monkeypatch.setattr(asr_fluid,'observe',challenger)
    assert cli.main(['--home',str(store.root),'transcribe',job['id'],'--utterance',unit['id'],'--json'])==0
    result = json.loads(capsys.readouterr().out)
    assert result['pcm_sha256']==committed['sha256'] and result['prompt'] is None
    assert result['comparison_status']=='recognizers_disagree'
    assert result['disagreements'][0]['primary']==['he','whispered']
    assert result['challenger']['word_edits']==0
    assert order==['base.en','small.en','fluid-int8']
    assert store.get(job['id'])==before


def test_diagnostic_reports_exact_silence_without_interpreting_performance(tmp_path):
    from audiobook.asr_diagnostic import digital_silence

    path = tmp_path/'take.pcm'
    path.write_bytes(b'\x01\x00'*RATE + bytes(RATE*2) + b'\x01\x00'*RATE)
    assert digital_silence(path)==[{'start_seconds':1.0,'end_seconds':2.0}]


def test_optional_diagnostic_is_cached_and_cannot_vote_on_publication(tmp_path,monkeypatch):
    from audiobook import asr_fluid

    store, job = queued(tmp_path)
    monkeypatch.setattr(jobs,'Engine',FakeEngine)
    jobs.render(store,job['id'],max_new_takes=2)
    real_observer = lambda *args: observation(*args,prefix='I suppose. ')
    monkeypatch.setattr(pipeline.qa_cache,'observe',real_observer)
    monkeypatch.setattr(asr_fluid,'configured',lambda *args:{'model':'fixture'})
    monkeypatch.setattr(asr_fluid,'check_assets',lambda path:None)
    monkeypatch.setattr(asr_fluid,'observer_identity',lambda:('ffmpeg','fixture'))
    requests = []

    def decode(profile,path,receipt):
        requests.append(receipt['sha256'])
        return {'observer_sha256':'fixture','pcm_sha256':receipt['sha256'],'processed_samples':receipt['samples'],
                'text':'I thought we had more time, she said.','provider':'CPUExecutionProvider'}

    monkeypatch.setattr(asr_fluid,'observe',decode)
    for _ in range(2):
        qa.check(store,job['id'],Path('python'),Path('model'),all_clips=True,require_review=True)
    report = loadjson(store.path(job)/'qa-r1.json')
    assert report['rows'][0]['challenger_observation']['word_edits']==0
    assert report['speech_diagnostic']['reused']==len(requests)==1
    assert store.get(job['id'])['status']=='needs_review'


def test_interrupted_directed_take_is_preserved_after_resume(tmp_path,monkeypatch):
    store,job=queued(tmp_path)
    code='''from pathlib import Path
import sys
from audiobook import jobs,pipeline
from audiobook.core import Store
from test_performance import FakeEngine,observation
jobs.Engine=FakeEngine
pipeline.qa_cache.observe=observation
pipeline.qa_cache.observer_identity=lambda *args:{'model_files':{}}
pipeline.run(Store(Path(sys.argv[1])),sys.argv[2],Path('/fixture'),Path('/fixture'))
'''
    tests=str(Path(__file__).parent)
    child=subprocess.run([sys.executable,'-c',code,str(store.root),job['id']],
                         env={**os.environ,'PYTHONPATH':tests,'AUDIOBOOK_FAULT':'after_clip_rename'},
                         capture_output=True,timeout=30)
    assert child.returncode==86,child.stderr.decode()
    directed=store.get(job['id'])
    clip=jobs.clip_key(directed['plan']['utterances'][0],directed)
    first=jobs.valid_clip(store,clip)
    assert first and first['control_request']['text'].startswith('(sigh)')
    resumed=Store(store.root)
    saved=resumed.get(job['id'])
    assert saved['revision']==1
    monkeypatch.setattr(jobs,'Engine',FakeEngine)
    monkeypatch.setattr(pipeline.qa_cache,'observer_identity',lambda *args:{'model_files':{}})
    monkeypatch.setattr(pipeline.qa_cache,'observe',observation)
    assert pipeline.run(resumed,job['id'],Path('/fixture'),Path('/fixture'))['status']=='ready'
    assert not resumed.get(job['id']).get('performance_fallbacks')
    assert jobs.valid_clip(store,clip)==first
