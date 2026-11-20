"""An owned, terminable asset-transfer worker; activation stays in the parent."""
import http.client
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from .core import Error


def transfer(url,part,offset,size):
    request=urllib.request.Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
    with urllib.request.urlopen(request,timeout=30) as response:
        append=bool(offset and response.status==206)
        if response.status==206:
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            if not match:raise Error('download_identity','Range response has no exact byte interval.')
            first,last,total=map(int,match.groups())
            if first!=offset or total!=size or not first<=last<size:raise Error('download_identity','Range response does not match the pinned offset and size.')
            length=response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or int(length)!=last-first+1):raise Error('download_identity','Range response length conflicts with its interval.')
        elif response.status!=200:raise Error('download_identity','Asset server returned an unsupported transfer status.')
        # A server may ignore Range and send the whole object. Replace only
        # this owned partial, then revalidate the complete pinned identity.
        with Path(part).open('ab' if append else 'wb',buffering=0) as output:
            while chunk:=response.read1(1024*1024):
                output.write(chunk)
                if output.tell()>size:raise Error('download_size','Download exceeded pinned size.')
            os.fsync(output.fileno())


def main():
    try:
        url,part,offset,size=sys.argv[1:]
        transfer(url,part,int(offset),int(size))
    except Error as exc:
        print(json.dumps({'code':exc.code,'message':exc.message}));return 1
    except (OSError,http.client.HTTPException,ValueError) as exc:
        print(json.dumps({'code':'download_failed','message':f'Asset transfer failed ({type(exc).__name__}).'}));return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
