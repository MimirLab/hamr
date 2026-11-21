import io
import zipfile

import pytest
from reportlab.pdfgen.canvas import Canvas

from audiobook.core import Error, digest
from audiobook.importers import cached_inspect, epub, inspect, markdown, pdf
from audiobook.planner import normalize, plan
from audiobook.speech_compare import comparison_tokens, mathematical_markers
from tests.test_import import make_epub


def speech(result):
    return ' '.join(take['text'] for take in result['utterances'])


def test_markdown_notes_keep_ast_definitions_and_exact_reference_owners():
    source = ('# Notes\r\n\r\nOne.[^a] Literal `[^fake]`. Escape \\[^missing].\r\n\r\n'
              'Two.[^b] Inline ^[A *short* note.]\r\n\r\n'
              '[^a]: First *paragraph*.\r\n\r\n    Second paragraph.\r\n\r\n    - List item\r\n\r\n'
              '[^b]: First *paragraph*.\r\n\r\n    Second paragraph.\r\n\r\n    - List item\r\n\r\n'
              '[^unused]: Unreferenced context.\r\n')
    doc = markdown(source.encode()); result = plan(doc)
    assert not result['issues']
    assert speech(result).count('First paragraph. Second paragraph. List item.') == 2
    assert 'A short note.' in speech(result) and 'Unreferenced context' not in speech(result)
    body = [b for b in doc.blocks if b.role == 'body']
    assert len(body[0].links) == 1 and body[0].links[0]['href'] == 'markdown-note:a'
    assert body[0].links[0]['destination'] != body[1].links[0]['destination']
    notes = [b for b in doc.blocks if b.role == 'note']
    assert notes[0].source == notes[1].source
    assert all(b.locator['exact'] == source[b.locator['start']:b.locator['end']] for b in notes if 'start' in b.locator)
    assert 'Note.' in speech(plan(doc, {'notes': 'inline'}))
    assert 'First paragraph' not in speech(plan(doc, {'notes': 'companion'}))


@pytest.mark.parametrize('text', ['See [^missing].',
                                  'See [^a].\n\n[^a]: The first note cites [^b].\n\n[^b]: Inner note.',
                                  'See [^a].\n\n[^a]: First meaning.\n\n[^a]: Second meaning.'])
def test_unresolved_markdown_note_retains_source_and_holds_spoken_plan(text):
    doc = markdown(text.encode()); result = plan(doc)
    assert result['issues'] and not result['utterances']
    assert '[^' in doc.blocks[0].locator['exact']


def modified_epub(transform=None, encryption=None, extra=None):
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(make_epub())) as original, zipfile.ZipFile(result, 'w') as output:
        for name in original.namelist():
            data = original.read(name)
            output.writestr(name, transform(name, data) if transform else data)
        if encryption is not None:
            output.writestr('META-INF/encryption.xml', encryption)
        for name,data in (extra or {}).items():
            output.writestr(name,data)
    return result.getvalue()


def font_manifest(name, data):
    if name.endswith('.opf'):
        return data.replace(b'</manifest>', b'<item id="font" href="Book%20Font.otf" media-type="font/otf"/></manifest>')
    return data


def encrypted(target, algorithm='http://www.idpf.org/2008/embedding'):
    return ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
            f'<enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>'
            f'<enc:CipherData><enc:CipherReference URI="{target}"/></enc:CipherData>'
            '</enc:EncryptedData></encryption>')


def test_font_only_obfuscation_preserves_every_narration_block():
    raw = modified_epub(font_manifest, encrypted('OEBPS/Book%20Font.otf'), {'OEBPS/Book Font.otf': b'obfuscated bytes'})
    imported = epub(raw)
    assert imported.source_sha256 == digest(raw)
    assert imported.blocks == epub(make_epub()).blocks
    assert imported.inventory[-1]['kind'] == 'font_obfuscation'


@pytest.mark.parametrize('target,algorithm', [('OEBPS/a.xhtml','http://www.idpf.org/2008/embedding'),
                                             ('OEBPS/Book%20Font.otf','urn:unknown'),
                                             ('OEBPS/%2E%2E/Book%20Font.otf','http://www.idpf.org/2008/embedding'),
                                             ('OEBPS/unknown.ttf','http://www.idpf.org/2008/embedding')])
def test_font_exemption_does_not_admit_protected_content_or_unknown_resource(target, algorithm):
    with pytest.raises(Error) as raised:
        epub(modified_epub(font_manifest, encrypted(target,algorithm), {'OEBPS/Book Font.otf': b'font'}))
    assert raised.value.code == 'protected_epub'


def test_epub_resolves_two_notes_in_the_same_paragraph():
    def transform(name, data):
        if name.endswith('/a.xhtml'):
            return data.replace(b'</a></p>',b'</a><a role="doc-noteref" href="notes.xhtml#n2">2</a></p>')
        if name.endswith('/notes.xhtml'):
            return data.replace(b'</body>',b'<aside id="n2">Maintain ventilation.</aside></body>')
        return data
    doc = epub(modified_epub(transform))
    claims = [b for b in doc.blocks if b.locator.get('anchor') == 'claim']
    assert len(claims[0].links) == 2 and len({link['source_start'] for link in claims[0].links}) == 2
    result = plan(doc)
    assert not result['issues'] and speech(result).count('Maintain ventilation.') == len(claims)


def test_epub_inventory_holds_visible_embedded_content_and_keeps_hidden_occurrences():
    def transform(name, data):
        if name.endswith('/b.xhtml'):
            return data.replace(b'</body>',b'<video src="field.mp4"/><canvas hidden="hidden"/></body>')
        return data
    doc = epub(modified_epub(transform))
    occurrence = next(row for row in doc.inventory if row.get('path')=='OEBPS/b.xhtml')
    assert [(row['element'],row['disposition']) for row in occurrence['embedded_objects']]==[('video','source review'),('canvas','authored hidden')]
    blocked = next(b for b in doc.blocks if b.locator.get('element_type')=='video')
    assert 'field.mp4' in blocked.locator['exact'] and any(issue['block']==blocked.id for issue in plan(doc)['issues'])
    choices = {'source_sha256':doc.source_sha256,'blocks':{blocked.id:{'action':'companion','verified':True,'reason':'Video retained for lookup.'}}}
    assert not plan(doc,{'document':choices})['issues']


@pytest.mark.parametrize('encoding', ['utf-8','utf-8-sig','utf-16','utf-32'])
def test_plain_text_decodes_strictly_and_accounts_for_source_lines(tmp_path,encoding):
    original = 'Preserve café\r\nand *literal* markers.\r\n\r\nNext paragraph.\r\n'
    path = tmp_path/'Control.txt';raw = original.encode(encoding);path.write_bytes(raw)
    doc = cached_inspect(path,tmp_path/'cache')
    assert doc.json() == inspect(path).json()
    assert doc.source_sha256 == digest(raw)
    assert doc.inventory[0]['represented_lines'] == 3 and doc.inventory[0]['blank_lines'] == 1
    assert [b.locator['exact'] for b in doc.blocks] == ['Preserve café\r\nand *literal* markers.\r\n','Next paragraph.\r\n']
    assert '*literal*' in speech(plan(doc))


def test_unknown_legacy_encoding_is_an_actionable_error(tmp_path):
    path = tmp_path/'Unknown.txt';path.write_bytes(b'caf\xe9')
    with pytest.raises(Error) as raised:
        inspect(path)
    assert raised.value.code == 'text_encoding'


def test_typed_readings_preserve_distinct_repeated_ranges_and_unknown_forms():
    text = 'Room 007 costs $12.50. Departure 08:05. Record 0.080 and 0.080. Use 1,234.500 mL at 20 °C. Dates 03/04 and v1.090 stay literal.'
    decision = plan(markdown(text.encode()))['decisions'][0]
    assert 'Room zero zero seven costs twelve dollars and fifty cents.' in decision['spoken']
    assert 'Departure time, eight oh five.' in decision['spoken']
    assert 'zero point zero eight zero and zero point zero eight zero' in decision['spoken']
    assert 'one thousand two hundred thirty four point five zero zero milliliters at twenty degrees Celsius' in decision['spoken']
    assert 'zero three slash zero four and v1.090' in decision['spoken']
    audit = decision['normalization']
    basis = decision['source'] if audit['input_basis'] == 'block_source' else audit['input']
    for edit in audit['edits']:
        assert basis[edit['input_start']:edit['input_end']] == edit['source']
        assert decision['spoken'][edit['output_start']:edit['output_end']] == edit['spoken']
    decimals = [edit for edit in audit['edits'] if edit['kind']=='decimal_precision']
    assert len(decimals)==2 and decimals[0]['input_end']<decimals[1]['input_start']
    assert normalize('Use 0.080.',rule_revision='faithful-12')=='Use 0.080.'
    assert comparison_tokens('Keep 0.080.') != comparison_tokens('Keep zero point zero eight.')
    assert mathematical_markers('Use 5 mL at 20 °C.') != mathematical_markers('Use 5 mL at 20 °F.')


def test_clock_observation_requires_explicit_clock_words_and_keeps_exact_minutes():
    expected = comparison_tokens('Departure time, eight oh five.')
    for observed in ('Departure time 08:05.','Departure time 805.','Departure time 8.05.','Departure time. 8.05.'):
        assert comparison_tokens(observed)==expected
    for other in ('Departure time 08:50.','Departure time 890.','Departure 805.','Departure time 805 UTC.','Clock reads 8.05 seconds.',
                  'Departure time was unknown. 8.05.'):
        assert comparison_tokens(other)!=expected


def test_normalization_audit_distinguishes_canonicalized_input_and_literals():
    text = 'Cafe\u0301 records 0.080.'
    decision = plan(markdown(text.encode()))['decisions'][0]
    audit = decision['normalization']
    assert audit['input_basis']=='normalization_input' and audit['input']=='Café records 0.080.'
    literal = 'Keep `0.080` literal, then check 0.080.'
    spoken = speech(plan(markdown(literal.encode())))
    assert spoken == 'Keep 0.080 literal, then check zero point zero eight zero.'
    assert normalize('Open https://example.test/?price=$12.50 and read v2.04.01.')=='Open https://example.test/?price=$12.50 and read v2.04.01.'


def test_typed_reading_survives_request_packing_inside_a_long_sentence():
    before = ' '.join(['a quiet steady message']*28)+' held back while we measure '
    text = before + '0.080 mL during the pause. '
    for flow in ('continuous','segmented'):
        doc = markdown(text.encode())
        result = plan(doc,{'document':{'source_sha256':doc.source_sha256,'delivery':{'flow':flow}}})
        assert not result['issues'] and any('zero point zero eight zero milliliters' in take['text'] for take in result['utterances'])


def test_unknown_character_is_localized_and_can_be_repaired():
    doc = markdown('Known line.\n\nValue 0\ue00193 is uncertain.\n\nAnother intact line.'.encode())
    result = plan(doc); bad = result['decisions'][1]
    assert len(result['issues'])==1 and bad['action']=='unreadable'
    sample = bad['source_characters']['samples'][0]
    assert bad['source'][sample['start']:sample['end']]=='\ue001'
    assert 'Known line.' in speech(result) and 'Another intact line.' in speech(result)
    policy = {'source_sha256':doc.source_sha256,'blocks':{bad['block']:{'verified':True,'action':'speak',
              'reason':'Checked the original visual reading.', 'expected_source_sha256':digest(bad['source'].encode()),'spoken':'Value 0.93 is uncertain.'}}}
    assert 'Value 0.93' in speech(plan(doc,{'document':policy}))


def test_nonpainting_pdf_text_admits_an_independent_visual_reading(monkeypatch):
    raw = io.BytesIO(); writer = Canvas(raw,pagesize=(220,100))
    writer.drawString(20,70,'Visible value 0.08.')
    hidden = writer.beginText(20,70);hidden.setTextRenderMode(3);hidden.textOut('Visible value 0.80.');writer.drawText(hidden);writer.save()
    observation = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
                   '5\t1\t1\t1\t1\t1\t40\t40\t190\t24\t96\tVisible value 0.08.\n')
    monkeypatch.setattr('audiobook.pdf_ocr.configuration',lambda:None)
    monkeypatch.setattr('audiobook.pdf_ocr.observe',lambda *a:(observation,False))
    doc = pdf(raw.getvalue()); page = doc.inventory[0]
    assert page['ocr_admission']==['nonpainting text layer'] and page['nonpainting_text_regions'][0]['render_mode']==3
    assert '0.80' in page['native_observation'] and '0.08' in page['ocr_observation']
    assert plan(doc)['issues']
