import io,zipfile
from audiobook.importers import epub
from audiobook.planner import plan
from audiobook.core import digest


def fixture():
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        z.writestr('book.opf','<package><metadata><title>Scoped pronunciation</title><language>en</language></metadata><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/><item id="lex" href="words.pls" media-type="application/pls+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr('a.xhtml','<html xmlns:tts="http://www.w3.org/2001/10/synthesis" tts:alphabet="ipa" xml:lang="en-US"><head><link rel="pronunciation" type="application/pls+xml" hreflang="en" href="words.pls"/></head><body><p>We lead the team.</p><p>Use <span tts:ph="lɛd">lead</span> here.</p><p xml:lang="fr">le renard</p></body></html>')
        z.writestr('b.xhtml','<html xml:lang="en"><body><p>We lead another team.</p></body></html>')
        z.writestr('words.pls','<lexicon xmlns="http://www.w3.org/2005/01/pronunciation-lexicon" version="1.0" xml:lang="en" alphabet="ipa"><lexeme><grapheme>lead</grapheme><alias>leed</alias></lexeme></lexicon>')
    return data.getvalue()


def test_linked_plain_alias_stays_in_document_and_language_scope():
    d=epub(fixture());p=plan(d);text=' '.join(u['text'] for u in p['utterances'])
    assert 'We leed the team.' in text and 'We lead another team.' in text
    assert 'le renard' not in text and len(p['issues'])==2
    assert 'We lead the team.' in [b.source for b in d.blocks]


def test_inline_hint_has_precedence_and_inherits_alphabet_without_fake_adapter_support():
    d=epub(fixture());b=next(b for b in d.blocks if b.source=='Use lead here.')
    hints=b.locator['inline_semantics'];inline=next(s for s in hints if s['kind']=='pronunciation_hint')
    assert inline['alphabet']=='ipa' and inline['phoneme']=='lɛd' and inline['applied'] is False
    assert next(s for s in hints if s['kind']=='lexicon_hint')['shadowed_by_inline']
    p=plan(d);assert any(i['block']==b.id and 'phoneme' in i['reason'] for i in p['issues'])
    policy={'source_sha256':d.source_sha256,'blocks':{b.id:{'action':'speak','verified':True,'expected_source_sha256':digest(b.source.encode()),'spoken':'Use led here.','reason':'Source-bound auditioned plain reading for this occurrence.'}}}
    repaired=plan(d,{'document':policy},aliases={'lead':'profile choice'})
    assert any(u['text']=='Use led here.' for u in repaired['utterances'])
    assert 'We leed the team.' in ' '.join(u['text'] for u in repaired['utterances'])
