"""Spoken-reference and word-budget regressions, including ambiguous contexts."""
import pytest
from audiobook.abbreviations import expand
from audiobook.core import Block, Document
from audiobook.planner import normalize, plan, split
from audiobook.speech_compare import comparison_tokens


@pytest.mark.parametrize('source,spoken', [
    ('Dr. Henry met Prof. Jones.', 'Doctor Henry met Professor Jones.'),
    ('Mr. Smith thanked Mrs. Brown.', 'Mister Smith thanked Missus Brown.'),
    ('Rev. Green met Capt. Black.', 'Reverend Green met Captain Black.'),
    ('Dr. J. M. Swan.', 'Doctor J. M. Swan.'),
    ('Dr. ].M.A. Swan.', 'Doctor ].M.A. Swan.'),
    ('William Smith, Jr., spoke.', 'William Smith, Junior, spoke.'),
    ('Hunt No. 128; see pp. 43–59 and Fig. 2.', 'Hunt number 128; see pages 43–59 and figure 2.'),
    ('No. Do not restart.', 'No. Do not restart.'),
    ('Prey (e.g., voles), i.e. mammals, etc.', 'Prey (for example, voles), that is mammals, et cetera.'),
    ('11:47 a.m. on Tuesday; 2 p.m. tomorrow.', '11:47 A M on Tuesday; 2 P M tomorrow.'),
    ('12 Lake Dr. and 14 Lake St.', '12 Lake Drive and 14 Lake Street.'),
    ('In Chapter 8 Dr. Henry explains.', 'In Chapter 8 Doctor Henry explains.'),
    ('In Chapter 8 author Dr. Henry explains.', 'In Chapter 8 author Doctor Henry explains.'),
    ('0.08 mg. and 3 cm. per gram.', '0.08 milligrams and 3 centimeters per gram.'),
    ('R. Henry and P. Smith use v2.4.10, 0.08 mg, and U.S. data.',
     'R. Henry and P. Smith use v2.4.10, 0.08 mg, and U.S. data.'),
    ('Read https://host.org/Dr. and Mr.Smith@example.org or e.g.txt.',
     'Read https://host.org/Dr. and Mr.Smith@example.org or e.g.txt.'),
])
def test_contextual_readings(source, spoken):
    assert expand(source)[0] == spoken


def test_aliases_override_default_readings_without_being_reexpanded():
    assert expand('Dr. Henry met Dr. Jones.', {'Dr. Henry': 'Doctor H', 'Dr.': 'Doc'})[0] == 'Doctor H met Doc Jones.'
    assert expand('Dr. Henry.', {'Dr.': 'Dr.'}) == ('Dr. Henry.', [], [])
    assert expand('dr. Henry.', {'Dr.': 'Doc'})[0] == 'Doctor Henry.'


def test_ambiguity_holds_publication_without_changing_original_source():
    source='Visit St. John after Dr.'
    doc=Document('Control','control','source',[Block('b',source,{})])
    p=plan(doc,rule_revision='faithful-5')
    assert p['rule_revision']=='faithful-5'
    assert p['decisions'][0]['source']==source
    assert len(p['issues'])==2
    assert all('pronounce' in issue['repair'] for issue in p['issues'])
    verified=plan(doc,aliases={'St. John':'Saint John','Dr.':'Doctor'},rule_revision='faithful-5')
    assert not verified['issues']
    assert verified['decisions'][0]['source']==source
    assert verified['decisions'][0]['spoken']=='Visit Saint John after Doctor'


def test_normalization_is_auditable_and_legacy_plan_stays_literal():
    source='Dr. Henry consulted Fig. 2.'
    doc=Document('Control','control','source',[Block('b',source,{})])
    new=plan(doc);old=plan(doc,rule_revision='faithful-4')
    assert new['decisions'][0]['source']==old['decisions'][0]['source']==source
    assert new['decisions'][0]['spoken']=='Doctor Henry consulted figure 2.'
    assert old['decisions'][0]['spoken']==source
    assert 'abbreviations' not in old['decisions'][0]
    assert [e['source'] for e in new['decisions'][0]['abbreviations']['readings']]==['Dr.','Fig.']


def test_title_and_initials_stay_with_names_at_forced_word_boundary():
    text=normalize(' '.join(['word']*64)+' Dr. Henry continues his observations.')
    chunks=split(text)
    assert not any(c.endswith('Doctor') for c in chunks)
    assert any('Doctor Henry' in c for c in chunks)
    assert ' '.join(chunks)==text
    initials=split(' '.join(['word']*64)+' J. M. Smith speaks.')
    assert any('J. M. Smith' in c for c in initials)
    assert all(len(c.split())<=65 for c in initials)


def test_qa_accepts_printed_title_and_spoken_reading_but_keeps_wrong_meaning():
    assert comparison_tokens('Dr. Henry used 0.08 mg.')==comparison_tokens('Doctor Henry used 0.08 milligrams.')
    assert comparison_tokens('Dr. Henry')!=comparison_tokens('Drive Henry')
    assert comparison_tokens('St. John')!=comparison_tokens('Saint John')
    assert comparison_tokens('No. Do not restart')!=comparison_tokens('Number do restart')
