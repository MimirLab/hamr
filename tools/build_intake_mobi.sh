#!/bin/sh
# Pass libmobi 0.12 source and an owned build directory.
set -eu
source=$1
build=$2
shift 2
if [ "$#" -eq 1 ]; then
  set -- "-DCMAKE_TOOLCHAIN_FILE=$1" '-DCMAKE_EXE_LINKER_FLAGS=-static -static-libgcc'
  strip=x86_64-w64-mingw32-strip
  suffix=.exe
else
  strip=strip
  suffix=
fi
cmake -S "$source" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF -DTOOLS_STATIC=ON -DUSE_ENCRYPTION=OFF \
  -DUSE_LIBXML2=OFF -DUSE_ZLIB=OFF "$@"
cmake --build "$build" --target mobitool -j "${HAMR_BUILD_JOBS:-4}"
"$strip" "$build/tools/mobitool$suffix"
