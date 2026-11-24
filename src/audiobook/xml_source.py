"""Recover an embedded binary value from its original XML element."""

import base64
import binascii

from .core import Error, digest


def indices(node):
    matches = []
    while (parent:=node.getparent()) is not None:
        matches.append(parent.index(node))
        node = parent
    return matches[::-1]


def payload(node):
    if len(node) or not node.text:
        raise Error('source_xml_asset','The embedded XML value has no readable binary data.')
    try:
        return base64.b64decode(''.join(node.text.split()),validate=True)
    except (ValueError,binascii.Error) as error:
        raise Error('source_xml_asset','The embedded XML binary data is invalid.') from error


def describe(node,data):
    return {'indices':indices(node),'tag':node.tag,'sha256':digest(data)}


def extract(root,location):
    steps = location.get('indices')
    if not isinstance(steps,list) or not all(type(index) is int and index>=0 for index in steps):
        raise Error('source_location','The binary XML location is invalid.')
    try:
        for index in steps:
            root = root[index]
    except IndexError as error:
        raise Error('source_location','The original XML has no matching binary value.') from error
    if root.tag!=location.get('tag'):
        raise Error('source_location','The original XML element does not match the binary location.')
    data = payload(root)
    if digest(data)!=location.get('sha256'):
        raise Error('snapshot_changed','The embedded XML data does not match its recorded identity.')
    return data
