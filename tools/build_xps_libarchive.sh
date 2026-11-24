#!/bin/sh
# Build the archive dependency used by the resident XPS page renderer.
set -eu

if [ "$#" -ne 4 ]; then
    echo 'Usage: build_xps_libarchive.sh LIBARCHIVE_SOURCE DEBIAN_SYSROOT BUILD_DIR OUTPUT.so' >&2
    exit 2
fi

source_dir=$(realpath "$1")
sysroot=$(realpath "$2")
build_dir=$3
output=$4
libdir=x86_64-linux-gnu
flags="-B$sysroot/usr/lib/$libdir/ -isystem $sysroot/usr/include/$libdir"
flags="$flags -ffile-prefix-map=$source_dir=libarchive-3.8.11 -ffile-prefix-map=$sysroot=bookworm"

set -- -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=MinSizeRel
set -- "$@" -DCMAKE_C_STANDARD=11 -DCMAKE_C_STANDARD_REQUIRED=ON -DHAVE_ARC4RANDOM_BUF=0
for feature in ACL BZip2 CAT CPIO EXPAT LIBB2 LIBXML2 LZ4 LZMA LZO MBEDTLS MD NETTLE OPENSSL \
               PCRE2POSIX PCREPOSIX TAR TEST UNZIP XATTR ZSTD; do
    set -- "$@" "-DENABLE_$feature=OFF"
done

cmake -S "$source_dir" -B "$build_dir" "$@" \
    -DCMAKE_SYSROOT="$sysroot" "-DCMAKE_C_FLAGS=$flags" \
    "-DZLIB_LIBRARY=$sysroot/lib/$libdir/libz.so.1.2.13" \
    "-DZLIB_INCLUDE_DIR=$sysroot/usr/include"
cmake --build "$build_dir" --target archive
strip --strip-unneeded -o "$output" "$build_dir/libarchive/libarchive.so.13.8.11"
