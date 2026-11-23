import pytest

from audiobook import performance, planner
from audiobook.importers import inspect


FIRST = '"Wait for the lantern," she whispered.'
SECOND = '"The latch is still closed."'


def quote_pdf(first, following, *, tags=(), indent=0, leading=15):
    """Make two visible lines and optionally attach page-local paragraph tags."""
    tagged = {line: group for group, lines in enumerate(tags, 8) for line in lines}
    contents = []
    for index, text in enumerate((first, following)):
        if index in tagged:
            contents.append(f'/P <</MCID {index}>> BDC')
        contents.append(f'BT /F1 13 Tf 1 0 0 1 {65 + index * indent} {720 - index * leading} Tm ({text}) Tj ET')
        if index in tagged:
            contents.append('EMC')
    stream = '\n'.join(contents)
    catalog_tags = ' /MarkInfo << /Marked true >> /StructTreeRoot 6 0 R' if tagged else ''
    page_tags = ' /StructParents 0' if tagged else ''
    objects = [
        '<< /Type /Catalog /Pages 2 0 R' + catalog_tags + ' >>',
        '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R'
        ' /Resources << /Font << /F1 4 0 R >> >>' + page_tags + ' >>',
        '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        f'<< /Length {len(stream)} >>\nstream\n{stream}\nendstream',
    ]
    if tagged:
        paragraphs = ' '.join(f'{index} 0 R' for index in range(8, 8 + len(tags)))
        parents = ' '.join(f'{tagged[index]} 0 R' if index in tagged else 'null' for index in (0, 1))
        objects.extend([
            f'<< /Type /StructTreeRoot /K [{paragraphs}] /ParentTree 7 0 R >>',
            f'<< /Nums [0 [{parents}]] >>',
            *(f'<< /Type /StructElem /S /P /P 6 0 R /Pg 3 0 R /K [{" ".join(map(str, lines))}] >>'
              for lines in tags),
        ])
    pdf = b'%PDF-1.7\n'
    offsets = ['0000000000 65535 f ']
    for index, obj in enumerate(objects, 1):
        offsets.append(f'{len(pdf):010d} 00000 n ')
        pdf += f'{index} 0 obj\n{obj}\nendobj\n'.encode()
    xref = f'xref\n0 {len(offsets)}\n' + '\n'.join(offsets)
    trailer = f'\ntrailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{len(pdf)}\n%%EOF\n'
    return pdf + (xref + trailer).encode()


@pytest.mark.parametrize(('tags', 'expected_flow', 'recipe'), [
    ((), 'adjacent_pdf_quotation', None),
    (((0, 1),), 'tagged_pdf_quotation', 'whisper'),
    (((0,), (1,)), None, 'whisper'),
    (((0,),), None, 'whisper'),
])
def test_pdf_quote_flow_uses_tags_to_apply_delivery_across_visible_lines(tmp_path, tags, expected_flow, recipe):
    source = tmp_path / 'story.pdf'
    source.write_bytes(quote_pdf(FIRST, SECOND, tags=tags))
    document = inspect(source)
    result = planner.plan(document)
    first = result['utterances'][0]
    assert first.get('performance_candidate', {}).get('recipe') == recipe
    assert [block.source for block in document.blocks] == [FIRST, SECOND]
    legacy = planner.plan(document, delivery_context=10)
    assert [unit['text'] for unit in legacy['utterances']] == [FIRST, SECOND]
    if expected_flow is None:
        assert [unit['text'] for unit in result['utterances']] == [FIRST, SECOND]
        assert 'performance_candidate' not in result['utterances'][1]
        return
    assert len(result['utterances']) == 1
    assert first['text'] == FIRST + ' ' + SECOND
    assert first['delivery_boundary'] == expected_flow
    assert first['sources'] == [block.id for block in document.blocks]
    if recipe:
        cue, = first['performance_candidate']['evidence']
        assert cue['form'] == 'continued_quotation'
        assert [part['block'] for part in cue['quotation_parts']] == first['sources']
        assert [block.source[slice(*part['span'])] for block, part in
                zip(document.blocks, cue['quotation_parts'], strict=True)] == ['"Wait for the lantern,"', SECOND]
    plain, = planner.plan(document, {'performance': 'off'})['utterances']
    assert plain['text'] == first['text'] and plain['delivery_boundary'] == expected_flow
    assert performance.request(plain)[1] == {'seed': 42}


@pytest.mark.parametrize(('following', 'style'), [
    (SECOND, {'indent': 16}),
    (SECOND, {'leading': 35}),
    ('"I caught the latch," he replied.', {}),
])
def test_pdf_quote_flow_respects_visible_or_spoken_boundaries(tmp_path, following, style):
    source = tmp_path / 'story.pdf'
    source.write_bytes(quote_pdf(FIRST, following, **style))
    first, second = planner.plan(inspect(source))['utterances']
    assert first['text'] == FIRST
    assert second['text'] == following
    assert first['performance_candidate']['recipe'] == 'whisper'
    assert 'performance_candidate' not in second


@pytest.mark.parametrize(('tail','layout','joined'), [
    ('And her voice had softened too.', {}, True),
    ('And her voice had softened too.', {'tags':((0, 1),)}, True),
    ('And her voice had softened too.', {'tags':((0,), (1,))}, False),
    ('And her voice had softened too.', {'leading':35}, False),
    ('And his voice had softened too.', {}, False),
])
def test_pdf_gives_trailing_voice_description_to_the_previous_reporter(tmp_path,tail,layout,joined):
    question = 'The door closed. "What now?" she asked him.'
    source = tmp_path/'story.pdf'
    source.write_bytes(quote_pdf(question,tail,**layout))
    document = inspect(source)
    assert [block.source for block in document.blocks] == [question,tail]
    assert [unit['text'] for unit in planner.plan(document,delivery_context=30)['utterances']] == [question,tail]
    result = planner.plan(document)
    if not joined:
        assert [unit['text'] for unit in result['utterances']] == [question,tail]
        assert not result['performance']['candidates']
        return
    unit, = result['utterances']
    assert unit['text']==question+' '+tail
    assert unit['delivery_boundary']=='pdf_voice_description'
    direction = unit['performance_candidate']
    assert direction['recipe']=='murmured'
    evidence, = direction['evidence']
    assert evidence['block']==document.blocks[1].id
    assert tail[slice(*evidence['span'])]=='her voice had softened too'
    quote, = evidence['quotation_parts']
    assert quote['block']==document.blocks[0].id
    assert question[slice(*quote['span'])]=='"What now?"'
