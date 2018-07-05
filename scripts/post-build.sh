#!/bin/bash
set -o errexit # Exit on error

usage() {
    echo "Usage: post-build.sh <build-dir> <config> <build-type> <build-options>"
}

if [ "$#" -ge 3 ]; then
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    . "$SCRIPT_DIR"/utils.sh

    BUILD_DIR="$(cd "$1" && pwd)"
    CONFIG="$2"
    PLATFORM="$(get-platform-from-config "$CONFIG")"
    COMPILER="$(get-compiler-from-config "$CONFIG")"
    ARCHITECTURE="$(get-architecture-from-config "$CONFIG")"
    BUILD_TYPE="$3"
    BUILD_OPTIONS="${*:4}"
    if [ -z "$BUILD_OPTIONS" ]; then
        BUILD_OPTIONS="$(get-build-options)" # use env vars (Jenkins)
    fi
elif [ -n "$BUILD_ID" ]; then # Jenkins
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    . "$SCRIPT_DIR"/utils.sh
    
    BUILD_DIR="$(cd "$WORKSPACE/../build" && pwd)"
    CONFIG="$CI_CONFIG"
    PLATFORM="$(get-platform-from-config "$CONFIG")"
    COMPILER="$(get-compiler-from-config "$CONFIG")"
    ARCHITECTURE="$(get-architecture-from-config "$CONFIG")"
    BUILD_TYPE="$CI_TYPE"
    BUILD_OPTIONS="$(get-build-options)" # use env vars (Jenkins)
else
    usage; exit 1
fi

# VM environment variables
echo "ENV VARS: load $SCRIPT_DIR/env/default"
. "$SCRIPT_DIR/env/default"
if [ -n "$NODE_NAME" ]; then
    if [ -e "$SCRIPT_DIR/env/$NODE_NAME" ]; then
        echo "ENV VARS: load node specific $SCRIPT_DIR/env/$NODE_NAME"
        . "$SCRIPT_DIR/env/$NODE_NAME"
    else
        echo "ERROR: No config file found for node $NODE_NAME."
        exit 1
    fi
fi

echo "--------------- post-build.sh vars ---------------"
echo "BUILD_DIR = $BUILD_DIR"
echo "CONFIG = $CONFIG"
echo "PLATFORM = $PLATFORM"
echo "COMPILER = $COMPILER"
echo "ARCHITECTURE = $ARCHITECTURE"
echo "BUILD_TYPE = $BUILD_TYPE"
echo "BUILD_OPTIONS = $BUILD_OPTIONS"
echo "--------------------------------------------------"

. "$SCRIPT_DIR"/dashboard.sh
. "$SCRIPT_DIR"/github.sh

load-env-vars "GITHUB" "$BUILD_DIR" # Retrieve GITHUB env vars used during build
load-env-vars "DASH" "$BUILD_DIR" # Retrieve DASH env vars used during build

echo "Dashboard env vars:"
env | grep "^DASH_"
echo "---------------------"
echo "GitHub env vars:"
env | grep "^GITHUB_"
echo "---------------------"

on-failure() {
    dashboard-notify "status=fail"
    github-notify "failure" "Build failed."
}

on-error() {
    dashboard-notify "status=fail"
    github-notify "error" "Unexpected error, see log for details."
}

on-aborted() {
    dashboard-notify "status=cancel"
    github-notify "failure" "Build canceled."
}

# Get build result from Groovy script output (Jenkins)
BUILD_RESULT="UNKNOWN"
if [ -e "$BUILD_DIR/build-result" ]; then
    BUILD_RESULT="$(cat $BUILD_DIR/build-result)"
fi
echo "BUILD_RESULT = $BUILD_RESULT"

case "$BUILD_RESULT" in
    FAILURE) on-failure;;
    ERROR) on-error;;
    ABORTED) on-aborted;;
esac


# Jenkins: remove link for Windows jobs (too long path problem)
if [ -n "$EXECUTOR_NUMBER" ]; then
    if vm-is-windows; then
        export BUILD_DIR_WINDOWS="$(cd "$BUILD_DIR" && pwd -W | sed 's#/#\\#g')"
        export BUILD_DIR_PARENT_WINDOWS="$(cd "$BUILD_DIR/.." && pwd -W | sed 's#/#\\#g')"
        cmd //c "if exist j:\build%EXECUTOR_NUMBER% rmdir j:\build%EXECUTOR_NUMBER%"
        cmd //c "if not exist %BUILD_DIR_WINDOWS%\parent_dir mklink /D %BUILD_DIR_WINDOWS%\parent_dir %BUILD_DIR_PARENT_WINDOWS%"
    else
        ln -sf "$(cd $BUILD_DIR/.. && pwd)" "$BUILD_DIR/parent_dir"
    fi
fi



