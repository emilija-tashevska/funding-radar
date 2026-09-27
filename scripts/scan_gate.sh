#!/usr/bin/env bash
# Should this scheduled run go ahead? Prints go=true or go=false.
#
#   scan_gate.sh <event_name> <cron that fired> <London UTC offset, e.g. +0100>
#
# 07:00 and 17:00 London are 06:00 and 16:00 UTC in summer time and 07:00 and
# 17:00 UTC in winter. Each run is judged by the cron that fired it and today's
# offset, so a run GitHub starts hours late still goes, and exactly one of each
# pair runs.
event="$1"
cron="$2"
offset="$3"

if [ "$event" != "schedule" ]; then
  echo "go=true"
  exit 0
fi

utc_hour=$(echo "$cron" | awk '{print $2}')
case "$offset" in
  +0100) wanted="6 16" ;;
  +0000) wanted="7 17" ;;
  *) echo "Unexpected London offset '$offset'; running anyway." >&2; echo "go=true"; exit 0 ;;
esac

for hour in $wanted; do
  if [ "$utc_hour" = "$hour" ]; then
    echo "go=true"
    exit 0
  fi
done
echo "Skipping: cron '$cron' is the other clock's slot while London is at $offset." >&2
echo "go=false"
