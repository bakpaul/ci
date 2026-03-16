#!/usr/bin/env python3

import os
import sys
import subprocess
import re
import shutil
import glob
import time
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

# Global variables
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_RESET = os.environ.get("PATH", "")

# Command line arguments
command = None
build_dir = None
src_dir = None
output_dir = None
MAX_PARALLEL_TESTS = 1

def usage():
    """Print usage information."""
    print("Usage: scene-tests.py [run|count-warnings|count-errors|print-summary] <build-dir> <src-dir> <output_dir> <max_parallel-tests>")

def filter_out_comments(text):
    """Remove comments from text."""
    return re.sub(r'#.*', '', text)

def remove_leading_blanks(text):
    """Remove leading blanks from text."""
    return re.sub(r'^\s*', '', text)

def remove_trailing_blanks(text):
    """Remove trailing blanks from text."""
    return re.sub(r'\s*$', '', text)

def delete_blank_lines(text):
    """Delete blank lines from text."""
    return '\n'.join(line for line in text.split('\n') if line.strip())

def clean_line(text):
    """Clean a line by removing comments, leading/trailing blanks, and blank lines."""
    return delete_blank_lines(remove_trailing_blanks(remove_leading_blanks(filter_out_comments(text))))

def log(message):
    """Log a message to stderr and to the log file."""
    log_file = os.path.join(output_dir, "log.txt")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "a") as f:
        f.write(message + "\n")
    sys.stderr.write(message + "\n")

def option_is_well_formed(line):
    """Check if an option line is well-formed."""
    pattern = r'^[^\s]+(\s+"[^"]*")+$'
    return bool(re.match(pattern, line.strip()))

def option_split_args(line):
    """Split arguments from an option line."""
    args = []
    rest = line.strip()
    while rest:
        match = re.match(r'^"([^"]*)"[\s]*(.*)', rest)
        if match:
            arg = match.group(1)
            rest = match.group(2)
            args.append(arg)
        else:
            break
    return args

def get_args(line):
    """Get arguments from an option line."""
    return re.sub(r'^[^\s]+[\s]+', '', line.strip())

def get_option(line):
    """Get the option from an option line."""
    return re.sub(r'[\s].*', '', line.strip())

def get_arg(args, index):
    """Get the nth argument from an option line."""
    split_args = option_split_args(args)
    if index <= len(split_args):
        return split_args[index - 1]
    return None

def count_args(args):
    """Count the number of arguments in an option line."""
    return len(option_split_args(args))

def list_scenes(directory):
    """List all scenes in a directory."""
    scenes_scn = []
    scenes_pyscn = []
    scenes_py = []

    # Find .scn files
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.scn'):
                rel_path = os.path.relpath(os.path.join(root, file), directory)
                scenes_scn.append(rel_path)

    # Filter out .scn files from .pyscn and .py searches
    scenes_scn_grep = '|'.join(os.path.splitext(scene)[0] for scene in scenes_scn)

    # Find .pyscn files
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.pyscn'):
                rel_path = os.path.relpath(os.path.join(root, file), directory)
                if not re.search(scenes_scn_grep, rel_path):
                    scenes_pyscn.append(rel_path)

    # Find .py files
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                rel_path = os.path.relpath(os.path.join(root, file), directory)
                if not re.search(scenes_scn_grep, rel_path):
                    scenes_py.append(rel_path)

    # Combine and sort
    all_scenes = scenes_scn + scenes_pyscn + scenes_py
    return sorted(set(all_scenes))

def get_lib(lib_name):
    """Get the path to a library."""
    paths = []

    # Search in build_dir/lib/
    lib_dir = os.path.join(build_dir, "lib")
    if os.path.exists(lib_dir):
        for pattern in [f"lib{lib_name}.dylib", f"lib{lib_name}.so", f"lib{lib_name}.lib", f"lib{lib_name}.dll", f"{lib_name}.dylib", f"{lib_name}.so", f"{lib_name}.lib", f"{lib_name}.dll"]:
            for file in glob.glob(os.path.join(lib_dir, pattern)):
                paths.append(file)

    # Search in SOFA_PLUGIN_PATH
    sofa_plugin_path = os.environ.get("SOFA_PLUGIN_PATH", "")
    for directory in sofa_plugin_path.replace(":", " ").replace(";", " ").split():
        if os.path.exists(directory):
            for pattern in [f"lib{lib_name}.dylib", f"lib{lib_name}.so", f"lib{lib_name}.lib", f"lib{lib_name}.dll", f"{lib_name}.dylib", f"{lib_name}.so", f"{lib_name}.lib", f"{lib_name}.dll"]:
                for file in glob.glob(os.path.join(directory, pattern)):
                    paths.append(file)

    return ' '.join(paths) if paths else None

def list_plugins():
    """List all plugins."""
    plugins = []

    # Search in src_dir/applications/plugins
    plugins_dir = os.path.join(src_dir, "applications", "plugins")
    if os.path.exists(plugins_dir):
        for plugin in os.listdir(plugins_dir):
            if os.path.exists(os.path.join(plugins_dir, plugin, "CMakeLists.txt")):
                plugins.append(plugin)

    # Search in build_dir/external_directories/fetched
    fetched_dir = os.path.join(build_dir, "external_directories", "fetched")
    if os.path.exists(fetched_dir):
        for plugin in os.listdir(fetched_dir):
            if not plugin.endswith("-temp"):
                plugins.append(plugin)

    return plugins

def list_scene_directories():
    """List all scene directories."""
    directories = []

    # Add examples directory
    examples_dir = os.path.join(src_dir, "examples")
    if os.path.exists(examples_dir):
        os.makedirs(os.path.join(output_dir, "examples"), exist_ok=True)
        directories.append(examples_dir)

    # Add share directory
    share_dir = os.path.join(src_dir, "share")
    if os.path.exists(share_dir):
        os.makedirs(os.path.join(output_dir, "share"), exist_ok=True)
        directories.append(share_dir)

    # List directories for compiled plugins only
    for plugin in list_plugins():
        lib = get_lib(plugin)
        if lib:
            log(f"Plugin {plugin}: built (found {lib})")
            scene_dir_found = False

            # Check various possible directories for scenes
            for scene_dir in [
                os.path.join(src_dir, "applications", "plugins", plugin, "examples"),
                os.path.join(src_dir, "applications", "plugins", plugin, "scenes"),
                os.path.join(build_dir, "external_directories", "fetched", plugin, "examples"),
                os.path.join(build_dir, "external_directories", "fetched", plugin, "scenes")
            ]:
                if os.path.exists(scene_dir):
                    log(f"Plugin {plugin}: examples/ or scenes/ directory found.")
                    rel_dir = os.path.relpath(scene_dir, src_dir)
                    os.makedirs(os.path.join(output_dir, rel_dir), exist_ok=True)
                    directories.append(scene_dir)
                    scene_dir_found = True

            if not scene_dir_found:
                log(f"Plugin {plugin}: no examples/ nor scenes/ directories.")
        else:
            log(f"Plugin {plugin}: not built")

    return directories

def get_output_relative_dir(path):
    """Get the relative directory for output."""
    if path.startswith(src_dir):
        return path[len(src_dir):].lstrip('/')
    else:
        return f"applications/plugins/{path[len(os.path.join(build_dir, 'external_directories', 'fetched')):].lstrip('/')}"

def create_directories():
    """Create the directory structure for scene testing."""
    directories = list_scene_directories()

    with open(os.path.join(output_dir, "directories.txt"), "w") as f:
        for directory in directories:
            f.write(directory + "\n")

    for path in directories:
        subpath = get_output_relative_dir(path)
        ignore_patterns_file = os.path.join(output_dir, subpath, "ignore-patterns.txt")
        add_patterns_file = os.path.join(output_dir, subpath, "add-patterns.txt")

        with open(ignore_patterns_file, "w") as f:
            pass
        with open(add_patterns_file, "w") as f:
            pass

        scenes = list_scenes(path)
        scenes_file = os.path.join(output_dir, subpath, "scenes.txt")
        with open(scenes_file, "w") as f:
            for scene in scenes:
                f.write(scene + "\n")

        for scene in scenes:
            scene_dir = os.path.join(output_dir, subpath, scene)
            os.makedirs(scene_dir, exist_ok=True)

            # Set default timeout
            timeout_file = os.path.join(scene_dir, "timeout.txt")
            with open(timeout_file, "w") as f:
                if os.environ.get("CI_TYPE") == "Debug":
                    f.write("300")  # Default debug timeout, in seconds
                else:
                    f.write("30")  # Default release timeout, in seconds

            # Set default iterations
            iterations_file = os.path.join(scene_dir, "iterations.txt")
            with open(iterations_file, "w") as f:
                f.write("100")  # Default number of iterations

            # Add to all-scenes.txt
            with open(os.path.join(output_dir, "all-scenes.txt"), "a") as f:
                f.write(os.path.join(path, scene) + "\n")

def parse_options_files():
    """Parse .scene-tests files for options."""
    directories = []
    with open(os.path.join(output_dir, "directories.txt"), "r") as f:
        directories = f.read().splitlines()

    for path in directories:
        subpath = get_output_relative_dir(path)
        scene_tests_file = os.path.join(path, ".scene-tests")

        if os.path.exists(scene_tests_file):
            with open(scene_tests_file, "r") as f:
                lines = f.readlines()

            for line in lines:
                cleaned_line = clean_line(line)
                if not cleaned_line:
                    continue

                if option_is_well_formed(cleaned_line):
                    option = get_option(cleaned_line)
                    args = get_args(cleaned_line)

                    if option == "ignore":
                        if count_args(args) == 1:
                            arg = get_arg(args, 1)
                            with open(os.path.join(output_dir, subpath, "ignore-patterns.txt"), "a") as f:
                                f.write(arg + "\n")
                        else:
                            log(f"{scene_tests_file}: warning: 'ignore' expects one argument: ignore <pattern>")

                    elif option == "add":
                        if count_args(args) == 1:
                            scene = get_arg(args, 1)
                            with open(os.path.join(output_dir, subpath, "add-patterns.txt"), "a") as f:
                                f.write(scene + "\n")

                            scene_dir = os.path.join(output_dir, subpath, scene)
                            os.makedirs(scene_dir, exist_ok=True)

                            # Set default timeout
                            timeout_file = os.path.join(scene_dir, "timeout.txt")
                            with open(timeout_file, "w") as f:
                                if os.environ.get("CI_TYPE") == "Debug":
                                    f.write("300")  # Default debug timeout, in seconds
                                else:
                                    f.write("30")  # Default release timeout, in seconds

                            # Set default iterations
                            iterations_file = os.path.join(scene_dir, "iterations.txt")
                            with open(iterations_file, "w") as f:
                                f.write("100")  # Default number of iterations
                        else:
                            log(f"{scene_tests_file}: warning: 'add' expects one argument: add <pattern>")

                    elif option == "timeout":
                        if count_args(args) == 2:
                            scene = get_arg(args, 1)
                            scene_path = os.path.join(path, scene)
                            if os.path.exists(scene_path):
                                timeout = get_arg(args, 2)
                                scene_dir = os.path.join(output_dir, subpath, scene)
                                os.makedirs(scene_dir, exist_ok=True)
                                with open(os.path.join(scene_dir, "timeout.txt"), "w") as f:
                                    f.write(timeout)
                            else:
                                log(f"{scene_tests_file}: warning: no such file: {scene}")
                        else:
                            log(f"{scene_tests_file}: warning: 'timeout' expects two arguments: timeout <file> <timeout>")

                    elif option == "iterations":
                        if count_args(args) == 2:
                            scene = get_arg(args, 1)
                            scene_path = os.path.join(path, scene)
                            if os.path.exists(scene_path):
                                iterations = get_arg(args, 2)
                                scene_dir = os.path.join(output_dir, subpath, scene)
                                os.makedirs(scene_dir, exist_ok=True)
                                with open(os.path.join(scene_dir, "iterations.txt"), "w") as f:
                                    f.write(iterations)
                            else:
                                log(f"{scene_tests_file}: warning: no such file: {scene}")
                        else:
                            log(f"{scene_tests_file}: warning: 'iterations' expects two arguments: iterations <file> <number>")

                    else:
                        log(f"{scene_tests_file}: warning: unknown option: {option}")
                else:
                    log(f"{scene_tests_file}: warning: ill-formed line: {line}")

    # Process ignored and added scenes
    for path in directories:
        subpath = get_output_relative_dir(path)
        ignore_patterns_file = os.path.join(output_dir, subpath, "ignore-patterns.txt")
        add_patterns_file = os.path.join(output_dir, subpath, "add-patterns.txt")
        scenes_file = os.path.join(output_dir, subpath, "scenes.txt")
        ignored_scenes_file = os.path.join(output_dir, subpath, "ignored-scenes.txt")
        tested_scenes_file = os.path.join(output_dir, subpath, "tested-scenes.txt")
        added_scenes_file = os.path.join(output_dir, subpath, "added-scenes.txt")

        # Ensure all files exist
        for f in [ignored_scenes_file, tested_scenes_file, added_scenes_file]:
            if not os.path.exists(f):
                with open(f, "w") as file:
                    pass

        # Filter ignored scenes
        with open(ignored_scenes_file, "w") as f:
            if os.path.exists(ignore_patterns_file) and os.path.getsize(ignore_patterns_file) > 0:
                with open(ignore_patterns_file, "r") as ignore_f:
                    ignore_patterns = ignore_f.read().splitlines()
                with open(scenes_file, "r") as scenes_f:
                    scenes = scenes_f.read().splitlines()
                for scene in scenes:
                    for pattern in ignore_patterns:
                        if re.search(pattern, scene):
                            f.write(scene + "\n")
                            break

        # Create tested-scenes.txt
        with open(tested_scenes_file, "w") as f:
            if os.path.exists(ignore_patterns_file) and os.path.getsize(ignore_patterns_file) > 0:
                with open(ignore_patterns_file, "r") as ignore_f:
                    ignore_patterns = ignore_f.read().splitlines()
                with open(scenes_file, "r") as scenes_f:
                    scenes = scenes_f.read().splitlines()
                for scene in scenes:
                    if not any(re.search(pattern, scene) for pattern in ignore_patterns):
                        f.write(scene + "\n")
            else:
                with open(scenes_file, "r") as scenes_f:
                    f.write(scenes_f.read())

        # Add scenes
        if os.path.exists(add_patterns_file) and os.path.getsize(add_patterns_file) > 0:
            with open(add_patterns_file, "r") as add_f:
                added_scenes = add_f.read().splitlines()
            with open(added_scenes_file, "w") as f:
                f.write('\n'.join(added_scenes) + "\n")
            with open(tested_scenes_file, "a") as f:
                f.write('\n'.join(added_scenes) + "\n")
            with open(scenes_file, "a") as f:
                f.write('\n'.join(added_scenes) + "\n")

        # Update all-ignored-scenes.txt and all-added-scenes.txt
        if os.path.exists(ignored_scenes_file):
            with open(os.path.join(output_dir, "all-ignored-scenes.txt"), "a") as f:
                with open(ignored_scenes_file, "r") as ignored_f:
                    for scene in ignored_f.read().splitlines():
                        f.write(os.path.join(path, scene) + "\n")

        if os.path.exists(added_scenes_file):
            with open(os.path.join(output_dir, "all-added-scenes.txt"), "a") as f:
                with open(added_scenes_file, "r") as added_f:
                    for scene in added_f.read().splitlines():
                        f.write(os.path.join(path, scene) + "\n")

        if os.path.exists(tested_scenes_file):
            with open(os.path.join(output_dir, "all-tested-scenes.txt"), "a") as f:
                with open(tested_scenes_file, "r") as tested_f:
                    for scene in tested_f.read().splitlines():
                        f.write(os.path.join(path, scene) + "\n")

    # Clean output files
    for filename in ["all-ignored-scenes.txt", "all-added-scenes.txt", "all-tested-scenes.txt"]:
        if os.path.exists(os.path.join(output_dir, filename)):
            with open(os.path.join(output_dir, filename), "r") as f:
                lines = f.read().splitlines()
            lines = [line for line in lines if '.' in line]
            lines = sorted(set(lines))
            with open(os.path.join(output_dir, filename), "w") as f:
                f.write('\n'.join(lines) + "\n")

def ignore_scenes_with_deprecated_components():
    """Ignore scenes with deprecated components."""
    log("Searching for deprecated components...")
    get_deprecated_components = None
    for pattern in ["getDeprecatedComponents", "getDeprecatedComponentsd", "getDeprecatedComponents_d"]:
        for file in glob.glob(os.path.join(build_dir, "bin", pattern)):
            get_deprecated_components = file
            break
        if get_deprecated_components:
            break

    if not get_deprecated_components:
        log("getDeprecatedComponents not found.")
        return

    result = subprocess.run([get_deprecated_components], capture_output=True, text=True)
    deprecated_components = result.stdout.splitlines()

    with open(os.path.join(output_dir, "deprecatedcomponents.txt"), "w") as f:
        f.write('\n'.join(deprecated_components) + "\n")

    for component in deprecated_components:
        component = component.strip()
        grep_result = subprocess.run(
            ["grep", "-r", component, "--include=*.{scn,py,pyscn}"],
            capture_output=True,
            text=True,
            cwd=src_dir
        )
        scenes = grep_result.stdout.splitlines()
        scenes = [line.split(':')[0] for line in scenes]
        scenes = sorted(set(scenes))

        for scene in scenes:
            if os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
                with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
                    tested_scenes = f.read().splitlines()
                if scene in tested_scenes:
                    tested_scenes = [s for s in tested_scenes if s != scene]
                    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "w") as f:
                        f.write('\n'.join(tested_scenes) + "\n")

                    if not os.path.exists(os.path.join(output_dir, "all-ignored-scenes.txt")) or scene not in open(os.path.join(output_dir, "all-ignored-scenes.txt")).read():
                        log(f"  ignore {scene}: deprecated component \"{component}\"")
                        with open(os.path.join(output_dir, "all-ignored-scenes.txt"), "a") as f:
                            f.write(scene + "\n")

    log("Searching for deprecated components: done.")

def ignore_scenes_with_missing_plugins():
    """Ignore scenes with missing plugins."""
    log("Searching for missing plugins...")

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    for scene in tested_scenes:
        if not os.path.exists(scene):
            continue
            
        try:
            with open(scene, "r") as f:
                content = f.read()
                if 'RequiredPlugin' not in content:
                    continue
        except Exception as e:
            log(f"  Warning: could not read {scene}: {e}")
            continue

        with open(scene, "r") as f:
            lines = f.readlines()

        for line in lines:
            if 'RequiredPlugin' in line:
                plugin_match = None
                if 'pluginName' in line:
                    plugin_match = re.search(r'pluginName[\s]*=[\s]*[\'"]([^\'\"]*)[\'\"]', line)
                elif 'name' in line:
                    plugin_match = re.search(r'name[\s]*=[\s]*[\'"]([^\'\"]*)[\'\"]', line)
                
                if plugin_match:
                    plugin = plugin_match.group(1)
                else:
                    log(f"  Warning: unknown RequiredPlugin found in {scene}")
                    break

                lib = get_lib(plugin)
                if not lib:
                    if scene in tested_scenes:
                        tested_scenes = [s for s in tested_scenes if s != scene]
                        with open(os.path.join(output_dir, "all-tested-scenes.txt"), "w") as f:
                            f.write('\n'.join(tested_scenes) + "\n")

                        if not os.path.exists(os.path.join(output_dir, "all-ignored-scenes.txt")) or scene not in open(os.path.join(output_dir, "all-ignored-scenes.txt")).read():
                            log(f"  ignore {scene}: missing plugin \"{plugin}\"")
                            with open(os.path.join(output_dir, "all-ignored-scenes.txt"), "a") as f:
                                f.write(scene + "\n")

    log("Searching for missing plugins: done.")

def ignore_scenes_python_without_createscene():
    """Ignore Python scenes without createScene function."""
    log("Searching for unwanted python scripts...")

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    for scene in tested_scenes:
        if scene.endswith('.py'):
            if not os.path.exists(scene):
                continue
                
            try:
                with open(scene, "r") as f:
                    content = f.read()
                    if 'def createScene' not in content:
                        tested_scenes = [s for s in tested_scenes if s != scene]
                        with open(os.path.join(output_dir, "all-tested-scenes.txt"), "w") as f:
                            f.write('\n'.join(tested_scenes) + "\n")

                        if not os.path.exists(os.path.join(output_dir, "all-ignored-scenes.txt")) or scene not in open(os.path.join(output_dir, "all-ignored-scenes.txt")).read():
                            log(f"  ignore {scene}: createScene function not found.")
                            with open(os.path.join(output_dir, "all-ignored-scenes.txt"), "a") as f:
                                f.write(scene + "\n")
            except Exception as e:
                log(f"  Warning: could not read {scene}: {e}")

    log("Searching for unwanted python scripts: done.")

def initialize_scene_tests():
    """Initialize scene testing."""
    log("Initializing scene testing.")
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "reports"), exist_ok=True)

    run_sofa = None
    for pattern in ["runSofa", "runSofad", "runSofa_d"]:
        for file in glob.glob(os.path.join(build_dir, "bin", pattern)):
            run_sofa = file
            break
        if run_sofa:
            break

    if run_sofa and (os.access(run_sofa, os.X_OK) or os.path.islink(run_sofa)):
        log(f"Found runSofa: {run_sofa}")
    else:
        log("Error: could not find runSofa.")
        sys.exit(1)

    # Create empty report files
    for report in ["successes.txt", "warnings.txt", "errors.txt", "crashes.txt"]:
        with open(os.path.join(output_dir, "reports", report), "w") as f:
            pass

    create_directories()
    parse_options_files()

def do_test_all_scenes(tested_scenes, thread_num):
    """Test all scenes in a thread."""
    tested_scenes_count = len(tested_scenes)
    current_scene_count = 0

    for scene in tested_scenes:
        current_scene_count += 1
        subpath = get_output_relative_dir(scene)

        iterations = 100  # Default
        if os.path.exists(os.path.join(output_dir, subpath, "iterations.txt")):
            with open(os.path.join(output_dir, subpath, "iterations.txt"), "r") as f:
                iterations = int(f.read())

        options = f"-g batch -s dag -n {iterations}"

        # Try to guess if a python scene needs SofaPython3
        if scene.endswith(".py") or scene.endswith(".pyscn"):
            options += " -l SofaPython3"

        run_sofa_cmd = f"{run_sofa} {options} {scene} >> {os.path.join(output_dir, subpath, 'output.txt')} 2>&1"
        timeout = 30  # Default
        if os.path.exists(os.path.join(output_dir, subpath, "timeout.txt")):
            with open(os.path.join(output_dir, subpath, "timeout.txt"), "r") as f:
                timeout = int(f.read())

        with open(os.path.join(output_dir, subpath, "command.txt"), "w") as f:
            f.write(run_sofa_cmd)

        log(f"- {scene} (thread {thread_num}/{MAX_PARALLEL_TESTS} ; scene {current_scene_count}/{tested_scenes_count})")

        with open(os.path.join(output_dir, subpath, "output.txt"), "w") as f:
            f.write("\n------------------------------------------\n\n")
            f.write(f"Running scene-test {scene}\n")
            f.write(f"Calling: {SCRIPT_DIR}/timeout.sh {os.path.join(output_dir, subpath, 'runSofa')} {run_sofa_cmd} {timeout}\n\n")

        begin_millisec = int(time.time() * 1000)
        subprocess.run([os.path.join(SCRIPT_DIR, "timeout.sh"), os.path.join(output_dir, subpath, "runSofa"), run_sofa_cmd, str(timeout)])
        end_millisec = int(time.time() * 1000)

        elapsed_millisec = end_millisec - begin_millisec
        elapsed_sec = f"{elapsed_millisec // 1000}.{elapsed_millisec % 1000:03d}"

        if os.path.exists(os.path.join(output_dir, subpath, "runSofa.timeout")):
            log(f"Timeout after {timeout} seconds ({elapsed_sec})! {scene}")
            with open(os.path.join(output_dir, subpath, "status.txt"), "w") as f:
                f.write("timeout")
            with open(os.path.join(output_dir, subpath, "output.txt"), "a") as f:
                f.write("\n\nINFO: Abort caused by timeout.\n")
            os.remove(os.path.join(output_dir, subpath, "runSofa.timeout"))
            shutil.copy(os.path.join(output_dir, subpath, "timeout.txt"), os.path.join(output_dir, subpath, "duration.txt"))
        else:
            if os.path.exists(os.path.join(output_dir, subpath, "runSofa.exit_code")):
                shutil.copy(os.path.join(output_dir, subpath, "runSofa.exit_code"), os.path.join(output_dir, subpath, "status.txt"))
            
            elapsed_sec_real = None
            if os.path.exists(os.path.join(output_dir, subpath, "output.txt")):
                with open(os.path.join(output_dir, subpath, "output.txt"), "r") as f:
                    output = f.read()
                    match = re.search(r"iterations done in ([0-9.]*?) s", output)
                    if match:
                        elapsed_sec_real = match.group(1)
            
            if elapsed_sec_real:
                with open(os.path.join(output_dir, subpath, "duration.txt"), "w") as f:
                    f.write(elapsed_sec_real)
            else:
                with open(os.path.join(output_dir, subpath, "duration.txt"), "w") as f:
                    f.write(elapsed_sec)

        if os.path.exists(os.path.join(output_dir, subpath, "runSofa.exit_code")):
            os.remove(os.path.join(output_dir, subpath, "runSofa.exit_code"))

def test_all_scenes():
    """Test all scenes."""
    log("Scene testing in progress...")

    # Shuffle the scenes if shuf is available
    if shutil.which("shuf"):
        subprocess.run(["shuf", os.path.join(output_dir, "all-tested-scenes.txt")], stdout=open(os.path.join(output_dir, "all-tested-scenes.txt"), "w"))

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        total_lines = len(f.readlines())

    lines_per_thread = (total_lines // MAX_PARALLEL_TESTS) + 1
    subprocess.run(["split", "-l", str(lines_per_thread), os.path.join(output_dir, "all-tested-scenes.txt"), os.path.join(output_dir, "all-tested-scenes_part-")])

    threads = []
    for i in range(MAX_PARALLEL_TESTS):
        part_file = os.path.join(output_dir, f"all-tested-scenes_part-{chr(97 + i)}")
        if os.path.exists(part_file):
            with open(part_file, "r") as f:
                tested_scenes = f.read().splitlines()
            thread = threading.Thread(target=do_test_all_scenes, args=(tested_scenes, i + 1))
            thread.start()
            threads.append(thread)

    for thread in threads:
        thread.join()

    log("Done.")

def extract_warnings():
    """Extract warnings from the output."""
    log("Extracting warnings...")

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    warnings_tmp = []
    for scene in tested_scenes:
        subpath = get_output_relative_dir(scene)
        output_file = os.path.join(output_dir, subpath, "output.txt")

        if os.path.exists(output_file):
            with open(output_file, "r") as f:
                output = f.read()
                warnings = re.findall(r"^\[WARNING\] [^\]]*", output, re.MULTILINE)
                if warnings:
                    scene_path = os.path.dirname(subpath)
                    archive_dir = os.path.join(output_dir, "archive", "warnings", scene_path)
                    os.makedirs(archive_dir, exist_ok=True)
                    if os.path.exists(os.path.join(output_dir, subpath)):
                        shutil.copytree(os.path.join(output_dir, subpath), os.path.join(archive_dir, os.path.basename(subpath)), dirs_exist_ok=True)

                    warnings_file = os.path.join(output_dir, subpath, "warnings.txt")
                    with open(warnings_file, "w") as f:
                        f.write('\n'.join(warnings) + "\n")
                    warnings_tmp.append(f"{scene}: {' '.join(warnings)}")

    with open(os.path.join(output_dir, "reports", "warnings.tmp"), "w") as f:
        f.write('\n'.join(warnings_tmp) + "\n")

    if os.path.exists(os.path.join(output_dir, "reports", "warnings.tmp")):
        subprocess.run(["sort", os.path.join(output_dir, "reports", "warnings.tmp")], stdout=open(os.path.join(output_dir, "reports", "warnings.txt"), "w"))
        os.remove(os.path.join(output_dir, "reports", "warnings.tmp"))
    
    log("Done.")

def extract_errors():
    """Extract errors from the output."""
    log("Extracting errors...")

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    errors_tmp = []
    for scene in tested_scenes:
        subpath = get_output_relative_dir(scene)
        output_file = os.path.join(output_dir, subpath, "output.txt")

        if os.path.exists(output_file):
            with open(output_file, "r") as f:
                output = f.read()
                errors = re.findall(r"^\[ERROR\] [^\]]*", output, re.MULTILINE)
                if errors:
                    scene_path = os.path.dirname(subpath)
                    archive_dir = os.path.join(output_dir, "archive", "errors", scene_path)
                    os.makedirs(archive_dir, exist_ok=True)
                    if os.path.exists(os.path.join(output_dir, subpath)):
                        shutil.copytree(os.path.join(output_dir, subpath), os.path.join(archive_dir, os.path.basename(subpath)), dirs_exist_ok=True)

                    errors_file = os.path.join(output_dir, subpath, "errors.txt")
                    with open(errors_file, "w") as f:
                        f.write('\n'.join(errors) + "\n")
                    errors_tmp.append(f"{scene}: {' '.join(errors)}")

    with open(os.path.join(output_dir, "reports", "errors.tmp"), "w") as f:
        f.write('\n'.join(errors_tmp) + "\n")

    if os.path.exists(os.path.join(output_dir, "reports", "errors.tmp")):
        subprocess.run(["sort", os.path.join(output_dir, "reports", "errors.tmp")], stdout=open(os.path.join(output_dir, "reports", "errors.txt"), "w"))
        os.remove(os.path.join(output_dir, "reports", "errors.tmp"))
    
    log("Done.")

def extract_crashes():
    """Extract crashes from the output."""
    log("Extracting crashes...")
    if os.path.exists(os.path.join(output_dir, "archive")):
        shutil.rmtree(os.path.join(output_dir, "archive"))
    os.makedirs(os.path.join(output_dir, "archive"), exist_ok=True)

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    crashes = []
    for scene in tested_scenes:
        subpath = get_output_relative_dir(scene)
        status_file = os.path.join(output_dir, subpath, "status.txt")

        if os.path.exists(status_file):
            with open(status_file, "r") as f:
                status = f.read().strip()
                if status != "0":
                    crashes.append(f"{scene}: error: {status}")
                    scene_path = os.path.dirname(subpath)
                    archive_dir = os.path.join(output_dir, "archive", "crashes", scene_path)
                    os.makedirs(archive_dir, exist_ok=True)
                    if os.path.exists(os.path.join(output_dir, subpath)):
                        shutil.copytree(os.path.join(output_dir, subpath), os.path.join(archive_dir, os.path.basename(subpath)), dirs_exist_ok=True)

    with open(os.path.join(output_dir, "reports", "crashes.txt"), "w") as f:
        f.write('\n'.join(crashes) + "\n")
    
    log("Done.")

def extract_successes():
    """Extract successes from the output."""
    log("Extracting successes...")

    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        log("all-tested-scenes.txt not found.")
        return

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    successes = []
    for scene in tested_scenes:
        subpath = get_output_relative_dir(scene)
        status_file = os.path.join(output_dir, subpath, "status.txt")

        if os.path.exists(status_file):
            with open(status_file, "r") as f:
                status = f.read().strip()
                if status == "0":
                    output_file = os.path.join(output_dir, subpath, "output.txt")
                    if os.path.exists(output_file):
                        with open(output_file, "r") as f:
                            output = f.read()
                            if "[ERROR]" not in output:
                                successes.append(scene)

    with open(os.path.join(output_dir, "reports", "successes.tmp"), "w") as f:
        f.write('\n'.join(successes) + "\n")

    if os.path.exists(os.path.join(output_dir, "reports", "successes.tmp")):
        subprocess.run(["sort", os.path.join(output_dir, "reports", "successes.tmp")], stdout=open(os.path.join(output_dir, "reports", "successes.txt"), "w"))
        os.remove(os.path.join(output_dir, "reports", "successes.tmp"))
    
    log("Done.")

def count_tested_scenes():
    """Count the number of tested scenes."""
    if os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
            return len(f.readlines())
    return 0

def count_durations():
    """Count the total duration of all tested scenes."""
    total = 0
    if not os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
        return 0

    with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
        tested_scenes = f.read().splitlines()

    for scene in tested_scenes:
        subpath = get_output_relative_dir(scene)
        duration_file = os.path.join(output_dir, subpath, "duration.txt")
        if os.path.exists(duration_file):
            with open(duration_file, "r") as f:
                duration = f.read().strip()
                try:
                    total += float(duration)
                except ValueError:
                    pass

    return total

def count_successes():
    """Count the number of successes."""
    if os.path.exists(os.path.join(output_dir, "reports", "successes.txt")):
        with open(os.path.join(output_dir, "reports", "successes.txt"), "r") as f:
            return len(f.readlines())
    return 0

def count_warnings():
    """Count the number of warnings."""
    if os.path.exists(os.path.join(output_dir, "reports", "warnings.txt")):
        with open(os.path.join(output_dir, "reports", "warnings.txt"), "r") as f:
            return len(f.readlines())
    return 0

def count_errors():
    """Count the number of errors."""
    if os.path.exists(os.path.join(output_dir, "reports", "errors.txt")):
        with open(os.path.join(output_dir, "reports", "errors.txt"), "r") as f:
            return len(f.readlines())
    return 0

def count_crashes():
    """Count the number of crashes."""
    if os.path.exists(os.path.join(output_dir, "reports", "crashes.txt")):
        with open(os.path.join(output_dir, "reports", "crashes.txt"), "r") as f:
            return len(f.readlines())
    return 0

def clamp_warnings(clamp_limit):
    """Clamp the number of warnings."""
    log(f"INFO: scene-test warnings limited to {clamp_limit}")
    warnings_file = os.path.join(output_dir, "reports", "warnings.txt")
    if os.path.exists(warnings_file):
        warnings_lines = count_warnings()
        if warnings_lines > clamp_limit:
            log("-------------------------------------------------------------")
            log(f"ALERT: TOO MANY SCENE-TEST WARNINGS ({warnings_lines} > {clamp_limit}), CLAMPING TO {clamp_limit}")
            log("-------------------------------------------------------------")
            with open(warnings_file, "r") as f:
                warnings = f.readlines()
            with open(warnings_file, "w") as f:
                f.writelines(warnings[:clamp_limit])

            with open(os.path.join(output_dir, "reports", "errors.txt"), "a") as f:
                f.write(f"{warnings_file}: [ERROR]   [JENKINS] TOO MANY SCENE-TEST WARNINGS (> {clamp_limit}), CLAMPING FILE TO {clamp_limit}\n")
        else:
            log(f"INFO: warnings clamping not needed ({warnings_lines} < {clamp_limit})")

def clamp_errors(clamp_limit):
    """Clamp the number of errors."""
    log(f"INFO: scene-test errors limited to {clamp_limit}")
    errors_file = os.path.join(output_dir, "reports", "errors.txt")
    if os.path.exists(errors_file):
        error_lines = count_errors()
        if error_lines > clamp_limit:
            log("-------------------------------------------------------------")
            log(f"ALERT: TOO MANY SCENE-TEST ERRORS ({error_lines} > {clamp_limit}), CLAMPING TO {clamp_limit}")
            log("-------------------------------------------------------------")
            with open(errors_file, "r") as f:
                errors = f.readlines()
            with open(errors_file, "w") as f:
                f.writelines(errors[:clamp_limit])

            with open(errors_file, "a") as f:
                f.write(f"{errors_file}: [ERROR]   [JENKINS] TOO MANY SCENE-TEST ERRORS (> {clamp_limit}), CLAMPING FILE TO {clamp_limit}\n")
        else:
            log(f"INFO: errors clamping not needed ({error_lines} < {clamp_limit})")

def print_summary():
    """Print a summary of the scene testing."""
    log("Scene testing summary:")
    log(f"- {count_tested_scenes()} scene(s) tested")
    log(f"- {count_successes()} success(es)")
    log(f"- {count_warnings()} warning(s)")

    errors = count_errors()
    log(f"- {errors} error(s)")
    if errors != 0:
        if os.path.exists(os.path.join(output_dir, "reports", "errors.txt")):
            with open(os.path.join(output_dir, "reports", "errors.txt"), "r") as f:
                for error in sorted(set(f.read().splitlines())):
                    log(f"  - {error}")

    crashes = count_crashes()
    log(f"- {crashes} crash(es)")
    if crashes != 0:
        if os.path.exists(os.path.join(output_dir, "all-tested-scenes.txt")):
            with open(os.path.join(output_dir, "all-tested-scenes.txt"), "r") as f:
                tested_scenes = f.read().splitlines()
            for scene in tested_scenes:
                subpath = get_output_relative_dir(scene)
                status_file = os.path.join(output_dir, subpath, "status.txt")
                if os.path.exists(status_file):
                    with open(status_file, "r") as f:
                        status = f.read().strip()
                        if status == "timeout":
                            log(f"  - Timeout: {scene}")
                        elif status.isdigit():
                            if int(status) > 128 and (os.uname().sysname == "Darwin" or os.uname().sysname == "Linux"):
                                try:
                                    signal_name = subprocess.run(['kill', '-l', status], capture_output=True, text=True).stdout.strip()
                                    log(f"  - Exit with status {status} ({signal_name}): {scene}")
                                except:
                                    log(f"  - Exit with status {status}: {scene}")
                            elif status != "0":
                                log(f"  - Exit with status {status}: {scene}")
                        elif status != "0":
                            log(f"Error: unexpected value in {status_file}: {status}")

def export_to_junit_xml():
    """Export results as JUnit XML."""
    log("Exporting as JUnit XML...")
    xml_file_errors_crashes = os.path.join(output_dir, "reports", "junit_errors_crashes.xml")
    xml_file_successes = os.path.join(output_dir, "reports", "junit_successes.xml")

    # Gather results for errors and crashes
    errors_crashes = []
    if os.path.exists(os.path.join(output_dir, "reports", "errors.txt")):
        with open(os.path.join(output_dir, "reports", "errors.txt"), "r") as f:
            errors = f.read().splitlines()
        for line in errors:
            scene = line.split(':')[0]
            errors_crashes.append(scene)

    if os.path.exists(os.path.join(output_dir, "reports", "crashes.txt")):
        with open(os.path.join(output_dir, "reports", "crashes.txt"), "r") as f:
            crashes = f.read().splitlines()
        for line in crashes:
            scene = line.split(':')[0]
            errors_crashes.append(scene)

    errors_crashes = sorted(set(errors_crashes))

    with open(xml_file_errors_crashes + ".tmp", "w") as f:
        for scene in errors_crashes:
            subpath = get_output_relative_dir(scene)
            scene_path = os.path.dirname(subpath)
            scene_name = os.path.basename(scene)
            scene_name_noext = os.path.splitext(scene_name)[0]
            elapsed_sec = "0"
            if os.path.exists(os.path.join(output_dir, subpath, "duration.txt")):
                with open(os.path.join(output_dir, subpath, "duration.txt"), "r") as duration_f:
                    elapsed_sec = duration_f.read().strip()

            f.write(f'\n        <testcase name="{scene_name}" type_param="" status="run" time="{elapsed_sec}" classname="SceneTests.{scene_path}">\n')

            # Add crashes
            if os.path.exists(os.path.join(output_dir, "reports", "crashes.txt")):
                with open(os.path.join(output_dir, "reports", "crashes.txt"), "r") as crashes_f:
                    for crash_line in crashes_f.read().splitlines():
                        if scene in crash_line:
                            parts = crash_line.split(':', 1)
                            crash_msg = parts[1].strip() if len(parts) > 1 else crash_line.strip()
                            crash_msg_short = crash_msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                            output_file = os.path.join(output_dir, subpath, "output.txt")
                            output_content = ""
                            if os.path.exists(output_file):
                                with open(output_file, "r") as output_f:
                                    output_content = output_f.read()
                            else:
                                output_content = f"export-to-junit-xml: error while running \"cat {output_file}\". See logs for details."
                            f.write(f'\n            <error message="{crash_msg_short}">\n<![CDATA[{output_content}]]>\n            </error>\n')

            # Add errors
            if os.path.exists(os.path.join(output_dir, "reports", "errors.txt")):
                with open(os.path.join(output_dir, "reports", "errors.txt"), "r") as errors_f:
                    for error_line in errors_f.read().splitlines():
                        if scene in error_line:
                            parts = error_line.split(':', 1)
                            error_msg = parts[1].strip() if len(parts) > 1 else error_line.strip()
                            error_msg_short = error_msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                            output_file = os.path.join(output_dir, subpath, "output.txt")
                            output_content = ""
                            if os.path.exists(output_file):
                                with open(output_file, "r") as output_f:
                                    output_content = output_f.read()
                            else:
                                output_content = f"export-to-junit-xml: error while running \"cat {output_file}\". See logs for details."
                            f.write(f'\n            <failure message="{error_msg_short}">\n<![CDATA[{output_content}]]>\n            </failure>\n')

            f.write('\n        </testcase>\n')

    # Write XML report for errors and crashes
    count_errors_crashes_tests = 0
    count_errors_crashes_errors = 0
    count_errors_crashes_failures = 0
    if os.path.exists(xml_file_errors_crashes + ".tmp"):
        with open(xml_file_errors_crashes + ".tmp", "r") as f:
            content = f.read()
            count_errors_crashes_tests = len(re.findall('<testcase ', content))
            count_errors_crashes_errors = len(re.findall('<error ', content))
            count_errors_crashes_failures = len(re.findall('<failure ', content))

    with open(xml_file_errors_crashes, "w") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites name="Scene Tests" tests="{count_errors_crashes_tests}" errors="{count_errors_crashes_errors}" failures="{count_errors_crashes_failures}" disabled="0">\n    <testsuite name="All Scenes" tests="{count_errors_crashes_tests}" errors="{count_errors_crashes_errors}" failures="{count_errors_crashes_failures}" disabled="0">\n')
        if os.path.exists(xml_file_errors_crashes + ".tmp"):
            with open(xml_file_errors_crashes + ".tmp", "r") as tmp_f:
                content = tmp_f.read()
                content = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', content)
                f.write(content)
        f.write('\n    </testsuite>\n</testsuites>\n')

    # Gather results for successes
    successes = []
    if os.path.exists(os.path.join(output_dir, "reports", "successes.txt")):
        with open(os.path.join(output_dir, "reports", "successes.txt"), "r") as f:
            successes = f.read().splitlines()

    with open(xml_file_successes + ".tmp", "w") as f:
        for scene in successes:
            subpath = get_output_relative_dir(scene)
            scene_path = os.path.dirname(subpath)
            scene_name = os.path.basename(scene)
            scene_name_noext = os.path.splitext(scene_name)[0]
            elapsed_sec = "0"
            if os.path.exists(os.path.join(output_dir, subpath, "duration.txt")):
                with open(os.path.join(output_dir, subpath, "duration.txt"), "r") as duration_f:
                    elapsed_sec = duration_f.read().strip()

            f.write(f'\n        <testcase name="{scene_name}" type_param="" status="run" time="{elapsed_sec}" classname="SceneTests.{scene_path}">\n')

            output_file = os.path.join(output_dir, subpath, "output.txt")
            output_content = ""
            if os.path.exists(output_file):
                with open(output_file, "r") as output_f:
                    output_content = output_f.read()
            else:
                output_content = f"export-to-junit-xml: error while running \"cat {output_file}\". See logs for details."
            f.write(f'\n        <system-out>\n<![CDATA[{output_content}]]>\n        </system-out>\n')

            f.write('\n        </testcase>\n')

    # Write XML report for successes
    count_successes_tests = 0
    count_successes_errors = 0
    count_successes_failures = 0
    if os.path.exists(xml_file_successes + ".tmp"):
        with open(xml_file_successes + ".tmp", "r") as f:
            content = f.read()
            count_successes_tests = len(re.findall('<testcase ', content))
            count_successes_errors = len(re.findall('<error ', content))
            count_successes_failures = len(re.findall('<failure ', content))

    with open(xml_file_successes, "w") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites name="Scene Tests" tests="{count_successes_tests}" errors="{count_successes_errors}" failures="{count_successes_failures}" disabled="0">\n    <testsuite name="All Scenes" tests="{count_successes_tests}" errors="{count_successes_errors}" failures="{count_successes_failures}" disabled="0">\n')
        if os.path.exists(xml_file_successes + ".tmp"):
            with open(xml_file_successes + ".tmp", "r") as tmp_f:
                content = tmp_f.read()
                content = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', content)
                f.write(content)
        f.write('\n    </testsuite>\n</testsuites>\n')

    if os.path.exists(xml_file_errors_crashes + ".tmp"):
        os.remove(xml_file_errors_crashes + ".tmp")
    if os.path.exists(xml_file_successes + ".tmp"):
        os.remove(xml_file_successes + ".tmp")
    
    log("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        usage()
        sys.exit(1)

    command = sys.argv[1]
    build_dir = os.path.abspath(sys.argv[2])
    src_dir = os.path.abspath(sys.argv[3])
    output_dir = os.path.join(sys.argv[4], "scene-tests")

    if len(sys.argv) == 6:
        MAX_PARALLEL_TESTS = int(sys.argv[5])
    else:
        MAX_PARALLEL_TESTS = 1

    # Find runSofa
    run_sofa = None
    for pattern in ["runSofa", "runSofad", "runSofa_d"]:
        for file in glob.glob(os.path.join(build_dir, "bin", pattern)):
            run_sofa = file
            break
        if run_sofa:
            break

    if command == "run":
        initialize_scene_tests()
        if not os.path.exists(os.path.join(build_dir, "config")):
            os.makedirs(os.path.join(build_dir, "config"))
        if not os.path.exists(os.path.join(build_dir, "screenshots")):
            os.makedirs(os.path.join(build_dir, "screenshots"))
        if not ("SOFA_WITH_DEPRECATED_COMPONENTS:.*=ON" in open(os.path.join(build_dir, "CMakeCache.txt")).read() and "APPLICATION_GETDEPRECATEDCOMPONENTS:.*=ON" in open(os.path.join(build_dir, "CMakeCache.txt")).read()):
            ignore_scenes_with_deprecated_components()
        ignore_scenes_with_missing_plugins()
        ignore_scenes_python_without_createscene()
        test_all_scenes()
        extract_successes()
        extract_warnings()
        extract_errors()
        extract_crashes()
        if not sys.platform.startswith('darwin'):
            export_to_junit_xml()
    elif command == "print-summary":
        print_summary()
    elif command == "count-tested-scenes":
        print(count_tested_scenes())
    elif command == "count-durations":
        print(count_durations())
    elif command == "count-successes":
        print(count_successes())
    elif command == "count-warnings":
        print(count_warnings())
    elif command == "count-errors":
        print(count_errors())
    elif command == "count-crashes":
        print(count_crashes())
    elif command == "clamp-warnings":
        clamp_warnings(int(sys.argv[4]))
    elif command == "clamp-errors":
        clamp_errors(int(sys.argv[4]))
    elif command == "extract-all":
        extract_successes()
        extract_warnings()
        extract_errors()
        extract_crashes()
    elif command == "export-junit":
        export_to_junit_xml()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)