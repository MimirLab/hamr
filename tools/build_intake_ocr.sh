#!/bin/sh
# Run with CMake, Ninja, a C++ compiler, libpng-dev and zlib development files.
# Pass the unpacked Leptonica and Tesseract source directories and an owned build directory.
set -eu
leptonica=$1
tesseract=$2
build=$3
jobs=${HAMR_BUILD_JOBS:-4}
png=$(cc -print-file-name=libpng.a)
zlib=$(cc -print-file-name=libz.a)
test -r "$png" && test -r "$zlib"
mkdir -p "$build"
cmake -S "$leptonica" -B "$build/leptonica" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_INSTALL_PREFIX="$build/prefix" -DPNG_LIBRARY="$png" -DZLIB_LIBRARY="$zlib" \
  -DENABLE_GIF=OFF -DENABLE_JPEG=OFF -DENABLE_TIFF=OFF -DENABLE_WEBP=OFF -DENABLE_OPENJPEG=OFF
cmake --build "$build/leptonica" -j "$jobs"
cmake --install "$build/leptonica"
cmake -S "$tesseract" -B "$build/tesseract" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_PREFIX_PATH="$build/prefix" \
  -DDISABLE_TIFF=ON -DDISABLE_CURL=ON -DDISABLE_ARCHIVE=ON -DDISABLED_LEGACY_ENGINE=ON \
  -DOPENMP_BUILD=OFF -DGRAPHICS_DISABLED=ON -DBUILD_TRAINING_TOOLS=OFF -DENABLE_NATIVE=OFF \
  '-DCMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc'
cmake --build "$build/tesseract" --target tesseract -j "$jobs"
strip "$build/tesseract/bin/tesseract"
"$build/tesseract/bin/tesseract" --version
