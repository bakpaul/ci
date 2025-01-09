#!/bin/bash
set -o errexit # Exit on error

# This script basically runs 'make' and saves the compilation output
# in make-output.txt.

## Significant environnement variables:
# - VM_MAKE_OPTIONS       # additional arguments to pass to make
# - ARCHITECTURE               # x86|amd64  (32-bit or 64-bit build - Windows-specific)
# - COMPILER           # important for Visual Studio (vs-2012, vs-2013 or vs-2015)


### Checks

usage() {
    echo "Usage: compile.sh <src-dir> <build-dir> <config> <build-options>"
}

if [ "$#" -ge 3 ]; then
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    . "$SCRIPT_DIR"/utils.sh

    SRC_DIR="$(cd "$1" && pwd)"
    if vm-is-windows; then
        SRC_DIR="$(cd $1 && pwd -W)"
    fi
    BUILD_DIR="$(cd "$2" && pwd)"
    CONFIG="$3"
    PLATFORM="$(get-platform-from-config "$CONFIG")"
    COMPILER="$(get-compiler-from-config "$CONFIG")"
    ARCHITECTURE="$(get-architecture-from-config "$CONFIG")"
    BUILD_OPTIONS="${*:4}"
    if [ -z "$BUILD_OPTIONS" ]; then
        BUILD_OPTIONS="$(get-build-options)" # use env vars (Jenkins)
    fi
else
    usage; exit 1
fi

if [[ ! -e "$BUILD_DIR/CMakeCache.txt" ]]; then
    echo "Error: '$BUILD_DIR' does not look like a build directory."
    usage; exit 1
fi

echo "--------------- compile.sh vars ---------------"
echo "BUILD_DIR = $BUILD_DIR"
echo "CONFIG = $CONFIG"
echo "PLATFORM = $PLATFORM"
echo "COMPILER = $COMPILER"
echo "ARCHITECTURE = $ARCHITECTURE"
echo "-----------------------------------------------"

# The output of make is saved to a file, to check for warnings later. Since make
# is inside a pipe, errors will go undetected, thus we create a file
# 'make-failed' when make fails, to check for errors.
rm -f "$BUILD_DIR/make-failed"

( call-make "$BUILD_DIR" "all" 2>&1 || touch "$BUILD_DIR/make-failed" ) | tee "$BUILD_DIR/make-output.txt"

if in-array "build-release-package" "$BUILD_OPTIONS"; then
    echo "-------------- Start Stubfiles generation --------------" | tee -a "$BUILD_DIR/make-output.txt"
    export SOFA_ROOT="$BUILD_DIR"
    if vm-is-windows; then
        # Avoid "libpython3X not found"
        if [ -e "$VM_PYTHON3_EXECUTABLE" ]; then
            pythonroot="$(dirname $VM_PYTHON3_EXECUTABLE)"
            pythonroot="$(cd "$pythonroot" && pwd)"
            export PATH="$pythonroot:$pythonroot/DLLs:$pythonroot/Lib:$PATH_RESET"
        fi
        PYTHON_SCRIPT_DIR=$(cd "$SRC_DIR/applications/plugins/SofaPython3/scripts" && pwd -W )
        PYTHON_SITE_PACKAGE_DIR=$(cd "$BUILD_DIR/lib/python3/site-packages" && pwd -W )
        export PYTHONPATH="$PYTHON_SITE_PACKAGE_DIR:$PYTHONPATH"

    else
        PYTHON_SCRIPT_DIR=$(cd "$SRC_DIR/applications/plugins/SofaPython3/scripts" && pwd )
        PYTHON_SITE_PACKAGE_DIR=$(cd "$BUILD_DIR/lib/python3/site-packages" && pwd )
        export PYTHONPATH="$PYTHON_SITE_PACKAGE_DIR:$PYTHONPATH"
    fi

    python_exe="$(find-python)"
    if [ -n "$python_exe" ]; then
        echo "Launching the stub generation with '$python_exe ${PYTHON_SCRIPT_DIR}/generate_stubs.py -d $PYTHON_SITE_PACKAGE_DIR -m Sofa --use_pybind11'" | tee -a "$BUILD_DIR/make-output.txt"
        $python_exe "${PYTHON_SCRIPT_DIR}/generate_stubs.py" -d "$PYTHON_SITE_PACKAGE_DIR" -m Sofa --use_pybind11 | tee -a "$BUILD_DIR/make-output.txt"
    fi
    echo "--------------- End Stubfiles generation ---------------" | tee -a "$BUILD_DIR/make-output.txt"
    echo "-------------- Start packaging --------------" | tee -a "$BUILD_DIR/make-output.txt"
    ( call-make "$BUILD_DIR" "package" 2>&1 || touch "$BUILD_DIR/make-failed" ) | tee -a "$BUILD_DIR/make-output.txt"
    echo "--------------- End packaging ---------------" | tee -a "$BUILD_DIR/make-output.txt"
fi

if [ -e "$BUILD_DIR/make-failed" ]; then
    echo "ERROR: Detected $BUILD_DIR/make-failed"
    echo "       See $BUILD_DIR/make-output.txt"
    exit 1
fi
