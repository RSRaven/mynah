#!/usr/bin/env bash
# ============================================================================
# Build whisper.cpp with the Metal backend on macOS (Apple Silicon, arm64).
#
# Prerequisites (one-time):
#   * Xcode command-line tools  (clang + the Metal toolchain): xcode-select --install
#   * CMake:  brew install cmake
#
# Produces:  scripts/_artifacts/wcpp-src/build-metal/bin/
#              whisper-server, libwhisper.dylib, libggml*.dylib  (Metal embedded; ~small).
# Point the app at it:  export MYNAH_WHISPERCPP_DIR=.../build-metal/bin
#
# GGML_METAL_EMBED_LIBRARY=ON matters: it embeds default.metallib into the lib so the pack is
# relocatable (no loose .metal shader file to carry and locate at runtime).
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/_artifacts/wcpp-src"
TAG="v1.9.1"
BUILD="$SRC/build-metal"
BIN="$BUILD/bin"

# --- get the source at the pinned tag (matches the Vulkan/CUDA builds) ---
if [ ! -d "$SRC" ]; then
  git clone --depth 1 --branch "$TAG" https://github.com/ggml-org/whisper.cpp.git "$SRC"
fi

# --- configure + build ---
cmake -S "$SRC" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_METAL=ON \
  -DGGML_METAL_EMBED_LIBRARY=ON \
  -DWHISPER_BUILD_SERVER=ON \
  -DBUILD_SHARED_LIBS=ON \
  -DWHISPER_SDL2=OFF \
  -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON \
  -DGGML_NATIVE=OFF

cmake --build "$BUILD" -j --config Release

# --- make the dylibs relocatable ---------------------------------------------------------
# The in-process LID + VAD (whispercpp_native.py) dlopen libwhisper.dylib from an arbitrary
# install dir; dyld must still find its sibling libggml*.dylib. CMake stamps the build's
# absolute @rpath, which won't exist on a user's machine. Rewrite every inter-dylib dep to
# @loader_path/<name> so the shipped pack is self-contained wherever it lands.
echo "Rewriting dylib install names to @loader_path…"
shopt -s nullglob
for dylib in "$BIN"/*.dylib; do
  base="$(basename "$dylib")"
  # Set the library's own install id to a bare @rpath/<name> (consumers resolve via their
  # own @loader_path), then rewrite each dependency that points at a sibling dylib.
  install_name_tool -id "@rpath/$base" "$dylib" 2>/dev/null || true
  while IFS= read -r dep; do
    depbase="$(basename "$dep")"
    case "$depbase" in
      libwhisper*.dylib|libggml*.dylib)
        install_name_tool -change "$dep" "@loader_path/$depbase" "$dylib" 2>/dev/null || true
        ;;
    esac
  done < <(otool -L "$dylib" | tail -n +2 | awk '{print $1}')
done

# whisper-server resolves its dylibs via @rpath; add @loader_path so it finds the siblings
# next to the binary regardless of where the pack is installed.
if [ -f "$BIN/whisper-server" ]; then
  install_name_tool -add_rpath "@loader_path" "$BIN/whisper-server" 2>/dev/null || true
  for dep in $(otool -L "$BIN/whisper-server" | tail -n +2 | awk '{print $1}'); do
    depbase="$(basename "$dep")"
    case "$depbase" in
      libwhisper*.dylib|libggml*.dylib)
        install_name_tool -change "$dep" "@loader_path/$depbase" "$BIN/whisper-server" 2>/dev/null || true
        ;;
    esac
  done
fi

echo "BUILD_OK  =>  $BIN"
ls -la "$BIN"/whisper-server "$BIN"/*.dylib 2>/dev/null || true
