"""Identify and recover embedded records by their stored byte ranges."""

from .core import Error, digest


def byte_asset(data,start,end,*,codec=None):
    asset = {'offset':start,'length':end-start,'sha256':digest(data[start:end])}
    if codec:
        asset['codec'] = codec
    return asset


def recover(data,asset):
    offset,length = asset.get('offset'),asset.get('length')
    if (type(offset) is not int or type(length) is not int or offset<0 or length<0
            or offset+length>len(data)):
        raise Error('source_location','The embedded asset byte range is outside its original document.')
    payload = data[offset:offset+length]
    if digest(payload)!=asset.get('sha256'):
        raise Error('snapshot_changed','The embedded asset does not match its recorded identity.')
    if asset.get('codec')=='rocket-zlib':
        from .rocket_intake import decode_chunks

        return decode_chunks(payload,'saved source record')
    if 'codec' in asset:
        raise Error('source_location','The saved embedded asset uses an unknown compression method.')
    return payload
