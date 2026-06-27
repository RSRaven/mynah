@echo off
REM ============================================================================
REM Build whisper.cpp with the Vulkan backend on Windows x64.
REM
REM Prerequisites (one-time):
REM   * Visual Studio 2022 Build Tools with the C++ workload  (MSVC + bundled
REM     CMake + Ninja). vswhere finds it; this script uses its vcvars64 + CMake.
REM   * Vulkan SDK:  winget install KhronosGroup.VulkanSDK   (sets VULKAN_SDK)
REM   * The Vulkan *runtime* (vulkan-1.dll) ships with the GPU driver.
REM
REM Produces:  scripts/_artifacts/wcpp-src/build-vulkan/bin/{whisper-cli,whisper-server}.exe
REM            + ggml-vulkan.dll  (~74 MB total; no cuBLAS/cuDNN).
REM Point the app at it:  set MYNAH_WHISPERCPP_DIR=...\build-vulkan\bin
REM ============================================================================
setlocal
set "HERE=%~dp0"
set "SRC=%HERE%_artifacts\wcpp-src"
set "TAG=v1.9.1"

REM --- locate Visual Studio (Build Tools or full IDE) via vswhere ---
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" set "VSWHERE=C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * ^
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS=%%i"
if not defined VS echo ERROR: VS2022 with the C++ toolset not found & exit /b 1

set "CMAKE=%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "NINJA_DIR=%VS%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
if not defined VULKAN_SDK echo ERROR: VULKAN_SDK not set (install the Vulkan SDK) & exit /b 1

call "%VS%\VC\Auxiliary\Build\vcvars64.bat" || exit /b 1
set "PATH=%NINJA_DIR%;%VULKAN_SDK%\Bin;%PATH%"

REM --- get the source at the pinned tag (matches the CUDA build) ---
if not exist "%SRC%" (
    git clone --depth 1 --branch %TAG% https://github.com/ggml-org/whisper.cpp.git "%SRC%" || exit /b 1
)

cd /d "%SRC%" || exit /b 1
"%CMAKE%" -B build-vulkan -G Ninja ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_VULKAN=ON ^
  -DWHISPER_SDL2=OFF ^
  -DWHISPER_BUILD_TESTS=OFF ^
  -DWHISPER_BUILD_EXAMPLES=ON ^
  -DGGML_NATIVE=OFF || exit /b 1

"%CMAKE%" --build build-vulkan --config Release -j || exit /b 1
echo BUILD_OK  ^=^>  %SRC%\build-vulkan\bin
