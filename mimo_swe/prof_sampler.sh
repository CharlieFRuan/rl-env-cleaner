#!/bin/sh
# Resource sampler for the profiling pass. Interval default 1 s, timed with /proc/uptime. Started in the background right after the sandbox starts
# (LabeledDaytonaEnv, MIMO_PROFILE=1), so it covers agent install, the agent run and the verifier.
# POSIX sh + coreutils only (task images vary). Every $1 seconds (default 1) it appends one CSV line to
# $D/series.csv and rewrites $D/peaks with the running maxima. Reads the sandbox's own cgroup v2 files:
#   memory.current (total incl. page cache), memory.stat anon/file/kernel/shmem (anon+kernel+shmem = the part
#   that can't be reclaimed), cpu.stat usage_usec (cores used over the interval), pids.current, and `df /`
#   (overlay upper layer = bytes written by this sandbox on top of the image).
# The verifier (TEST_SH) prints $D/peaks plus the lifetime counters and copies series.csv to /logs/verifier/.
D=/var/lib/.mimo_prof; mkdir -p $D
I=${1:-1}; C=/sys/fs/cgroup
# Milliseconds from `date +%s%N` (GNU coreutils). /proc/uptime is virtualized in the sandbox (container uptime,
# read back as 0), and `date +%s` alone (1 s) made 2-s rates up to ~30% too high. Fallback: whole seconds.
now_ms() { _n=$(date +%s%N 2>/dev/null); case $_n in *[!0-9]*|"") echo $(( $(date +%s) * 1000 ));; *) echo $(( _n / 1000000 ));; esac; }
echo "t,mem_current,anon,file,kernel,shmem,cpu_cores,disk_used_kb,pids" > $D/series.csv
pk_mem=0; pk_nonfile=0; pk_anon=0; pk_cores_milli=0; pk_disk=0; pk_pids=0
prev_ms=$(now_ms); prev_u=0
while read -r k v; do [ "$k" = usage_usec ] && prev_u=$v; done < $C/cpu.stat
n=0
while :; do
  sleep "$I"
  ms=$(now_ms); t=$((ms / 1000))
  mem=$(cat $C/memory.current 2>/dev/null || echo 0)
  anon=0; file=0; kern=0; shm=0
  while read -r k v; do
    case $k in anon) anon=$v;; file) file=$v;; kernel) kern=$v;; shmem) shm=$v;; esac
  done < $C/memory.stat
  u=$prev_u; while read -r k v; do [ "$k" = usage_usec ] && u=$v; done < $C/cpu.stat
  dms=$((ms - prev_ms)); [ $dms -le 0 ] && dms=1
  milli=$(( (u - prev_u) / dms ))             # milli-cores = usage_usec / interval_ms
  prev_ms=$ms; prev_u=$u
  set -- $(df -kP / 2>/dev/null | tail -1); disk=${3:-0}
  pids=$(cat $C/pids.current 2>/dev/null || echo 0)
  nonfile=$((anon + kern + shm))
  [ $mem -gt $pk_mem ] && pk_mem=$mem
  [ $nonfile -gt $pk_nonfile ] && pk_nonfile=$nonfile
  [ $anon -gt $pk_anon ] && pk_anon=$anon
  [ $milli -gt $pk_cores_milli ] && pk_cores_milli=$milli
  [ $disk -gt $pk_disk ] && pk_disk=$disk
  [ $pids -gt $pk_pids ] && pk_pids=$pids
  echo "$t,$mem,$anon,$file,$kern,$shm,$milli,$disk,$pids" >> $D/series.csv
  n=$((n + 1))
  echo "samples=$n interval_s=$I peak_mem_current=$pk_mem peak_nonfile=$pk_nonfile peak_anon=$pk_anon peak_cores_milli=$pk_cores_milli peak_disk_used_kb=$pk_disk peak_pids=$pk_pids last_t=$t" > $D/peaks.tmp
  mv $D/peaks.tmp $D/peaks
done
