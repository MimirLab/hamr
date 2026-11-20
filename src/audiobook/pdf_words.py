"""Hold competing lexical readings before synthesis; never pick a winner."""
from collections import Counter
import re
from .linguistic import safe_unicode

POLICY='pdf-localized-words-v1'
SOFT=re.compile(r'([A-Za-z]+)[\u00ad\u00ac]\s*([A-Za-z]+)')


def words(text):
    text=SOFT.sub(lambda m:m[1]+m[2],safe_unicode(text)).lower().replace('’',"'")
    return re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)*",text)


def visual_reading(part,native,native_page):
    """Honor the existing narrowly corroborated line-wrap rule only."""
    text=part['text'];proven={m[1].lower()+m[2].lower() for m in SOFT.finditer(native_page)}
    for join in part['locator'].get('line_wrap_joins',[]):
        original=join.get('original','');spoken=join.get('spoken','')
        m=re.fullmatch(r'([A-Za-z]+)-\n([a-z]+)',original)
        if m and spoken==m[1]+m[2] and spoken.lower() in proven and spoken.lower() in words(native):
            text=text.replace(original,spoken)
    return text


def localize_words(parts,native,visual,read_region,qualified=True):
    regions=[]
    if qualified and native.strip():
        try:
            for part in parts:
                observed=read_region(part['locator']['rect'])
                reading=visual_reading(part,observed,native)
                regions.append({'paragraph':part['locator'].get('paragraph'),'rect':part['locator']['rect'],
                    'native_observation':observed,'visual_comparison_reading':reading,
                    'lexical_disagreement':words(observed)!=words(reading)})
        except (ValueError,RuntimeError):regions=[]
    native_coverage=bool(regions) and sum((Counter(words(r['native_observation'])) for r in regions),Counter())==Counter(words(native))
    visual_coverage=bool(regions) and sum((Counter(words(p['text'])) for p in parts),Counter())==Counter(words(visual))
    precise=native_coverage and visual_coverage
    hold=[]
    if precise:
        hold=[i for i,r in enumerate(regions) if r['lexical_disagreement']]
        reason='Native and visual text disagree on words or their order in this source region. Verify the visible source before synthesis.'
    else:
        disagreement=bool(native.strip()) and (words(native)!=words(visual) or any(r['lexical_disagreement'] for r in regions))
        if disagreement:hold=list(range(len(parts)))
        reason='Native and visual text have an unlocalized word disagreement. Verify the visible page before synthesis.'
    for i in hold:
        existing=parts[i].get('issue')
        parts[i]['issue']=(existing+' '+reason) if existing else reason
    return {'policy':POLICY,'scope':'paragraph' if precise else 'page' if native.strip() else 'unavailable',
        'qualified_geometry':qualified,'native_word_coverage':native_coverage,'visual_word_coverage':visual_coverage,
        'held_regions':len(hold),'regions':regions,
        'meaning':'Competing observations are review leads, not spelling corrections or visual-source certification. Numeric/symbol checks remain separate.'}
