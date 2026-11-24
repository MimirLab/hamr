"""Compare renderings of the same printed page before shaping its reading."""

from collections import Counter
import subprocess
from unicodedata import normalize

from .core import Error


def text(words):
    return [normalize('NFC',word[11]) for word in words]


def box(word, size):
    x,y,w,h = (int(value) for value in word[6:10])
    width,height = size
    return x/width,y/height,(x+w)/width,(y+h)/height


def same_slot(first, second):
    left,top,right,bottom = first
    x,y,w,h = second
    overlap = (min(right,w)-max(left,x),min(bottom,h)-max(top,y))
    extent = (min(right-left,w-x),min(bottom-top,h-y))
    return all(a>=.48*b for a,b in zip(overlap,extent))


def align(primary, size, candidate, candidate_size):
    if len(primary)!=len(candidate):
        return False
    return all(same_slot(box(first,size),box(second,candidate_size))
               for first,second in zip(primary,candidate))


def verify_words(primary, size, alternatives, read):
    """Adopt a different word only when both alternate views bind to its slot."""
    initial = text(primary)
    readings = []
    report = {'policy':'printed-word-agreement-v1','views':[],'corrections':[]}
    for label,load in alternatives:
        try:
            with load() as image:
                words = read(image)
                candidate = text(words)
                aligned = align(primary,size,words,image.size)
        except (Error,OSError,subprocess.SubprocessError) as error:
            report['views'].append({'view':label,'status':'unavailable',
                                    'error':error.code if isinstance(error,Error) else type(error).__name__})
            continue
        report['views'].append({'view':label,'words':len(words),'aligned':aligned,
                                'exact':candidate==initial})
        if aligned and candidate==initial:
            report['outcome'] = 'agreed'
            return primary,report,()
        if aligned:
            readings.append(candidate)
    if len(readings)!=2:
        report['outcome'] = 'unresolved_order'
        return primary,report,None
    selected,unresolved = [],[]
    report['disputed'] = []
    for index,(observed,one,two) in enumerate(zip(initial,*readings)):
        readings_at_position = Counter((observed,one,two))
        value,count = readings_at_position.most_common(1)[0]
        source = primary[index]
        if count<2:
            unresolved.append(index)
            report['disputed'].append({'word':index,'readings':[source[11],one,two],
                                       'rect_pixels':list(map(int,source[6:10]))})
        elif value!=observed:
            report['corrections'].append({'word':index,'original':source[11],
                                          'confirmed':value,'rect_pixels':list(map(int,source[6:10]))})
            source = [*source[:11],value]
        selected.append(source)
    report.update(outcome='unresolved_words' if unresolved else 'agreed',unresolved_words=unresolved)
    return selected,report,tuple(unresolved)


def localize_verification(parts, words, unresolved, rect, scale):
    """Keep an uncertain reading at its printed source location."""
    if unresolved==():
        return
    left,_,_,top = rect
    affected = []
    if unresolved is not None:
        for index in unresolved:
            x,y,w,h = (int(value) for value in words[index][6:10])
            affected.append((left+(x+w/2)/scale,top-(y+h/2)/scale))
    touched = set()
    for part in parts:
        a,b,c,d = part['locator']['rect']
        for point,(x,y) in enumerate(affected):
            if a<=x<=c and b<=y<=d:
                part['ocr_visual_dispute'] = True
                touched.add(point)
    uncertain_page = unresolved is None or len(touched)!=len(affected)
    for part in parts:
        local = part.pop('ocr_visual_dispute',False)
        if uncertain_page or local:
            message = ('Different renderings disagree about the visible text or its order.'
                       if uncertain_page else 'Different renderings disagree about printed words in this passage.')
            part['issue'] = ' '.join(filter(None,(part['issue'],message)))
