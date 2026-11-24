"""Locate and verify a recoverable value in an immutable JSON source."""

import base64
import binascii
import json

from .core import Error


def load(data):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            multiline(key)
            if key in result:
                raise Error('source_json','The JSON source contains a repeated object key.')
            result[key] = value
        return result

    try:
        return json.loads(data,object_pairs_hook=unique)
    except json.JSONDecodeError as error:
        raise Error('source_json',f'The JSON source is invalid at line {error.lineno}, column {error.colno}.') from error
    except (UnicodeError,ValueError,RecursionError) as error:
        raise Error('source_json','The JSON source cannot be decoded within the reader limits.') from error


def multiline(value):
    if isinstance(value,list) and all(isinstance(line,str) for line in value):
        value = ''.join(value)
    if not isinstance(value,str):
        raise Error('source_json_text','Expected text or an ordered list of text lines.')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise Error('source_json_text','The text contains a value that cannot be represented as UTF-8.') from error
    return value


def value_at(document,path):
    try:
        for part in path:
            if isinstance(document,dict) and isinstance(part,str):
                document = document[part]
            elif isinstance(document,list) and type(part) is int and part>=0:
                document = document[part]
            else:
                raise KeyError(part)
    except (KeyError,IndexError,TypeError) as error:
        raise Error('source_location','The source has no matching embedded JSON value.') from error
    return document


def decode(value,encoding):
    text = multiline(value)
    if encoding=='utf-8':
        return text.encode()
    if encoding=='base64':
        try:
            return base64.b64decode(''.join(text.split()),validate=True)
        except (binascii.Error,ValueError) as error:
            raise Error('source_json_text','The embedded data is invalid base64.') from error
    raise Error('source_location','The source location has an unknown data encoding.')
