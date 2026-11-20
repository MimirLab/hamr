"""Compare competing observations without treating either as an authority.

Multisets allow table-column traversal differences. Matching signatures do not
certify word order, cell association or accuracy.
"""
from collections import Counter
import re
import unicodedata
from .spoken_forms import REGNAL_NAME,REGNAL_NUMBER,roman


REGNAL_GLYPH=re.compile(r'\b(?:'+REGNAL_NAME+r')\s+\[(?=\s|$)')
BRACKET_CONTENT=re.compile(r'[^\[\]]*\]')


def damaged_regnal_glyph(text):
    """Hold a stray bracket in a possible regnal suffix after visual OCR."""
    return any(not BRACKET_CONTENT.match(text,match.end()) for match in REGNAL_GLYPH.finditer(text))


def critical_signature(text):
    from .speech_compare import mathematical_markers
    text=unicodedata.normalize('NFC',text).replace('−','-')
    numbers=re.findall(r'(?<![\w.])[+-]?\d+(?:[.,]\d+)*',text)
    symbols=re.findall(r'[±=<>≤≥≠×÷]|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂₃₄₅₆₇₈₉]+',text)
    # Compare competing I/T/l/1 forms in a bounded prose context. This locates
    # an ambiguity; it never rewrites a literal variable or selects a winner.
    word_forms=re.findall(r'\b([ITl1])\s+((?:(?:once|also|then)\s+)?(?:am|have|had|know|believe|use|think|was|decided|worked))\b',text,re.I)
    regnal=Counter(f'{match[1]} {value}' for match in REGNAL_NUMBER.finditer(text)
                   if (value:=roman(match[2])) is not None)
    return {'numbers':dict(sorted(Counter(numbers).items())),
            'symbols':dict(sorted(Counter(symbols).items())),
            'typed_math':mathematical_markers(text),
            'regnal_numbers':dict(sorted(regnal.items())),
            'word_forms':dict(sorted(Counter(' '.join(words) for words in word_forms).items()))}


def disputes(native,visual):
    if not native.strip():return []
    first,second=critical_signature(native),critical_signature(visual)
    return [{'kind':kind,'native':first[kind],'visual':second[kind]} for kind in first if first[kind]!=second[kind]]
