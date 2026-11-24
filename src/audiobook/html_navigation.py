"""Bind legacy contents headings to their visible, verified anchor lists."""

from collections import Counter, defaultdict
import re
from urllib.parse import unquote, urlsplit

from bs4 import Tag, NavigableString

from .epub_semantics import NON_TEXT


HEADINGS = ('h1','h2','h3','h4','h5','h6')
CONTENTS_LABELS = {'contents','table of contents'}


def fragment_reference(href,part):
    try:
        target = urlsplit(href)
    except ValueError:
        return None
    if (target.scheme or target.netloc or target.query or not target.fragment
            or target.path and unquote(target.path).removeprefix('./').casefold()!=part.casefold()):
        return None
    return unquote(target.fragment)


def named_elements(body):
    elements = defaultdict(list)
    for tag in body.find_all(True):
        for value in {tag.get('id'),tag.get('name') if tag.name=='a' else None}-{None,''}:
            elements[value].append(tag)
    return elements


def anchor_targets(node,part):
    anchors = node.find_all('a',href=True)
    linked = {id(string) for anchor in anchors for string in anchor.strings}
    if any(str(string).strip() and id(string) not in linked for string in node.strings):
        return ()
    targets = []
    for anchor in anchors:
        if not (target:=fragment_reference(anchor['href'],part)):
            return ()
        targets.append(target)
    return targets


def linked_contents(body,part):
    candidates = [head for head in body.find_all(HEADINGS)
                  if head.get_text(' ',strip=True).casefold().rstrip(':') in CONTENTS_LABELS]
    if not candidates:
        return set()
    ids = named_elements(body)
    navigation = set()
    for head in candidates:
        group,targets = [head],set()
        for sibling in head.next_siblings:
            if isinstance(sibling,NavigableString):
                if str(sibling).strip() and not isinstance(sibling,NON_TEXT):
                    break
                continue
            if not isinstance(sibling,Tag) or sibling.name=='hr':
                break
            if not sibling.get_text(strip=True):
                visual = ('img','svg','math','audio','video')
                if sibling.name in visual or sibling.find(visual):
                    break
                continue
            found = anchor_targets(sibling,part)
            if not found or any(len(ids[anchor])!=1 for anchor in found):
                break
            group.append(sibling)
            targets.update(found)
        if len(targets)>=2:
            inside = {id(tag) for node in group for tag in (node,*node.descendants)}
            if all(id(ids[target][0]) not in inside for target in targets):
                navigation.update(map(id,group))
    return navigation


def illustration_titles(body,part):
    headings = [tag for tag in body.find_all(HEADINGS)
                if tag.get_text(' ',strip=True).casefold().rstrip(':')=='list of illustrations']
    if not headings:
        return {},set()
    anchors = named_elements(body)
    images = Counter(image['src'] for image in body.find_all('img',src=True))
    captions = defaultdict(list)
    rows = set()
    for heading in headings:
        indexed = []
        for row in heading.next_siblings:
            if not isinstance(row,Tag):
                continue
            if row.name=='hr' or row.name in HEADINGS and row.name<=heading.name:
                break
            links = row.find_all('a',href=True)
            found = []
            for link in links:
                target = fragment_reference(link['href'],part)
                owners = anchors.get(target,())
                if len(owners)!=1:
                    continue
                image, = owners
                if image.name=='a' and not image.get_text(strip=True):
                    image = next((item for item in image.next_siblings if isinstance(item,Tag) or
                                  str(item).strip() and not isinstance(item,NON_TEXT)),None)
                title = link.get_text(' ',strip=True)
                if isinstance(image,Tag) and image.name=='img' and images[image.get('src')]==1 and title:
                    source = image['src']
                    captions[source].append({'title':title,'anchor':target,'image':source,
                                             'origin':'Authored list of illustrations'})
                    found.append(link)
            if found:
                indexed.append(row)
                linked = {id(string) for link in found for string in link.strings}
                remainder = ' '.join(str(word).strip() for word in row.strings
                                     if str(word).strip() and id(word) not in linked)
                if not remainder or re.fullmatch(r'Chapter\s+(?:[IVXLCDM]+|[0-9]+)',remainder,re.I):
                    rows.add(id(row))
        if indexed:
            rows.add(id(heading))
    return {source:values[0] for source,values in captions.items() if len(values)==1},rows
