"""Find source characters whose spoken reading needs evidence."""
import re


REVIEW_CHARS = re.compile(r'[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\ufffd\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]')


def suspect_characters(text):
    samples = []
    count = 0
    for match in REVIEW_CHARS.finditer(text):
        count += 1
        if len(samples) < 32:
            samples.append({'start': match.start(), 'end': match.end(), 'codepoint': f'U+{ord(match[0]):04X}'})
    return {'count': count, 'samples': samples} if count else None
