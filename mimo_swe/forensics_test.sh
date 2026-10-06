#!/bin/bash
# Image forensics (run with the nop agent, so this is the untouched image state). Prints FORENSIC key=value lines.
mkdir -p /logs/verifier; echo 0 > /logs/verifier/reward.txt
CWD="__CWD__"
cd "$CWD" || { echo "FORENSIC cwd_missing=1"; exit 0; }
git config --global --add safe.directory '*'
BASE=$(cat /etc/mimo_base_ref 2>/dev/null)
bt=$(git log -1 --format=%ct "$BASE" 2>/dev/null)
echo "FORENSIC base=$BASE base_date=$(git log -1 --format=%cI "$BASE" 2>/dev/null)"
echo "FORENSIC commits_in_history=$(git rev-list --count "$BASE" 2>/dev/null)"
echo "FORENSIC refs=$(git for-each-ref | wc -l) tags=$(git tag | wc -l)"
echo "FORENSIC future_reachable=$(git rev-list --all --not "$BASE" 2>/dev/null | wc -l)"
for r in $(git for-each-ref --format='%(refname)'); do
  n=$(git rev-list "$r" --not "$BASE" 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] && echo "FORENSIC future_ref $r +$n $(git log -1 --format='%cI %s' "$r" | cut -c1-100)"
done
echo "FORENSIC stash=$(git stash list 2>/dev/null | wc -l)"
git stash list 2>/dev/null | head -3 | sed 's/^/FORENSIC stash_entry /'
git stash show --stat 'stash@{0}' 2>/dev/null | tail -4 | sed 's/^/FORENSIC stash_stat /'
echo "FORENSIC reflog_entries=$(git reflog 2>/dev/null | wc -l)"
newer=0; total=0
for c in $(git fsck --unreachable --no-reflogs --no-progress 2>/dev/null | awk '/ commit /{print $3}'); do
  total=$((total+1)); ct=$(git log -1 --format=%ct "$c" 2>/dev/null)
  if [ -n "$bt" ] && [ -n "$ct" ] && [ "$ct" -gt "$bt" ]; then
    newer=$((newer+1)); [ $newer -le 5 ] && echo "FORENSIC dangling_newer $c $(git log -1 --format='%cI %s' "$c" | cut -c1-100) files=$(git show --stat --format= "$c" | tail -1)"
  fi
done
echo "FORENSIC dangling_commits=$total dangling_newer_than_base=$newer"
echo "FORENSIC orig_head=$([ -f .git/ORIG_HEAD ] && echo 1 || echo 0) fetch_head=$([ -f .git/FETCH_HEAD ] && echo 1 || echo 0)"
# hidden tests already present?
if git apply --check --reverse /tests/test.patch >/dev/null 2>&1; then echo "FORENSIC test_patch_already_applied=1"; else echo "FORENSIC test_patch_already_applied=0"; fi
pre=0; for f in $(grep -A1 '^new file mode' /tests/test.patch -B2 | grep -oE '^diff --git a/\S+' | sed 's#diff --git a/##'); do [ -e "$f" ] && { pre=$((pre+1)); echo "FORENSIC new_file_exists $f"; }; done
echo "FORENSIC patch_new_files_present=$pre"
echo "FORENSIC build_env_present=$([ -e .build_env ] && echo 1 || echo 0) mimo_files=$(ls -a | grep -c '^mimo_')"
ls -a | grep -E '^(\.build_env|mimo_|test_commands\.json|usercase-test-coderl)' | sed 's/^/FORENSIC leftover /'
exit 0
