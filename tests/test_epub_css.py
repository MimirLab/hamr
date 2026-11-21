from test_upstream_epub import book,speech
from audiobook.importers import epub
from audiobook.planner import plan


def test_css_semantics_are_authored_not_guessed_from_class_names():
    body='<style>.hide {display:none} .speaker::before {content:attr(data-speaker) ": "}</style><p>Before <span class="hide">HIDDEN</span> after.</p><p class="screen-reader-only">OFFSCREEN_PROSE.</p><p class="speaker" data-speaker="ALICE">Hello.</p>'
    d=epub(book({'c':body},['c']));p=plan(d)
    assert speech(p)=='Before after. OFFSCREEN_PROSE. ALICE: Hello.' and not p['issues']
    assert any('HIDDEN' in row['source'] for row in p['decisions'])


def test_inline_and_important_cascade_is_respected():
    body='<style>.hide {display:none} .force {display:none !important}</style><p class="hide" style="display:block">VISIBLE.</p><p class="force" style="display:block">HIDDEN.</p>'
    p=plan(epub(book({'c':body},['c'])))
    assert speech(p)=='VISIBLE.' and not p['issues']


def test_css_superscript_retains_ambiguous_semantics_as_review():
    body='<style>.raised {vertical-align:super}</style><p>Value 10<span class="raised">3</span>.</p>'
    p=plan(epub(book({'c':body},['c'])))
    assert not p['utterances'] and any('superscript' in r['reason'] for r in p['issues'])


def test_static_css_siblings_and_namespaces_keep_visibility_scoped():
    body=('<style>@namespace se "http://www.idpf.org/2007/ops"; '
          '[se|type~="z3998:poem"] p > span + b, [data-text="a,b"] ~ b {display:none}</style>'
          '<section epub:type="z3998:poem"><p><span>First.</span> <b>HIDDEN.</b> Next.</p></section>'
          '<p><i data-text="a,b">Second.</i> <b>HIDDEN TOO.</b> Again.</p>'
          '<p><span>Third.</span> <b>VISIBLE.</b></p>')
    p=plan(epub(book({'c':body},['c'])))
    assert not p['issues']
    assert speech(p)=='First. Next. Second. Again. Third. VISIBLE.'
    assert sum('HIDDEN' in row['source'] for row in p['decisions'])==2


def test_css_font_size_supports_branch_keeps_the_accessible_text():
    body=('<style>.title {display:none} @supports(font-size: 0) {.title {display:block}} '
          '@media (prefers-color-scheme: dark) {.body {display:block}}</style>'
          '<p class="title">TITLE.</p><p class="body">BODY.</p>')
    p=plan(epub(book({'c':body},['c'])))
    assert not p['issues'] and speech(p)=='TITLE. BODY.'
    uncertain=body.replace('font-size: 0','unknown-property: value')
    p=plan(epub(book({'c':uncertain},['c'])))
    assert p['issues'] and all('Conditional CSS' in row['reason'] for row in p['issues'])


def test_unqualified_generated_content_does_not_silently_disappear():
    body='<style>p::before {content:counter(chapter)}</style><p>BODY_CANARY.</p>'
    p=plan(epub(book({'c':body},['c'])))
    assert not p['utterances'] and any('Generated' in r['reason'] or 'generated' in r['reason'] for r in p['issues'])


def test_attribute_selectors_and_note_marker_decoration_are_scoped():
    body='<style>a[role=doc-noteref] sup:before {content:"["} a[role=doc-noteref] sup:after {content:"]"}</style><p>BEFORE<a role="doc-noteref" href="#n"><sup>1</sup></a> AFTER.</p><aside epub:type="footnote" id="n">NOTE_CANARY.</aside>'
    p=plan(epub(book({'c':body},['c'])))
    assert not p['issues'] and speech(p)=='BEFORE AFTER. Note. NOTE_CANARY. End note.'


def test_container_generated_labels_are_retained_at_the_authored_boundaries_once():
    body='<style>section::before {content:"START."} section::after {content:"END."} .empty::before {content:"EMPTY_LABEL."}</style><section><p>FIRST.</p><p>SECOND.</p></section><div class="empty"/><section hidden="hidden"><p>SECRET.</p></section>'
    p=plan(epub(book({'c':body},['c'])))
    assert not p['issues'] and speech(p)=='START. FIRST. SECOND. END. EMPTY_LABEL.'
