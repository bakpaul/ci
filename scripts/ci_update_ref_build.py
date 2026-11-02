#!python
"""Build-job steps of CI_generate_regression_references (run by CI_build_and_test / CI_macos_linux_windows_pixi).

  ci_update_ref_build.py prepare   delete the listed references, write the `--filter` regex to <BUILD_DIR>/ci_update_ref/filter.txt
  ci_update_ref_build.py commit    commit the new references of this OS in each repository, rebase, push (with retries)

Environment: CI_UPDATE_REF (JSON of ci_update_ref_setup.py), SRC_DIR, BUILD_DIR, REGRESSION_DIR,
             BUILD_COMMIT (hash or HEAD), GH_TOKEN (used by `gh auth setup-git`).
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC_DIR = os.environ.get("SRC_DIR")
BUILD_DIR = os.environ.get("BUILD_DIR")
REGRESSION_DIR = os.environ.get("REGRESSION_DIR")

# Shared utilities of the Regression plugin (no SOFA import needed)
sys.path.insert(0, os.path.join(REGRESSION_DIR or "", "SofaRegressionProgram", "tools"))
import RegressionUtils  # noqa: E402
import RegressionHelper  # noqa: E402

PUSH_RETRIES = 5


def os_name():
    """Reference folder of this build: forced by CI_UPDATE_REF_OS when the regression runs in a docker image."""
    return os.environ.get("CI_UPDATE_REF_OS") or RegressionHelper.get_os()


def load_payload():
    return json.loads(os.environ["CI_UPDATE_REF"])


def fetched_dir():
    return os.path.join(BUILD_DIR, "external_directories", "fetched")


def input_folders():
    return [os.path.join(SRC_DIR, "examples"), os.path.join(SRC_DIR, "applications"), fetched_dir()]


def repo_dir(repo):
    if repo["kind"] == "regression":
        return REGRESSION_DIR
    if repo["kind"] == "sofa":
        return SRC_DIR
    return os.path.join(fetched_dir(), repo["id"])


def plugin_root(key):
    """Root directory of the plugin `key` (see the location rules of the ci-update-ref format)."""
    if key == "sofa":
        return SRC_DIR
    for folder in ("plugins", "projects"):
        candidate = os.path.join(SRC_DIR, "applications", folder, key)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(fetched_dir(), key)


def active_scenes(payload):
    """ci-update-ref structure restricted to the repositories that are not skipped."""
    skipped = {k for r in payload["repos"] if r["skipped"] for k in r["keys"]}
    return {k: v for k, v in payload["scenes"].items() if k not in skipped}


def prepare():
    payload = load_payload()
    scenes = active_scenes(payload)
    if not scenes:
        print("Nothing to generate: every repository is skipped.")
        regex = None
    else:
        roots = {k: plugin_root(k) for k in scenes}
        regex = RegressionUtils.build_filter_from_update_ref(scenes, roots, input_folders())
        current_os = os_name()
        for key, scene_list in scenes.items():
            for scene in scene_list:
                for _, value, ref_dir in RegressionUtils.find_scene_entries(input_folders(), os.path.join(roots[key], scene)):
                    prefix = RegressionUtils.reference_file_path(ref_dir, current_os, value)
                    for ref_file in RegressionUtils.list_reference_files(prefix):
                        print(f"Deleting {ref_file}")
                        os.remove(ref_file)

    out = Path(BUILD_DIR) / "ci_update_ref"
    out.mkdir(parents=True, exist_ok=True)
    (out / "filter.txt").write_text(regex or "", encoding="utf-8")
    print(f"Filter regex: {regex}")


# ======================================================================================================================


def git(cwd, *args, check=True, capture=False):
    return subprocess.run(["git", *args], cwd=cwd, check=check, text=True,
                          stdout=subprocess.PIPE if capture else None)


def reference_pathspecs(payload, cwd):
    """Reference folders of this OS, for the scenes of the payload, that are inside the repository `cwd`."""
    current_os = os_name()
    roots = {k: plugin_root(k) for k in active_scenes(payload)}
    specs = set()
    for key, scene_list in active_scenes(payload).items():
        for scene in scene_list:
            for _, value, ref_dir in RegressionUtils.find_scene_entries(input_folders(), os.path.join(roots[key], scene)):
                folder = os.path.abspath(os.path.join(ref_dir, current_os))
                if folder.startswith(os.path.abspath(cwd) + os.sep):
                    specs.add(os.path.relpath(folder, cwd).replace(os.sep, "/"))
    return sorted(specs)


def changed_files(cwd, pathspecs):
    """(modified_or_added, deleted) paths of the references, relative to the repository root."""
    out = git(cwd, "status", "--porcelain", "-z", "--untracked-files=all", "--", *pathspecs, capture=True).stdout
    entries = [e for e in out.split("\0") if e]
    present, deleted = [], []
    for e in entries:
        status, path = e[:2], e[3:]
        (deleted if "D" in status else present).append(path)
    return present, deleted


def build_commit_hash():
    commit = os.environ.get("BUILD_COMMIT", "HEAD")
    if commit in ("", "HEAD"):
        commit = git(SRC_DIR, "rev-parse", "HEAD", capture=True).stdout.strip()
    return commit


def commit_repo(repo, message, branch, payload):
    cwd = repo_dir(repo)
    pathspecs = reference_pathspecs(payload, cwd)
    if not pathspecs:
        print(f"{repo['id']}: no reference folder inside this repository.")
        return True
    present, deleted = changed_files(cwd, pathspecs)
    if not present and not deleted:
        print(f"{repo['id']}: no reference changed, nothing to commit.")
        return True

    # Keep the generated files aside: the repository may not be on the references branch (nor even contain it)
    with tempfile.TemporaryDirectory() as tmp:
        top = git(cwd, "rev-parse", "--show-toplevel", capture=True).stdout.strip()
        for path in present:
            dest = Path(tmp) / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(os.path.join(top, path), dest)

        git(cwd, "fetch", repo["url"], f"+refs/heads/{branch}:refs/remotes/ci-update-ref/{branch}")
        git(cwd, "checkout", "-f", "-B", branch, f"refs/remotes/ci-update-ref/{branch}")

        for path in present:
            target = os.path.join(top, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(Path(tmp) / path, target)
        for path in deleted:
            if os.path.exists(os.path.join(top, path)):
                os.remove(os.path.join(top, path))

    git(cwd, "add", "-A", "--", *pathspecs)
    if git(cwd, "diff", "--cached", "--quiet", check=False).returncode == 0:
        print(f"{repo['id']}: references identical to the branch content, nothing to commit.")
        return True
    git(cwd, "commit", "-m", message)

    for attempt in range(1, PUSH_RETRIES + 1):
        git(cwd, "pull", "--rebase", repo["url"], branch)
        if git(cwd, "push", repo["url"], f"HEAD:refs/heads/{branch}", check=False).returncode == 0:
            print(f"{repo['id']}: references pushed on {repo['full_name']}:{branch}")
            return True
        print(f"Push rejected (attempt {attempt}/{PUSH_RETRIES}), retrying.")
        time.sleep(2 * attempt + random.uniform(0, 3))
    return False


def commit():
    payload = load_payload()
    # The OS name is the reference folder name, e.g. ubuntu / fedora / macos / windows
    message = f"{os_name()} references from PR {payload['pr_number']} commit {build_commit_hash()}"
    subprocess.run(["gh", "auth", "setup-git"], check=False)
    ok = True
    for repo in payload["repos"]:
        if repo["skipped"]:
            continue
        ok = commit_repo(repo, message, payload["branch"], payload) and ok
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("prepare", "commit"):
        print(__doc__)
        sys.exit(1)
    {"prepare": prepare, "commit": commit}[sys.argv[1]]()
