"""Convert the `code` split of XiaomiMiMo/MiMo-V2.6-RL-oss into Harbor task directories.

Grading mirrors mimoagent's OpenSourceCodeEnvironment
(third_party/mimoagent-osr/src/mimoagent/environments/datasets/opensource_code.py):
  1. reset every path test_patch touches back to the base commit
  2. git apply test_patch
  3. run test_command; reward = 1 iff it exits 0
The base commit is recorded at image build time, so agent commits can't move it.

Usage: python mimo_to_harbor.py <code.parquet> <out_dir> [--limit N] [--ids id1,id2]
"""

import argparse
import json
import re
import shlex
from pathlib import Path

import pandas as pd

IMAGE_REPO = "xiaomimimo/mimo-v2.6-rl-oss"

TASK_TOML = """schema_version = "1.3"
artifacts = []

[task]
name = "mimo-v2.6-rl-oss/{instance_id}"
description = "MiMo-V2.6 RL code task {instance_id}"
authors = []
keywords = ["mimo", "code", "swe"]

[metadata]
source = "XiaomiMiMo/MiMo-V2.6-RL-oss"
docker_image = "{image}"

[verifier]
timeout_sec = {verifier_timeout}

[verifier.env]

[agent]
timeout_sec = {agent_timeout}
# Agent phase: only the model endpoint is reachable (answer-leak prevention, HANDOFF 4.3).
# The run adds the endpoint host with --allow-agent-host; the placeholder keeps the list non-empty.
network_mode = "allowlist"
allowed_hosts = ["model-endpoint.invalid"]

[environment]
network_mode = "public"
build_timeout_sec = 1800.0
os = "linux"
cpus = {cpus}
memory_mb = {memory_mb}
storage_mb = {storage_mb}
mcp_servers = []

[environment.env]

[solution.env]
"""

DOCKERFILE = """FROM {image}
# The published images were flattened, which dropped their ENV; restore the usual toolchain dirs.
ENV PATH=/usr/local/go/bin:/go/bin:/root/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:/opt/java/openjdk/bin:/usr/local/bundle/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \\
    BASH_ENV=/etc/profile.d/00-mimo-toolchains.sh
COPY mimo_setup.sh /tmp/mimo_setup.sh
RUN sh /tmp/mimo_setup.sh {cwd} {keep_build_env} && rm -f /tmp/mimo_setup.sh
WORKDIR {cwd}
"""

SETUP_SH = r"""#!/bin/sh
# Make a MiMo image usable outside Xiaomi's cluster, then strip answer leaks.
# $1 = repository dir; $2 = 1 if the verifier reads the image's own .build_env (keep it whole).
set -e
CWD="$1"
KEEP_BUILD_ENV="${2:-0}"

# 1. apt points at Xiaomi's internal mirror (apt.sys.srv): use the public archives instead.
rm -f /etc/apt/sources.list.d/xiaomi.sources /etc/apt/sources.list.d/xiaomi.list
for f in $(grep -rl 'apt\.sys\.srv' /etc/apt 2>/dev/null); do
  sed -i -e '/apt\.sys\.srv\/xiaomi/d' \
         -e 's#http://apt\.sys\.srv/ubuntu#http://archive.ubuntu.com/ubuntu#g' \
         -e 's#http://apt\.sys\.srv/debian-security#http://deb.debian.org/debian-security#g' \
         -e 's#http://apt\.sys\.srv/debian#http://deb.debian.org/debian#g' "$f"
done
# Debian releases past end of LTS (bullseye: Aug 2026) are only on archive.debian.org.
. /etc/os-release 2>/dev/null || true
case "${ID:-}:${VERSION_CODENAME:-}" in
  debian:jessie|debian:stretch|debian:buster|debian:bullseye)
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do
      [ -f "$f" ] && sed -i -E 's#https?://(deb|security|httpredir)\.debian\.org/#http://archive.debian.org/#g' "$f"
    done
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99mimo-archive ;;
esac
# Third-party repos baked into some images fail `apt-get update` (missing/rotated signing key,
# gone Release file), which fails the agent's install step. Disable those sources.list.d entries.
if command -v apt-get >/dev/null 2>&1; then
  out=$(apt-get update 2>&1 || true)
  for url in $(echo "$out" | grep -E "^(E|W):" | grep -E "NO_PUBKEY|EXPKEYSIG|not signed|does not have a Release file|is no longer signed|404" \
               | grep -oE "https?://[^ ']+" | sed -E 's#^(https?://[^/]+).*#\1#' | sort -u); do
    for f in $(grep -lF "$url" /etc/apt/sources.list.d/* 2>/dev/null); do
      echo "mimo_setup: disabling $f ($url fails apt-get update)"; mv "$f" "$f.disabled"
    done
  done
fi

# 2. Toolchain env lost in flattening. Sourced by login shells (profile.d) and non-login bash (BASH_ENV).
P=/etc/profile.d/00-mimo-toolchains.sh
: > "$P"
[ -d /usr/local/go ] && echo 'export GOROOT=/usr/local/go' >> "$P"
[ -d /go ] && echo 'export GOPATH=/go' >> "$P"
[ -d /usr/local/rustup ] && echo 'export RUSTUP_HOME=/usr/local/rustup' >> "$P"
[ -d /usr/local/cargo ] && echo 'export CARGO_HOME=/usr/local/cargo' >> "$P"
[ -d /opt/java/openjdk ] && echo 'export JAVA_HOME=/opt/java/openjdk' >> "$P"
[ -d /usr/share/maven ] && echo 'export MAVEN_HOME=/usr/share/maven' >> "$P"
[ -d /usr/local/bundle ] && echo 'export GEM_HOME=/usr/local/bundle BUNDLE_APP_CONFIG=/usr/local/bundle' >> "$P"
# Version managers whose bin dirs were on the image's PATH. Each line prepends, so later lines win:
# managers first, then the project venv below.
add() { echo "case \":\$PATH:\" in *:$1:*) ;; *) export PATH=\"$1:\$PATH\";; esac" >> "$P"; }
for d in /root/.volta/bin /root/.bun/bin /root/.deno/bin /root/.rbenv/shims /root/.goenv/shims; do [ -d "$d" ] && add "$d"; done
for d in /opt/conda/bin /root/miniconda3/bin /root/anaconda3/bin /opt/miniconda3/bin; do [ -d "$d" ] && { add "$d"; break; }; done
for d in /root/.sdkman/candidates/*/current/bin; do [ -d "$d" ] && add "$d"; done
n=$(ls -d /root/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1); [ -n "$n" ] && add "$n"
if [ -d /root/.pyenv/versions ]; then
  echo 'export PYENV_ROOT=/root/.pyenv' >> "$P"
  [ -d /root/.pyenv/bin ] && add /root/.pyenv/bin
  if [ -s /root/.pyenv/version ] && [ -d /root/.pyenv/shims ]; then add /root/.pyenv/shims
  else v=$(ls -d /root/.pyenv/versions/*/bin 2>/dev/null | sort -V | tail -1); [ -n "$v" ] && add "$v"; fi
fi
# Project virtualenv (e.g. /testbed/.venv in the multi-toolchain images) was on the image's PATH.
for v in "$CWD/.venv" "$CWD/venv" /opt/venv; do
  if [ -x "$v/bin/python" ]; then
    echo "case \":\$PATH:\" in *:$v/bin:*) ;; *) export VIRTUAL_ENV=$v PATH=\"$v/bin:\$PATH\";; esac" >> "$P"
    break
  fi
done
echo 'case ":$PATH:" in *:/usr/local/go/bin:*) ;; *) export PATH="/usr/local/go/bin:/go/bin:/root/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:/opt/java/openjdk/bin:/usr/local/bundle/bin:$PATH";; esac' >> "$P"

# Some images ship MiMo's own build-time verifier output under /logs (e.g. an LLM-judge reward.json).
# Harbor reads /logs/verifier/reward.json, so stale files there would be taken as this run's reward.
rm -rf /logs/verifier /logs/agent

# 3. Record the task's base commit (images without git history get a baseline commit),
#    root-only so the verifier's reset point can't be moved by the agent.
git config --global --add safe.directory '*'
if ! git -C "$CWD" rev-parse --git-dir >/dev/null 2>&1; then
  (cd "$CWD" && git init -q && git add -A && git -c user.email=base@mimo -c user.name=base commit -q -m baseline --allow-empty)
fi
git -C "$CWD" rev-parse HEAD > /etc/mimo_base_ref
chmod 600 /etc/mimo_base_ref
BASE=$(cat /etc/mimo_base_ref)
cd "$CWD"

# 4. Anti-hack cleanup, ported from MiMo-Agent (anti_hack_cleanup: true in verl config/agent/code/mini-*.yaml):
#    environments/datasets/base.py _purge_build_residue + _purge_build_artifacts + _purge_global_caches.
#    Best-effort, like MiMo. This dataset rows carry no "language", so MiMo uses _CLEAN_KEEP_UNKNOWN and
#    skips the rust/swift/python-specific steps; same here.
# 4a. _purge_build_residue (_RESIDUE_SCRUB_GLOBAL + repo node_modules caches)
rm -rf "$CWD/node_modules/.cache" "$CWD/node_modules/.vitest" 2>/dev/null || true
rm -f /tmp/fail.log /tmp/pass.log /tmp/patch.diff /tmp/test_patch.diff /tmp/*.log 2>/dev/null || true
rm -rf /tmp/claude-0 /tmp/claude-* /tmp/testem-* /tmp/puppeteer_dev_chrome_profile-* 2>/dev/null || true
rm -f /tmp/test_files.json 2>/dev/null || true
rm -rf /tmp/jest_* /tmp/jest-* /tmp/build /tmp/build_env 2>/dev/null || true
rm -rf /tmp/pytest-of-root /tmp/pytest-* /tmp/.pytest_cache /tmp/__pycache__ 2>/dev/null || true
rm -rf /tmp/go-build* /tmp/standards /tmp/wordpress /tmp/zig-* /tmp/testbase /tmp/testbed 2>/dev/null || true
rm -f /tmp/tmp*.tmp /tmp/*.bak /tmp/expect* /tmp/butwas* 2>/dev/null || true
rm -rf /root/.cache/go-build 2>/dev/null || true
rm -rf /tests /logs 2>/dev/null || true
find /var/log -type f -delete 2>/dev/null || true
rm -rf /var/lib/postgresql/*/*/log /var/lib/postgresql/*/*/pg_log 2>/dev/null || true
find /var/lib/mysql /var/lib/mongodb -type f -name '*.log' -delete 2>/dev/null || true
rm -rf /root/.npm/_logs /root/.babel.json /root/.pytest_cache 2>/dev/null || true
find / -maxdepth 4 -xdev -name task_description.md -path '*/.build_env/*' -delete 2>/dev/null || true
# 4b. _purge_build_artifacts: git clean -fdx keeping dependency dirs (_CLEAN_KEEP_COMMON + _CLEAN_KEEP_UNKNOWN).
#     Deviation: .build_env is also excluded here and handled below. MiMo keeps .build_env/test_command.sh
#     by design; 50 tasks' verifiers read the image's own .build_env (venvs, site-packages) without
#     shipping it in test.patch, so for those (KEEP_BUILD_ENV=1) it is kept whole.
git clean -fdxq -e node_modules -e bower_components -e .husky -e vendor -e third_party -e _deps \
  -e vcpkg_installed -e .venv -e venv -e .gradle -e target -e .build -e lib -e .bundle -e Manifest.toml \
  -e _build -e .stack-work -e dist-newstyle -e .build_env 2>/dev/null || true
if [ -d .build_env ] && [ "$KEEP_BUILD_ENV" != 1 ] && ! git ls-files --error-unmatch .build_env >/dev/null 2>&1; then
  find .build_env -mindepth 1 -maxdepth 1 ! -name test_command.sh -exec rm -rf {} + 2>/dev/null || true
fi
# 4c. _purge_global_caches (_GLOBAL_CACHE_SCRUB)
find /root/.m2 -type f \( -name '*-SNAPSHOT.jar' -o -name '*-SNAPSHOT-sources.jar' \
  -o -name '*-SNAPSHOT-tests.jar' -o -name '*-SNAPSHOT-test-sources.jar' \) -delete 2>/dev/null || true
rm -rf /root/.julia/compiled 2>/dev/null || true
rm -rf /root/.gradle/caches/build-cache-* /root/.gradle/daemon 2>/dev/null || true
rm -rf /root/.gradle/caches/*/scripts /root/.gradle/caches/jars-* 2>/dev/null || true
rm -rf /root/.cache/bazel 2>/dev/null || true

# 5. Strip git history that is not in BASE's ancestry, then assert nothing newer than BASE survives.
#    (MiMo: base.py _strip_future_commits + opensource_code.py _assert_history_truncated; this also
#    removes dangling objects, which MiMo's rev-list assertion does not see.) HEAD stays at BASE and the
#    working tree is untouched.
strip_git() {  # $1 = repo dir, $2 = base commit
  (
  cd "$1" || exit 1
  b="$2"
  gd=$(git rev-parse --absolute-git-dir) || exit 1
  git worktree prune 2>/dev/null || true
  rm -rf "$gd/worktrees"
  for r in $(git remote 2>/dev/null); do git remote remove "$r" >/dev/null 2>&1 || true; done
  git stash clear 2>/dev/null || true
  # Keep only refs whose commit is an ancestor of base (old branches/tags for `git describe`);
  # drop everything else, incl. replace/notes/stash/remotes/pull refs and non-commit refs.
  git for-each-ref --format='%(refname)' | while read -r ref; do
    case "$ref" in
      refs/heads/*|refs/tags/*) git merge-base --is-ancestor "$ref" "$b" 2>/dev/null && continue ;;
    esac
    git update-ref -d "$ref" 2>/dev/null || git update-ref --no-deref -d "$ref" 2>/dev/null || true
  done
  # for-each-ref skips broken refs (e.g. refs/remotes/origin/HEAD -> a deleted branch), and gc then
  # dies on them: drop remote refs wholesale and any loose ref that no longer resolves.
  rm -rf "$gd/refs/remotes"
  find "$gd/refs" -type f 2>/dev/null | while read -r f; do
    git rev-parse -q --verify "${f#"$gd"/}" >/dev/null 2>&1 || rm -f "$f"
  done
  [ -f "$gd/packed-refs" ] && grep -v ' refs/remotes/' "$gd/packed-refs" > "$gd/packed-refs.new" && mv "$gd/packed-refs.new" "$gd/packed-refs"
  for f in ORIG_HEAD FETCH_HEAD MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD AUTO_MERGE BISECT_LOG BISECT_START \
           BISECT_EXPECTED_REV BISECT_ANCESTORS_OK BISECT_NAMES BISECT_TERMS rebase-merge rebase-apply \
           sequencer refs/original logs; do
    rm -rf "${gd:?}/$f"
  done
  git reflog expire --expire=now --expire-unreachable=now --all 2>/dev/null || true
  # Objects borrowed from an alternate store can't be pruned here: copy what we need, drop the link.
  if [ -s "$gd/objects/info/alternates" ]; then git repack -a -d -q && rm -f "$gd/objects/info/alternates"; fi
  rm -f "$gd"/objects/pack/*.keep "$gd"/objects/pack/*.mtimes
  git -c gc.pruneExpire=now -c gc.cruftPacks=false -c gc.reflogExpire=now -c gc.reflogExpireUnreachable=now \
    gc -q --prune=now || exit 1
  git prune --expire=now 2>/dev/null || true
  )
}
assert_git_clean() {  # $1 = repo dir, $2 = base commit; prints the reason and returns 1 on a leak
  (
  cd "$1" || exit 1
  b="$2"
  [ "$(git rev-parse HEAD)" = "$b" ] || { echo "HEAD moved off base"; exit 1; }
  n=$(git rev-list --all --not "$b" | wc -l)
  [ "$n" -eq 0 ] || { echo "$n commits outside base ancestry reachable"; exit 1; }
  bt=$(git log -1 --format=%ct "$b")
  newer=0
  for c in $(git fsck --connectivity-only --unreachable --no-reflogs --no-progress 2>/dev/null | awk '$2 == "commit" {print $3}'); do
    ct=$(git log -1 --format=%ct "$c" 2>/dev/null || echo 0)
    [ "$ct" -gt "$bt" ] && newer=$((newer + 1))
  done
  [ "$newer" -eq 0 ] || { echo "$newer unreachable commits newer than base"; exit 1; }
  )
}
strip_git "$CWD" "$BASE"
if ! why=$(assert_git_clean "$CWD" "$BASE"); then
  echo "mimo_setup: FATAL git history leak in $CWD: $why"; exit 1
fi
# Submodules: same strip at each submodule's checked-out commit (warn only).
git submodule foreach --recursive --quiet 'pwd' 2>/dev/null | while read -r sm; do
  sb=$(git -C "$sm" rev-parse HEAD 2>/dev/null) || continue
  strip_git "$sm" "$sb" >/dev/null 2>&1 || true
  w=$(assert_git_clean "$sm" "$sb") || echo "mimo_setup: WARN submodule $sm: $w"
done
echo "mimo_setup: done (base $BASE, $(git rev-list --count HEAD) commits in history)"
"""

TEST_SH = """#!/bin/bash
# Reward = 1 iff test_command exits 0 after the hidden test patch is applied.
mkdir -p /logs/verifier
# Harbor prefers reward.json over reward.txt: drop any stale (image residue) or agent-written one.
rm -f /logs/verifier/reward.json
echo 0 > /logs/verifier/reward.txt
cd {cwd} || exit 0
git config --global --add safe.directory '*'
BASE=$(cat /etc/mimo_base_ref)

# 1. reset every touched path to base (absent in base -> remove; agent may have planted it)
while IFS= read -r tf; do
  [ -z "$tf" ] && continue
  if git cat-file -e "$BASE":"$tf" 2>/dev/null; then
    git checkout "$BASE" -- "$tf" 2>/dev/null || true
  else
    git rm -f --cached "$tf" >/dev/null 2>&1 || true
    rm -f "$tf"
  fi
done <<'EOF_RESET_TEST_FILES'
{touched}
EOF_RESET_TEST_FILES

# 2. apply hidden tests + verifier script
if ! git apply --verbose /tests/test.patch; then
  echo "apply_test_patch_failed" > /logs/verifier/error.txt
  exit 0
fi

# 3. run the verifier; no stdin, like MiMo's k8s exec (stdin=False, tty=False), so nothing can block on a prompt
{{ {test_command}; }} </dev/null
rc=$?
echo "verifier_returncode=$rc"
echo "mimo_mem_peak_bytes=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null) mimo_disk=$(df -k / | awk 'NR==2{{print $3"/"$2}}')"
[ $rc -eq 0 ] && echo 1 > /logs/verifier/reward.txt
exit 0
"""


def touched_files(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.split("\n"):
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if m:
            for fp in m.groups():
                if fp not in files:
                    files.append(fp)
    return files


def uses_image_build_env(patch: str) -> bool:
    """The verifier reads the image's own .build_env (it doesn't ship one as mimo_build_env.tar.gz.b64)."""
    return ".build_env" in patch and "mimo_build_env.tar.gz.b64" not in patch


HEAVY_RE = re.compile(r"\b(node|npx|npm|pnpm|yarn|jest|vitest|mocha|mvn|mvnw|gradle|gradlew|java|sbt)\b")


def verifier_text(r: dict) -> str:
    """test_command + test patch, with any base64 build-env tarball unpacked (it holds the test scripts)."""
    import base64, io, tarfile
    text = r["test_command"] + "\n" + r["test_patch"]
    m = re.search(r"mimo_build_env\.tar\.gz\.b64\n.*?@@[^\n]*\n((?:\+[^\n]*\n)+)", r["test_patch"], re.S)
    if m:
        try:
            raw = base64.b64decode("".join(l[1:] for l in m.group(1).splitlines()))
            with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                for mem in tf.getmembers():
                    if mem.isfile() and mem.size < 2_000_000:
                        text += "\n" + tf.extractfile(mem).read().decode(errors="ignore")
        except Exception:
            pass
    return text


def memory_for(r: dict, default_mb: int, heavy_mb: int) -> int:
    """JS/JVM test stacks get more memory: a 4 GB sandbox OOM-killed a jest verifier in the prototype."""
    return heavy_mb if HEAVY_RE.search(verifier_text(r)) else default_mb


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("out_dir")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ids", default=None, help="comma-separated instance_ids")
    ap.add_argument("--agent-timeout", type=float, default=3600.0)
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--memory-mb", type=int, default=6144)
    # MiMo k8s sandbox limit is 4 CPU / 8Gi; Daytona reserves the full request against a shared 500 GiB
    # org quota, so only JS/JVM verifiers get the full 8 GB.
    ap.add_argument("--heavy-memory-mb", type=int, default=8192)
    ap.add_argument("--storage-mb", type=int, default=10240)
    args = ap.parse_args()

    df = pd.read_parquet(args.parquet)
    rows = [json.loads(e["instance_json"]) for e in df.extra_info]
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if r["instance_id"] in wanted]
    if args.limit:
        rows = rows[: args.limit]

    out = Path(args.out_dir)
    for r in rows:
        iid, cwd = r["instance_id"], r["cwd"]
        tag = r["docker_image"].split(":")[0]
        image = f"{IMAGE_REPO}:{tag}"
        d = out / iid
        (d / "environment").mkdir(parents=True, exist_ok=True)
        (d / "tests").mkdir(exist_ok=True)
        (d / "task.toml").write_text(
            TASK_TOML.format(
                instance_id=iid,
                image=image,
                verifier_timeout=float(r["verifier_timeout_sec"]),
                agent_timeout=args.agent_timeout,
                cpus=args.cpus,
                memory_mb=memory_for(r, args.memory_mb, args.heavy_memory_mb),
                storage_mb=args.storage_mb,
            )
        )
        (d / "instruction.md").write_text(r["problem_statement"].strip() + f"\n\nThe repository is at `{cwd}`.\n")
        (d / "environment" / "Dockerfile").write_text(DOCKERFILE.format(image=image, cwd=cwd, keep_build_env=int(uses_image_build_env(r["test_patch"]))))
        (d / "environment" / "mimo_setup.sh").write_text(SETUP_SH)
        (d / "tests" / "test.patch").write_text(r["test_patch"])
        test_sh = d / "tests" / "test.sh"
        test_sh.write_text(
            TEST_SH.format(
                cwd=shlex.quote(cwd),
                touched="\n".join(touched_files(r["test_patch"])),
                test_command=r["test_command"],
            )
        )
        test_sh.chmod(0o755)
    print(f"wrote {len(rows)} tasks to {out}")


if __name__ == "__main__":
    main()
