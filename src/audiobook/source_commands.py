"""Bind displayed command alternatives to the sentence that introduces them."""

from dataclasses import replace
import re


EITHER = re.compile(r'\beither:?\s*\Z', re.I)
COMMAND = re.compile(r'[A-Za-z.][A-Za-z0-9_./\\:-]*(?:[ \t]+[A-Za-z0-9_./\\:~-]+){0,5}')
SCOPE = ('chapter_id','page','member','occurrence','path','source_file','block_quote')
IDENTIFIERS = ('anchor','container_anchors','ordered_item','chapter_marker')
LITERAL_STYLES = frozenset(('preformattedtext','htmlpreformatted','sourcecode','codeblock'))


def short_command(text):
    return len(text)<=96 and COMMAND.fullmatch(text) is not None


def literal_style(name):
    label = re.sub(r'[\s_-]+','',(name or '').casefold().replace('_20_',' '))
    return label in LITERAL_STYLES


def literal_spans(spans):
    return all(span['kind'] in ('literal','line_break','emphasis') for span in spans)


def literal_paragraph_location(node, member, definition, style_member, ident):
    tree = node.getroottree()
    previous = node.getprevious()
    return {
        'literal_paragraph_style':{'name':ident,'source_file':style_member,
                                   'xml_path':definition.getroottree().getpath(definition)},
        'literal_paragraph_container':tree.getpath(node.getparent()),
        'literal_paragraph_previous':member+':'+tree.getpath(previous) if previous is not None else None,
    }


def scope(block):
    return block.chapter,*(block.locator.get(key) for key in SCOPE)


def display(block):
    return not block.locator.get('alternate_rendering') and short_command(block.source.strip()) and (block.role=='code' or (
        block.role=='body' and block.locator.get('literal_display_frame')))


def printed_alignment(lead, first, conjunction, second):
    left = first.locator['literal_display_frame']
    right = second.locator['literal_display_frame']
    height = left[3]-left[1]
    return (abs(left[0]-right[0])<=height/4 and abs(height-(right[3]-right[1]))<=height/4
            and abs(lead.locator['rect'][0]-left[0])<=height/2
            and abs(conjunction.locator['rect'][0]-left[0])<=height/2
            and lead.locator['rect'][1]>left[3] and left[1]>conjunction.locator['rect'][3]
            and conjunction.locator['rect'][1]>right[3])


def choices(run, following):
    lead,first,conjunction,second = run
    marker = EITHER.search(lead.source)
    if (lead.role!='body' or not marker or conjunction.role!='body'
            or conjunction.source.strip().lower()!='or' or not display(first) or not display(second)):
        return False
    if any(span['source_start']<marker.end() and span['source_end']>marker.start()
           for span in lead.locator.get('inline_semantics',())):
        return False
    from .source_passages import has_authored_breaks

    if has_authored_breaks(lead):
        return False
    owner = scope(lead)
    if any(block.issue or scope(block)!=owner for block in run):
        return False
    if any(block.links or not literal_spans(block.locator.get('inline_semantics',())) or
           any(block.locator.get(key) is not None and block.locator.get(key)!=[] for key in IDENTIFIERS)
           for block in run[1:]):
        return False
    if conjunction.locator.get('inline_semantics'):
        return False
    if (lead.locator.get('page') is not None and not (first.locator.get('literal_display_frame')
            and second.locator.get('literal_display_frame') and printed_alignment(*run))):
        return False
    # A following lower-case clause could belong to either choice. Keep that
    # case separate until its continuation has an authored boundary.
    if (following and scope(following)==owner and following.role=='body'
            and following.locator.get('ordered_item') is None):
        start = following.source.lstrip()
        if start and (start[0].islower() or start[0] in ',;:)]'):
            return False
    return True


def pieces(run,separator):
    fragments = []
    text = []
    cursor = 0
    for index, block in enumerate(run):
        value = block.source.strip() if index else block.source
        if index:
            cursor += len(separator)
        end = cursor+len(value)
        loc = {**block.locator,'source_block':block.id,'source_role':block.role,
               'passage_range':[cursor,end]}
        fragments.append(loc)
        text.append(value)
        cursor = end
    return separator.join(text),fragments


def bind(run):
    lead = run[0]
    spans = list(lead.locator.get('inline_semantics', ()))
    source,fragments = pieces(run,' ')
    for part in (fragments[1],fragments[3]):
        start,end = part['passage_range']
        spans.append({'kind':'literal','source':source[start:end],'source_start':start,'source_end':end,
                      'source_locator':part})
    spans.append({'kind':'layout_reading','source':'','source_start':len(source),'source_end':len(source),'reading':'.'})
    locator = {**lead.locator,'inline_semantics':spans,'source_fragments':fragments,
               'layout_reading':'Displayed command alternatives in one sentence'}
    return replace(lead,source=source,locator=locator,
                   evidence=[*lead.evidence,'The written either-or construction joins two short literal displays.'])


def literal_paragraph_owner(block):
    container = block.locator.get('literal_paragraph_container')
    if (container is not None and block.role=='code' and not block.issue and not block.links
            and not any(block.locator.get(key) for key in IDENTIFIERS)):
        return (*scope(block),container)
    return None


def prepare(document):
    blocks = document.blocks
    output = []
    index = 0
    while index<len(blocks):
        end = index+4
        if end<=len(blocks) and choices(blocks[index:end],blocks[end] if end<len(blocks) else None):
            output.append(bind(blocks[index:end]))
            index = end
        else:
            block = blocks[index]
            end = index+1
            if owner:=literal_paragraph_owner(block):
                while (end<len(blocks) and literal_paragraph_owner(blocks[end])==owner
                       and blocks[end].locator.get('literal_paragraph_previous')==blocks[end-1].locator.get('element')):
                    end += 1
            if end>index+1:
                source,fragments = pieces(blocks[index:end],'\n')
                block = replace(block,source=source,locator={**block.locator,'source_fragments':fragments,
                                                              'inline_semantics':[]})
            output.append(block)
            index = end
    document.blocks = output
