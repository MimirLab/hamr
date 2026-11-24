#!/bin/sh
set -eu
out=/build/win-ocr
prefix=$out/prefix
target=x86_64-w64-mingw32
mkdir -p "$out"
cat > "$out/mingw.cmake" <<EOF
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_C_COMPILER ${target}-gcc)
set(CMAKE_CXX_COMPILER ${target}-g++)
set(CMAKE_RC_COMPILER ${target}-windres)
set(CMAKE_FIND_ROOT_PATH $prefix /usr/$target)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
EOF
build() {
    source=$1 dir=$2
    shift 2
    cmake -S "$source" -B "$out/$dir" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$out/mingw.cmake" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_PREFIX_PATH="$prefix" -DZLIB_LIBRARY="$prefix/lib/libzs.a" -DZLIB_INCLUDE_DIR="$prefix/include" \
        -DBUILD_SHARED_LIBS=OFF "$@"
    cmake --build "$out/$dir" -j "${HAMR_BUILD_JOBS:-4}"
    cmake --install "$out/$dir"
}
build /upstream/zlib-1.3.2 zlib -DZLIB_BUILD_TESTING=OFF -DZLIB_BUILD_SHARED=OFF
build /upstream/libpng-1.6.59 png -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_TESTS=OFF -DPNG_TOOLS=OFF
build /upstream/leptonica-1.87.0 lept -DSW_BUILD=OFF -DENABLE_GIF=OFF -DENABLE_JPEG=OFF \
    -DENABLE_TIFF=OFF -DENABLE_WEBP=OFF -DENABLE_OPENJPEG=OFF
# MinGW uses lowercase import-library names on case-sensitive build hosts.
if [ ! -d "$out/tesseract-source" ]; then
    cp -a /upstream/tesseract-5.5.1 "$out/tesseract-source"
    sed -i 's/set(LIB_Ws2_32 Ws2_32)/set(LIB_Ws2_32 ws2_32)/' "$out/tesseract-source/CMakeLists.txt"
fi
cmake -S "$out/tesseract-source" -B "$out/tess-portable" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$out/mingw.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$prefix" -DBUILD_SHARED_LIBS=OFF -DSW_BUILD=OFF \
    -DDISABLE_TIFF=ON -DDISABLE_CURL=ON -DDISABLE_ARCHIVE=ON -DDISABLED_LEGACY_ENGINE=ON \
    -DOPENMP_BUILD=OFF -DGRAPHICS_DISABLED=ON -DBUILD_TRAINING_TOOLS=OFF -DENABLE_NATIVE=OFF \
    '-DCMAKE_EXE_LINKER_FLAGS=-static -static-libstdc++ -static-libgcc'
cmake --build "$out/tess-portable" --target tesseract -j "${HAMR_BUILD_JOBS:-4}"
"$target-strip" "$out/tess-portable/bin/tesseract.exe"
"$target-objdump" -p "$out/tess-portable/bin/tesseract.exe" | grep 'DLL Name'
