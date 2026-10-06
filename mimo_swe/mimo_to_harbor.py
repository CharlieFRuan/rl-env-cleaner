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
RUN sh /tmp/mimo_setup.sh {cwd} && rm -f /tmp/mimo_setup.sh
WORKDIR {cwd}
"""

SETUP_SH = r"""#!/bin/sh
# Make a MiMo image usable outside Xiaomi's cluster. $1 = repository dir.
set -e
CWD="$1"

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("out_dir")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ids", default=None, help="comma-separated instance_ids")
    ap.add_argument("--agent-timeout", type=float, default=3600.0)
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--memory-mb", type=int, default=8192)  # MiMo k8s sandbox limit: 4 CPU / 8Gi
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
                memory_mb=args.memory_mb,
                storage_mb=args.storage_mb,
            )
        )
        (d / "instruction.md").write_text(r["problem_statement"].strip() + f"\n\nThe repository is at `{cwd}`.\n")
        (d / "environment" / "Dockerfile").write_text(DOCKERFILE.format(image=image, cwd=cwd))
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
