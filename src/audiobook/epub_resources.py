"""Resolve one linear occurrence without turning manifest order into flow."""
from pathlib import PurePosixPath


def resolve_spine(identifier,nodes,paths,names):
    chain=[];seen=set()
    while identifier:
        if identifier in seen:return None,chain,'Cyclic EPUB manifest fallback chain.'
        seen.add(identifier);node=nodes.get(identifier);path=paths.get(identifier)
        chain.append({'idref':identifier,'path':path,'media_type':node.get('media-type') if node is not None else None})
        if node is None:return None,chain,'EPUB spine or fallback target is absent from its manifest.'
        if not path or path not in names:return None,chain,'EPUB spine or fallback resource is missing.'
        kind=node.get('media-type')
        if kind in ('application/xhtml+xml','image/svg+xml') or not kind and PurePosixPath(path).suffix.lower() in ('.xhtml','.html','.htm','.svg'):
            return path,chain,None
        identifier=node.get('fallback')
    return None,chain,'Unsupported linear EPUB resource has no supported local fallback.'
