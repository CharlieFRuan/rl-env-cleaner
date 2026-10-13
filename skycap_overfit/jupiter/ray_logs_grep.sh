#!/bin/bash
# usage: srun ... bash ray_logs_grep.sh <ray_tmp> <regex>   (secrets filtered)
D=$1/session_latest/logs
grep -lE "$2" $D/*.out $D/*.err $D/*.log 2>/dev/null | head -10
echo ---
grep -hE "$2" $D/*.out $D/*.err 2>/dev/null | grep -vE "API_KEY|TOKEN|SECRET" | tail -20
