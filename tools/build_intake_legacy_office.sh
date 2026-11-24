#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
    printf 'Usage: %s SOURCE_ARCHIVES NEW_BUILD_DIRECTORY OUTPUT\n' "$0" >&2
    exit 2
fi

tools_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
archives=$(CDPATH= cd -- "$1" && pwd)
output=$(realpath -m "$3")
mkdir "$2"
build=$(CDPATH= cd -- "$2" && pwd)
prefix=$build/prefix
jobs=${HAMR_BUILD_JOBS:-4}

(
    cd "$archives"
    sha256sum -c <<'CHECKSUMS'
02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584  zlib-1.4.tar.gz
98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821  libxml2-2.15.4.tar.xz
52a65e904d255dbdd97a8b7bb28d6574e14f999eb01416aff004502406d0904d  librevenge_0.0.6.orig.tar.bz2
2465b0b662fdc5d4e3bebcdc9a79027713fb629ca2bff04a3c9251fdec42dd09  libwpd_0.10.3.orig.tar.xz
ac3590f691a2904eb8c7dc8b757b8a29f125f592449e421459ae8fa928b399eb  libmwaw_0.3.23.orig.tar.xz
b68ec10c23b8ac7abed8dfeb181b8592714c1e5bd33997ced0ee8bd6c4612bc5  libodfgen_0.1.9.orig.tar.xz
CHECKSUMS
)

cd "$build"
for archive in zlib-1.4.tar.gz libxml2-2.15.4.tar.xz librevenge_0.0.6.orig.tar.bz2 \
               libwpd_0.10.3.orig.tar.xz libmwaw_0.3.23.orig.tar.xz libodfgen_0.1.9.orig.tar.xz; do
    tar -xf "$archives/$archive"
done
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
export CFLAGS="-O2 -ffile-prefix-map=$build=/build"
export CXXFLAGS="$CFLAGS"

build_library() (
    cd "$1"
    shift
    ./configure --prefix="$prefix" --enable-static --disable-shared "$@"
    make -j "$jobs"
    make install
)

(
    cd zlib-1.4
    ./configure --prefix="$prefix" --static
    make -j "$jobs"
    make install
)
build_library libxml2-2.15.4 --without-python --without-icu --without-iconv \
    --without-lzma --without-zlib --without-catalog
build_library librevenge-0.0.6 --disable-tests --disable-tools
build_library libwpd-0.10.3 --without-docs
build_library libmwaw-0.3.23 --without-docs --disable-tools --disable-zip
build_library libodfgen-0.1.9 --without-docs

${CXX:-c++} $CXXFLAGS -std=c++17 -Wall -Wextra -Werror -static \
    "$tools_dir/intake_legacy_office.cc" \
    $(pkg-config --static --cflags --libs libwpd-0.10 libmwaw-0.3 librevenge-stream-0.0 libodfgen-0.1 libxml-2.0 zlib) \
    -o "$output"
${STRIP:-strip} --strip-unneeded "$output"
