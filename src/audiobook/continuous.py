"""Bounded coherent passages, with explicit evidence for unavoidable cuts."""
import re
from .boundaries import sentence_spans,safe_cut


EXTENDED_SENTENCE_WORDS = 220
EXTENDED_SENTENCE_BYTES = 1400
SENTENCE_END = re.compile(r'[.!?]["”’\')\]]*$')


def fits_extended_sentence(text):
    words = text.split()
    return (bool(SENTENCE_END.search(text)) and 1 < len(words) <= EXTENDED_SENTENCE_WORDS
            and len(text.encode()) <= EXTENDED_SENTENCE_BYTES
            and all(len(word.encode()) <= 1000 for word in words))


def passages(text,max_words=120,max_bytes=1000,separate_incomplete=False,max_sentence_words=120,whole_sentences=False):
    """Prefer complete sentences; reserve bounded fallbacks for long literals.

    The exact token sequence is preserved. A single oversized token is returned
    for source review, never truncated or sent to the backend as valid input.
    """
    text=' '.join(text.split());result=[];pending=''
    def fits(value):return len(value.split())<=max_words and len(value.encode())<=max_bytes
    def append(value,kind):
        if value:result.append({'text':value,'boundary':kind,'over_budget':not fits(value)})
    for span in sentence_spans(text):
        sentence=span['text']
        if not sentence:continue
        closed=bool(re.search(r'[.!?]["”’\')\]]*$',sentence))
        if separate_incomplete and not closed:
            if pending:append(pending,'sentence');pending=''
            if fits(sentence):append(sentence,'incomplete');continue
        if fits((pending+' '+sentence).strip()):pending=(pending+' '+sentence).strip();continue
        if pending:append(pending,'sentence');pending=''
        if fits(sentence):pending=sentence;continue
        if separate_incomplete and closed and len(sentence.split())<=max_sentence_words and len(sentence.encode())<=max_bytes:
            result.append({'text':sentence,'boundary':'sentence','over_budget':False});continue
        if whole_sentences:
            result.append({'text':sentence,'boundary':'sentence','over_budget':not fits_extended_sentence(sentence)})
            continue
        words=sentence.split()
        while words:
            if fits(' '.join(words)):
                if separate_incomplete:append(' '.join(words),'continuation')
                else:pending=' '.join(words)
                break
            limit=1
            while limit<len(words) and fits(' '.join(words[:limit+1])):limit+=1
            if limit==len(words):append(' '.join(words),'oversized_token');break
            clauses=[i for i in range(3,limit+1) if re.search(r'[,;:—–]$',words[i-1])]
            cut=clauses[-1] if clauses else safe_cut(words,limit)
            # Attached protected groups may exceed the byte budget: hold them.
            cut=max(1,cut);append(' '.join(words[:cut]),'clause' if clauses else 'forced');words=words[cut:]
    if pending:append(pending,'sentence' if re.search(r'[.!?]["”’\')\]]*$',pending) else 'incomplete')
    return result
