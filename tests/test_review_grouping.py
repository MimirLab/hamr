from test_upstream_epub import book
from audiobook.importers import epub
from audiobook.planner import plan
from audiobook.core import digest


def test_authored_senses_group_without_merging_different_pronunciations_or_accepting_them():
    body='<p>Use <span xmlns:ssml="http://www.w3.org/2001/10/synthesis" ssml:ph="lɛd" ssml:alphabet="ipa">lead</span> pipe.</p><p>Another <span xmlns:ssml="http://www.w3.org/2001/10/synthesis" ssml:ph="lɛd" ssml:alphabet="ipa">lead</span> pipe.</p><p>Now <span xmlns:ssml="http://www.w3.org/2001/10/synthesis" ssml:ph="liːd" ssml:alphabet="ipa">lead</span> them.</p>'
    d=epub(book({'c':body},['c']));p=plan(d);groups=p['review_groups']['groups']
    assert sorted(len(g['occurrences']) for g in groups)==[1,2] and len(p['issues'])==3 and not p['utterances']
    assert all(o['source_sha256']==digest(o['context'].encode()) for g in groups for o in g['occurrences'])
    b=d.blocks[0];policy={'source_sha256':d.source_sha256,'blocks':{b.id:{'action':'speak','verified':True,'expected_source_sha256':digest(b.source.encode()),'spoken':'Use led pipe.','reason':'Auditioned this occurrence.'}}}
    repaired=plan(d,{'document':policy})
    assert len(repaired['issues'])==2 and [u['text'] for u in repaired['utterances']]==['Use led pipe.']
    assert sum(len(g['occurrences']) for g in repaired['review_groups']['groups'])==2
