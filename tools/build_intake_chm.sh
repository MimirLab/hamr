#!/bin/sh
set -eu
if [ "$#" -ne 2 ]; then
    printf 'Usage: %s VERIFIED_CHMLIB_SOURCE OUTPUT\n' "$0" >&2
    exit 2
fi
project=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
library=$1/src
"${CC:-cc}" -O2 -std=c11 -D_DEFAULT_SOURCE -D_FILE_OFFSET_BITS=64 -I"$library" \
    "$project/intake_chm.c" "$library/chm_lib.c" "$library/lzx.c" -o "$2"
"${STRIP:-strip}" "$2"
