#!/bin/bash
# Image residue probe: run as the verifier with Harbor's nop agent, so it sees the untouched image after mimo_setup.sh.
# Looks for copies of the project that could hold the fixed code: installed packages, node_modules, Go/cargo/maven
# caches, second checkouts, stale bytecode, plus repo mtime clustering. Prints "RESIDUE <kind> ..." lines; reward is 0.
# __CWD__ is substituted per task (residue_audit.py). Diagnostic only: changes nothing in the image.
mkdir -p /logs/verifier; echo 0 > /logs/verifier/reward.txt
# watchdog: the whole scan gets 25 min (verifier limit is 30), so partial findings are still reported
if [ -z "$RESIDUE_INNER" ]; then
  RESIDUE_INNER=1 timeout -k 10 1500 bash "$0"; rc=$?
  [ $rc -ne 0 ] && echo "RESIDUE watchdog rc=$rc"
  exit 0
fi
CWD="__CWD__"
cd "$CWD" || { echo "RESIDUE cwd_missing"; exit 0; }
git config --global --add safe.directory '*' 2>/dev/null
BASE=$(cat /etc/mimo_base_ref 2>/dev/null)
W=$(mktemp -d /tmp/.residue.XXXX)
t0=$(date +%s)
PRUNE='( -path /proc -o -path /sys -o -path /dev -o -path /run -o -path /tests -o -path /logs -o -path /installed-agent -o -path '"$W"' -o -path '"$CWD"'/.git )'
SRC_EXT='py|pyx|pyi|js|mjs|cjs|jsx|ts|tsx|vue|go|rs|java|kt|scala|rb|php|c|h|cc|cpp|hpp|cs|swift|ex|exs|lua|pl|pm|jl|hs|ml|dart|zig|r|R'

# ---------- 0. tracked files of BASE (path -> blob) ----------
git ls-tree -r "$BASE" 2>/dev/null | awk -F'\t' '{split($1,a," "); print a[3]"\t"$2}' > $W/tracked
echo "RESIDUE tracked_files $(wc -l < $W/tracked)"

# ---------- 1. project identity ----------
names=$W/names; : > $names
for f in $(git ls-tree -r --name-only "$BASE" 2>/dev/null | grep -E '(^|/)(package\.json|pyproject\.toml|setup\.py|setup\.cfg|go\.mod|Cargo\.toml|pom\.xml|composer\.json|[^/]*\.gemspec)$' \
           | grep -vE '(^|/)(node_modules|test|tests|testdata|fixtures?|examples?|docs?|__tests__|e2e)/' | awk -F/ 'NF<=4' | head -60); do
  case "$f" in
    *package.json) n=$(grep -m1 -oE '"name"[[:space:]]*:[[:space:]]*"[^"]+"' "$f" | sed -E 's/.*"([^"]+)"$/\1/'); k=npm ;;
    *pyproject.toml) n=$(awk '/^\[(project|tool\.poetry)\]/{s=1;next} /^\[/{s=0} s&&/^name[[:space:]]*=/{print;exit}' "$f" | sed -E "s/.*=[[:space:]]*['\"]([^'\"]+).*/\1/"); k=py ;;
    *setup.py) n=$(grep -m1 -oE "name[[:space:]]*=[[:space:]]*['\"][^'\"]+['\"]" "$f" | sed -E "s/.*['\"]([^'\"]+)['\"]$/\1/"); k=py ;;
    *setup.cfg) n=$(awk '/^\[metadata\]/{s=1;next} /^\[/{s=0} s&&/^name[[:space:]]*=/{print;exit}' "$f" | sed -E 's/.*=[[:space:]]*//'); k=py ;;
    *go.mod) n=$(awk '/^module /{print $2; exit}' "$f"); k=go ;;
    *Cargo.toml) n=$(awk '/^\[package\]/{s=1;next} /^\[/{s=0} s&&/^name[[:space:]]*=/{print;exit}' "$f" | sed -E 's/.*"([^"]+)".*/\1/'); k=cargo ;;
    *pom.xml) n=$(grep -v '<parent>' "$f" | awk '/<parent>/{p=1} /<\/parent>/{p=0;next} !p' | grep -m1 -oE '<artifactId>[^<]+' | sed 's/<artifactId>//'); k=maven ;;
    *composer.json) n=$(grep -m1 -oE '"name"[[:space:]]*:[[:space:]]*"[^"]+"' "$f" | sed -E 's/.*"([^"]+)"$/\1/'); k=composer ;;
    *.gemspec) n=$(grep -m1 -oE "\.name[[:space:]]*=[[:space:]]*['\"][^'\"]+" "$f" | sed -E "s/.*['\"]//"); k=gem ;;
  esac
  [ -n "$n" ] && case "$n" in *'$'*|*'{'*) ;; *) echo "$k $n $f" >> $names ;; esac
done
sort -u -k1,2 $names -o $names
sed 's/^/RESIDUE name /' $names | head -40
ver=$(git show "$BASE":package.json 2>/dev/null | grep -m1 -oE '"version"[[:space:]]*:[[:space:]]*"[^"]+"' | sed -E 's/.*"([^"]+)"$/\1/')
[ -z "$ver" ] && ver=$(git show "$BASE":pyproject.toml 2>/dev/null | grep -m1 -E '^version[[:space:]]*=' | sed -E "s/.*['\"]([^'\"]+).*/\1/")
echo "RESIDUE repo_version ${ver:-?} describe=$(git describe --tags "$BASE" 2>/dev/null || echo -)"

# ---------- 2. candidate installed copies ----------
cands=$W/cands; : > $cands   # kind<TAB>dir<TAB>label
norm() { echo "$1" | tr 'A-Z' 'a-z' | sed -E 's/[-_.]+/_/g'; }
# One walk of the filesystem for the dirs we care about.
echo "RESIDUE phase start"
timeout 600 find / $PRUNE -prune -o \( -type d \( -name '*.dist-info' -o -name '*.egg-info' -o -name node_modules -o -name '.git' -o -name 'mod' -o -name 'registry' -o -name 'repository' -o -name __pycache__ \) -print \) \
  -o \( -type f \( -name '*.egg-link' -o -name '__editable__*' -o -name '*.pth' \) -print \) 2>/dev/null > $W/walk
# all regular files (for the token scan and the second-checkout search)
timeout 900 find / $PRUNE -prune -o -type f -size -20M -print 2>/dev/null > $W/allfiles
echo "RESIDUE fs_files $(wc -l < $W/allfiles) walk_s=$(( $(date +%s) - t0 ))"
while read -r k n f; do
  case $k in
    py) nn=$(norm "$n")
      grep -E '\.(dist|egg)-info$' $W/walk | while read -r d; do
        b=$(basename "$d"); dn=$(norm "${b%%-[0-9]*}"); dn=${dn%_dist_info}; dn=${dn%_egg_info}
        [ "$dn" = "$nn" ] || continue
        case "$d" in "$CWD"/*.egg-info|"$CWD"/src/*.egg-info) continue ;; esac   # in-tree metadata of a develop install
        sp=$(dirname "$d")
        tops=$( { cat "$d/top_level.txt" 2>/dev/null; [ -f "$d/RECORD" ] && cut -d, -f1 "$d/RECORD" | grep -vE '\.(dist|egg)-info/|^\.\./|__pycache__' | cut -d/ -f1 | sed 's/\.py$//'; } | sort -u | grep -v '^$' | head -10)
        echo "RESIDUE py_dist $d version=$(grep -m1 '^Version:' "$d/METADATA" "$d/PKG-INFO" 2>/dev/null | head -1 | awk '{print $2}') tops=$(echo $tops | tr ' ' ',') direct_url=$(grep -oE '"url": *"[^"]+"' "$d/direct_url.json" 2>/dev/null | head -1 | tr -d ' ')"
        for t in $tops; do
          if [ -d "$sp/$t" ] && [ ! -L "$sp/$t" ]; then printf 'py\t%s\t%s\n' "$sp/$t" "$t" >> $cands
          elif [ -f "$sp/$t.py" ]; then printf 'pyfile\t%s\t%s\n' "$sp/$t.py" "$t" >> $cands; fi
        done
      done ;;
    npm) grep '/node_modules$' $W/walk | while read -r nm; do
        d="$nm/$n"; [ -e "$d" ] || continue
        if [ -L "$d" ]; then r=$(readlink -f "$d"); case "$r" in "$CWD"|"$CWD"/*) continue ;; esac
          echo "RESIDUE npm_link_outside $d -> $r"; continue; fi
        echo "RESIDUE npm_copy $d version=$(grep -m1 -oE '"version"[[:space:]]*:[[:space:]]*"[^"]+"' "$d/package.json" 2>/dev/null | sed -E 's/.*"([^"]+)"$/\1/')"
        printf 'npm\t%s\t%s\n' "$d" "$(dirname "$f" | sed 's#^\.$##')" >> $cands
      done ;;
    go) for root in $(grep -E '/pkg/mod$' $W/walk); do
        for d in "$root/$(echo "$n" | sed -E 's/[A-Z]/!\L&/g')"@*; do [ -d "$d" ] || continue
          echo "RESIDUE go_mod_copy $d"; printf 'go\t%s\t%s\n' "$d" "$(dirname "$f" | sed 's#^\.$##')" >> $cands; done
        ls -d "$root/cache/download/$n/@v/"*.zip 2>/dev/null | head -5 | sed 's/^/RESIDUE go_mod_zip /'
      done ;;
    cargo) for d in $(grep -E '/registry$' $W/walk); do ls -d "$d"/src/*/"$n"-[0-9]* 2>/dev/null; done | while read -r d; do
        echo "RESIDUE cargo_copy $d"; printf 'cargo\t%s\t%s\n' "$d" "$(dirname "$f" | sed 's#^\.$##')" >> $cands; done ;;
    maven) for d in $(grep -E '/\.m2/repository$' $W/walk); do find "$d" -type d -name "$n" 2>/dev/null | head -5; done | while read -r d; do
        echo "RESIDUE m2_artifact $d versions=$(ls "$d" | tr '\n' ',' | cut -c1-200)"; done ;;
    composer|gem) for d in $(find / $PRUNE -prune -o -type d -path "*/vendor/$n" -print 2>/dev/null | head -5) \
                          $(find / $PRUNE -prune -o -type d -path "*/gems/$n-[0-9]*" -print 2>/dev/null | head -5); do
        echo "RESIDUE ${k}_copy $d"; printf '%s\t%s\t%s\n' "$k" "$d" "$(dirname "$f" | sed 's#^\.$##')" >> $cands; done ;;
  esac
done < $names
# editable installs / .pth files pointing somewhere other than the repo
grep -E '\.(egg-link|pth)$|__editable__' $W/walk | while read -r p; do
  case "$p" in "$CWD"/*) continue ;; esac
  grep -hoE '^/[^ ]+' "$p" 2>/dev/null | head -3 | while read -r tgt; do
    case "$tgt" in "$CWD"|"$CWD"/*|/usr/*|/opt/conda*|/root/.pyenv*|/usr/local/lib*) ;; *) [ -d "$tgt" ] && echo "RESIDUE pth_points_elsewhere $p -> $tgt" ;; esac
  done
done

# ---------- 3. diff each copy against BASE ----------
# For every source file in the copy, find the BASE path that ends with the same relative path; compare blob ids.
sort -u $cands | head -40 | while IFS=$'\t' read -r kind d label; do
  if [ "$kind" = pyfile ]; then (cd "$(dirname "$d")" && echo "$(basename "$d")") ; rel_prefix="";
  else (cd "$d" && find . -type f 2>/dev/null | sed 's#^\./##' | grep -E "\.($SRC_EXT)$" | grep -v '/node_modules/' | head -20000); fi > $W/cfiles
  [ -s $W/cfiles ] || { echo "RESIDUE copy_empty $kind $d"; continue; }
  base_dir=$d; [ "$kind" = pyfile ] && base_dir=$(dirname "$d")
  # blob ids, outside any repo (no .gitattributes / safe.directory surprises); a batch that errors is redone per file
  : > $W/chash
  split -l 200 -d -a 4 $W/cfiles $W/cbatch.
  for b in $W/cbatch.*; do
    if ! (cd $W && sed "s#^#$base_dir/#" "$b" | GIT_CEILING_DIRECTORIES=/ timeout 60 xargs -d '\n' git hash-object --no-filters -- 2>/dev/null) > $W/hb \
       || [ "$(wc -l < $W/hb)" -ne "$(wc -l < "$b")" ]; then
      while IFS= read -r x; do (cd $W && GIT_CEILING_DIRECTORIES=/ git hash-object --no-filters -- "$base_dir/$x" 2>/dev/null) || echo unreadable; done < "$b" > $W/hb
    fi
    cat $W/hb >> $W/chash; rm -f "$b"
  done
  [ "$(wc -l < $W/chash)" -eq "$(wc -l < $W/cfiles)" ] || { echo "RESIDUE copy_hash_failed $kind $d"; continue; }
  case $kind in py|pyfile) pre="$label/"; [ "$kind" = pyfile ] && pre="" ;; *) pre="" ;; esac
  # Match copy files to BASE paths by path suffix. A copy (e.g. a monorepo package) maps to one repo directory: learn
  # that prefix from byte-identical matches, then compare differing files only under it (no index.js lookalikes).
  paste $W/cfiles $W/chash > $W/cpairs
  awk -F'\t' -v pre="$pre" -v kind="$kind" -v d="$d" -v bd="$base_dir" '
    function cands(rel,   q, m, k, cs, i, c, out) { m=split(rel,q,"/"); k=split(bn[q[m]],cs,"\n"); out="";
      for(i=1;i<=k;i++){ c=cs[i]; if(c=="")continue; if(c==rel || substr(c,length(c)-length(rel))=="/" rel) out=out "\n" c }
      return out }
    FILENAME==ARGV[1] { blob[$2]=$1; byblob[$1]=1; n=split($2,p,"/"); bn[p[n]]=bn[p[n]] "\n" $2; next }
    { rel[FNR]=pre $1; h[FNR]=$2; N=FNR
      k=split(cands(rel[FNR]),cs,"\n"); for(i=1;i<=k;i++){ c=cs[i]; if(c!="" && blob[c]==h[FNR]){ px=substr(c,1,length(c)-length(rel[FNR])); pc[px]++; ident[FNR]=1; break } } }
    END {
      best=""; bc=-1; for(x in pc) if(pc[x]>bc){bc=pc[x]; best=x}
      for(j=1;j<=N;j++){
        if(ident[j]){ matched++; same++; continue }
        if(bc>=0){ c=best rel[j]; if(!(c in blob)){ only++; continue } }
        else { k=split(cands(rel[j]),cs,"\n"); c=""; for(i=1;i<=k;i++) if(cs[i]!=""&&(c==""||length(cs[i])<length(c))) c=cs[i]; if(c==""){only++; continue} }
        matched++; diff++; if(nd<15) dl=dl " " c; if(nd<300) print c "\t" bd "/" substr(rel[j],length(pre)+1) > "/dev/stderr"; nd++ }
      printf "RESIDUE copy_vs_base kind=%s dir=%s files=%d matched=%d same=%d differ=%d copy_only=%d prefix=%s\n", kind, d, N, matched, same, diff, only, (bc>=0?best:"?");
      if(diff>0) printf "RESIDUE copy_differ_files dir=%s%s\n", d, dl }' $W/tracked $W/cpairs 2> $W/dpairs
  head -4 $W/dpairs 2>/dev/null | while IFS=$'\t' read -r bp cp; do
    git show "$BASE:$bp" > $W/bfile 2>/dev/null
    echo "RESIDUE copy_diffstat $(diff $W/bfile "$cp" | grep -c '^[<>]') lines $bp vs $cp"
  done
  # Is each differing copy file an older version of the repo file (its blob occurs in BASE's history of that path)?
  # "future" = never in history: written after BASE (or a fork / build product) -> the leak candidates.
  if [ -s $W/dpairs ]; then
    old=0; fut=0; fl=""
    while IFS=$'\t' read -r bp cp; do
      h=$(cd $W && GIT_CEILING_DIRECTORIES=/ git hash-object --no-filters -- "$cp" 2>/dev/null)
      if timeout 20 git log --raw --no-abbrev --format= "$BASE" -- "$bp" 2>/dev/null | awk '{print $4}' | grep -qx "$h"; then old=$((old+1))
      else fut=$((fut+1)); [ $fut -le 6 ] && fl="$fl $bp"; echo "RESIDUE future_pair $bp $cp"; fi
    done < <(head -150 $W/dpairs)
    echo "RESIDUE copy_history dir=$d in_history=$old not_in_history=$fut shallow=$([ -f .git/shallow ] && echo 1 || echo 0) commits=$(git rev-list --count "$BASE")$fl"
  fi
done
echo "RESIDUE phase copies_done t=$(( $(date +%s) - t0 ))s"

# ---------- 4. novel identifiers from the hidden tests, anywhere outside the tracked tree ----------
# Identifiers the test patch adds that appear nowhere in BASE are what the fix introduces; finding them in an
# untracked file (installed copy, cache, doc, other checkout) means that file comes from after the fix.
if [ -f /tests/test.patch ]; then
  # skip the harness script (mimo_test_command.sh) the patch also adds: its env-var names are not the fix's
  awk '/^diff --git /{skip=($0 ~ /mimo_test_command\.sh|\.build_env\//)} !skip' /tests/test.patch > $W/tp
  grep -E '^\+[^+]' $W/tp | grep -oE '[A-Za-z_][A-Za-z0-9_]{5,}' | sort -u > $W/tok_add
  grep -E '^[ -][^-]' $W/tp | grep -oE '[A-Za-z_][A-Za-z0-9_]{5,}' | sort -u > $W/tok_ctx
  comm -23 $W/tok_add $W/tok_ctx | head -3000 > $W/tok
  if [ -s $W/tok ]; then
    git grep -h -o -w -F -f $W/tok "$BASE" 2>/dev/null | sed "s/^$BASE://" | sort -u > $W/tok_base
    comm -23 $W/tok $W/tok_base | grep -vE '^(test|Test|TEST)_?[a-z]*$' | head -400 > $W/novel
  else : > $W/novel; fi
  echo "RESIDUE novel_tokens $(wc -l < $W/novel) $(head -12 $W/novel | tr '\n' ' ')"
  if [ -s $W/novel ]; then
    # all regular files outside pseudo-fs, minus tracked files of the repo (those are the agent's to edit)
    grep -vE '\.(png|jpg|jpeg|gif|ico|woff2?|ttf|otf|so(\.[0-9.]+)?|a|o|zip|gz|xz|bz2|zst|whl|jar|tar|pdf|mo|db|sqlite|pak|sym|typelib|mgc)$' $W/allfiles \
      | grep -vE '^/etc/(environment|profile\.d/)|/share/(doc|man|locale)/|^/usr/src/|/\.build_env/test_command\.sh$' \
      | awk -F'\t' -v c="$CWD/" 'NR==FNR{t[c $2]=1;next} !($0 in t)' $W/tracked - > $W/scan
    echo "RESIDUE scan_files $(wc -l < $W/scan) (of $(wc -l < $W/allfiles))"
    tr '\n' '\0' < $W/scan | timeout 600 xargs -0 -P 2 -n 500 grep -a -c -w -F -f $W/novel 2>/dev/null | awk -F: '$NF>0' > $W/hits
    echo "RESIDUE novel_hit_files $(wc -l < $W/hits)"
    # Token rarity: generic words (Content, base64, ...) occur all over the image; a token that the fix introduces
    # occurs in few files. Keep tokens found in <= 5 files ("rare"), rank files by distinct rare tokens.
    sort -t: -k2,2n $W/hits | cut -d: -f1 | head -8000 | tr '\n' '\0' | timeout 300 xargs -0 -n 300 grep -a -o -w -H -F -f $W/novel 2>/dev/null | sort -u > $W/pairs
    awk -F: '{df[$NF]++} END{for(t in df) print df[t]"\t"t}' $W/pairs | sort -n > $W/df
    awk -F'\t' '$1<=5{print $2}' $W/df > $W/rare
    echo "RESIDUE rare_tokens $(wc -l < $W/rare) $(head -15 $W/rare | tr '\n' ' ')"
    awk -F: 'NR==FNR{r[$0]=1;next} { t=$NF; f=substr($0,1,length($0)-length(t)-1); if(t in r){n[f]++; if(n[f]<=6) tk[f]=tk[f] t ","} }
             END{for(f in n) print n[f]"\t"tk[f]"\t"f}' $W/rare $W/pairs | sort -t$'\t' -k1,1nr | head -40 \
      | while IFS=$'\t' read -r n tk f; do echo "RESIDUE rare_hit distinct=$n tokens=$tk $f"; done
  fi
fi

echo "RESIDUE phase tokens_done t=$(( $(date +%s) - t0 ))s"
# ---------- 5. second checkouts ----------
grep -E '/\.git$' $W/walk | grep -vxF "$CWD/.git" | while read -r g; do
  r=$(dirname "$g"); case "$r" in "$CWD"/*) sub=1 ;; *) sub=0 ;; esac
  root=$(git -C "$r" rev-list --max-parents=0 HEAD 2>/dev/null | tail -1)
  myroot=$(git rev-list --max-parents=0 "$BASE" 2>/dev/null | tail -1)
  echo "RESIDUE git_dir $r inside_cwd=$sub same_root=$([ -n "$root" ] && [ "$root" = "$myroot" ] && echo 1 || echo 0) head=$(git -C "$r" log -1 --format='%h %cI' 2>/dev/null) refs=$(git -C "$r" for-each-ref 2>/dev/null | wc -l)"
done
# directories outside the repo holding the same top-level marker file with identical project name
while read -r k n f; do
  case "$f" in */*) continue ;; esac   # only the root manifest
  bn=$(basename "$f")
  grep -F "/$bn" $W/allfiles | grep "/$bn\$" | grep -v "^$CWD/" | grep -v '/node_modules/\|/site-packages/\|/dist-packages/\|/pkg/mod/\|/registry/src/' | head -2000 | while read -r g; do
    grep -qF "$n" "$g" 2>/dev/null || continue
    dd=$(dirname "$g"); same=0; tot=0
    for x in $(git ls-tree --name-only "$BASE" | head -30); do [ -e "$dd/$x" ] && same=$((same+1)); tot=$((tot+1)); done
    [ $same -ge 3 ] && echo "RESIDUE second_checkout $dd marker=$bn toplevel_overlap=$same/$tot"
  done
done < $names

# ---------- 6. bytecode in the repo ----------
orph=0; stale=0; tot=0
for pc in $(grep -E "^$CWD/.*/__pycache__$|^$CWD/__pycache__$" $W/walk | grep -v '/node_modules/\|/\.venv/\|/site-packages/' | head -3000); do
  for p in "$pc"/*.pyc; do [ -f "$p" ] || continue; tot=$((tot+1))
    s="$(dirname "$pc")/$(basename "$p" | sed -E 's/\.(cpython|pypy)[^.]*\.pyc$/.py/; s/\.pyc$/.py/')"
    if [ ! -f "$s" ]; then orph=$((orph+1)); [ $orph -le 10 ] && echo "RESIDUE pyc_orphan ${p#$CWD/}"; continue; fi
    read -r flags mt sz <<< "$(od -An -tu4 -j4 -N12 "$p" 2>/dev/null)"
    if [ "$flags" = 0 ] && { [ "$mt" != "$(stat -c %Y "$s")" ] || [ "$sz" != "$(stat -c %s "$s")" ]; }; then
      stale=$((stale+1)); [ $stale -le 10 ] && echo "RESIDUE pyc_stale ${p#$CWD/} pyc_mtime=$mt src_mtime=$(stat -c %Y "$s") pyc_size=$sz src_size=$(stat -c %s "$s")"
    fi
  done
done
echo "RESIDUE pyc_summary total=$tot orphan=$orph stale=$stale"

# ---------- 7. mtime clustering of tracked files ----------
cut -f2 $W/tracked | head -50000 | tr '\n' '\0' | xargs -0 stat -c '%Y %n' 2>/dev/null | sort -n > $W/mt
if [ -s $W/mt ]; then
  med=$(awk '{a[NR]=$1} END{print a[int((NR+1)/2)]}' $W/mt)
  late=$(awk -v m=$med '$1>m+86400' $W/mt | wc -l)
  echo "RESIDUE mtime median=$med distinct_days=$(awk '{print int($1/86400)}' $W/mt | sort -u | wc -l) newer_than_median_plus_1d=$late base_commit_time=$(git log -1 --format=%ct "$BASE")"
  [ "$late" -gt 0 ] && [ "$late" -le 200 ] && tail -8 $W/mt | sed 's/^/RESIDUE mtime_newest /'
fi
echo "RESIDUE done elapsed=$(( $(date +%s) - t0 ))s"
rm -rf $W
exit 0
