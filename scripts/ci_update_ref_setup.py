#!python
"""Setup job of CI_generate_regression_references.

Parses the SOFA-PR description (tags, `[ci-update-ref {json}]`), resolves the repositories that will receive the
references, creates the branches `ci/add_sofa_prXXXX_references` and exports the build inputs.

Environment: GH_TOKEN, PR_NUMBER, GITHUB_ENV (as for checkPRInfoBeforeBuild.py).
Terminology: SOFA-PR, DEP-PR (ci-depends-on) and REF-PR (see the finish script).
"""
import json
import os
import re
import sys

import requests

# checkPRInfoBeforeBuild.py reads its configuration at import time
if not os.environ.get("GH_TOKEN") or not os.environ.get("PR_NUMBER"):
    print("Error: GH_TOKEN and PR_NUMBER are required.")
    sys.exit(1)
os.environ.setdefault("GITHUB_TOKEN", os.environ["GH_TOKEN"])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkPRInfoBeforeBuild as pr_info  # noqa: E402  (reused: PR fetch, tags and ci-depends-on parsing)

API = "https://api.github.com"
PR_NUMBER = os.environ["PR_NUMBER"]
BRANCH = f"ci/add_sofa_pr{PR_NUMBER}_references"
DEFAULT_ORG = "sofa-framework"
SH_BUILDERS = ["sh-ubuntu_gcc_release", "sh-fedora_clang_release", "sh-macos_clang_release"]
PIXI_BUILDERS = ["windows-latest"]  # the pixi workflow only runs the regression on Windows


def parse_ci_update_ref(body):
    """Return the `[ci-update-ref {json}]` structure of a SOFA-PR description ({} if absent)."""
    match = re.search(r"\[ci-update-ref\s+(?=\{)", body or "")
    if match is None:
        return {}
    data, end = json.JSONDecoder().raw_decode(body, match.end())
    if not body[end:].lstrip().startswith("]"):
        raise ValueError("Malformed [ci-update-ref ...] tag: missing closing bracket")
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(s, str) for s in v)
            for k, v in data.items()):
        raise ValueError('ci-update-ref must be {"sofa": ["scene.scn"], "plugin_name": [...]}')
    return {k: v for k, v in data.items() if v}


def api_get(path):
    return requests.get(f"{API}/{path}", headers=pr_info.HEADERS)


def split_repo_url(url):
    """'https://github.com/org/name(.git)' -> 'org/name'."""
    return "/".join(url.rstrip("/").removesuffix(".git").split("/")[-2:])


def branch_head_sha(full_name, branch):
    r = api_get(f"repos/{full_name}/git/ref/heads/{branch}")
    r.raise_for_status()
    return r.json()["object"]["sha"]


def create_branch(full_name, sha):
    """Create BRANCH at `sha`. Returns None on success (or if it already exists), else the failure reason."""
    r = requests.post(f"{API}/repos/{full_name}/git/refs", headers=pr_info.HEADERS,
                      json={"ref": f"refs/heads/{BRANCH}", "sha": sha})
    if r.status_code == 201:
        print(f"Branch {BRANCH} created on {full_name} at {sha}")
        return None
    if r.status_code == 422 and "already exists" in r.text:
        print(f"Branch {BRANCH} already exists on {full_name}: it is reused")
        return None
    return f"{r.status_code} {r.json().get('message', '') if r.headers.get('content-type', '').startswith('application/json') else ''}".strip()


def plugin_location(head_full_name, head_sha, name):
    """'sofa' if the plugin lives in the SOFA tree (applications/plugins|projects), else 'fetched'."""
    for folder in ("applications/plugins", "applications/projects"):
        if api_get(f"repos/{head_full_name}/contents/{folder}/{name}?ref={head_sha}").status_code == 200:
            return "sofa"
    return "fetched"


def default_branch(full_name):
    r = api_get(f"repos/{full_name}")
    r.raise_for_status()
    return r.json()["default_branch"]


def plan_repo(name, dep, is_merged):
    """Where and from which start point the references of the repo `name` are pushed (see the CLAUDE.md table)."""
    if name in dep and not is_merged[name]:
        full = split_repo_url(dep[name]["repo_url"])  # head repo of the DEP-PR
        return {"url": f"https://github.com/{full}.git", "full_name": full, "base_branch": dep[name]["branch_name"],
                "dep_pr_url": dep[name]["pr_url"]}
    full = f"{DEFAULT_ORG}/{name}"
    return {"url": f"https://github.com/{full}.git", "full_name": full, "base_branch": default_branch(full),
            "dep_pr_url": None}


def main():
    pr_data = pr_info.fetch_pr_data()
    update_ref = parse_ci_update_ref(pr_data.get("body"))
    if not update_ref:
        print("Error: no [ci-update-ref {json}] tag found in the SOFA-PR description.")
        sys.exit(1)

    pr_sha = pr_info.export_pr_sha(pr_data)  # PR_OWNER_URL, PR_BRANCH_NAME, PR_COMMIT_SHA in GITHUB_ENV
    pr_info.check_body_for_tags(pr_data)
    dep, is_merged = pr_info.extract_ci_depends_on(pr_data)
    forced_builders = pr_info.extract_ci_force_builder(pr_data)

    head_full_name = pr_data["head"]["repo"]["full_name"]
    head_branch = pr_data["head"]["ref"]

    # ---- group the scenes by repository receiving the references
    repos = {}  # id -> repo description
    for key, scenes in update_ref.items():
        if key == "sofa":
            repo_id, kind = "Regression", "regression"
        else:
            kind = plugin_location(head_full_name, pr_sha, key)
            repo_id = "sofa" if kind == "sofa" else key
            kind = "sofa" if kind == "sofa" else "fetched"
        if repo_id not in repos:
            if kind == "sofa":
                repos[repo_id] = {"url": f"https://github.com/{head_full_name}.git", "full_name": head_full_name,
                                  "base_branch": head_branch, "dep_pr_url": None}
            else:
                repos[repo_id] = plan_repo(repo_id, dep, is_merged)
            repos[repo_id].update({"id": repo_id, "kind": kind, "keys": [], "skipped": None})
        repos[repo_id]["keys"].append(key)

    # ---- create the branches (never push to the SOFA-PR or DEP-PR branch itself)
    for repo in repos.values():
        try:
            reason = create_branch(repo["full_name"], branch_head_sha(repo["full_name"], repo["base_branch"]))
        except requests.HTTPError as e:
            reason = str(e)
        if reason is not None:
            print(f"Repository {repo['full_name']} skipped: {reason}")
            repo["skipped"] = reason

    payload = {"pr_number": PR_NUMBER, "branch": BRANCH, "scenes": update_ref, "repos": list(repos.values())}

    # ---- builders (same names as checkPRInfoBeforeBuild.py)
    if forced_builders:
        pixi = [b for b in forced_builders if b in ["ubuntu-latest", "macos-latest", "macos-15-intel", "windows-latest"]]
        sh = [b for b in forced_builders if b not in pixi]
    else:
        pixi, sh = PIXI_BUILDERS, SH_BUILDERS

    with open(os.environ["GITHUB_ENV"], "a") as env_file:
        env_file.write(f"SOFA_BRANCH_NAME={pr_data['base']['ref']}\n")
        env_file.write(f"WITH_ALL_TESTS=False\n")
        env_file.write(f"FORCE_FULL_BUILD={pr_info.force_full_build_found}\n")
        env_file.write("CI_DEPENDS_ON=" + f"{dep}".replace("'", "\\\"") + "\n")
        env_file.write(f"CI_UPDATE_REF={json.dumps(payload, separators=(',', ':'))}\n")
        env_file.write(f"SH_BUILDER_OS={json.dumps(sh, separators=(',', ':'))}\n")
        env_file.write(f"PIXI_BUILDER_OS={json.dumps(pixi, separators=(',', ':'))}\n")


if __name__ == "__main__":
    main()
