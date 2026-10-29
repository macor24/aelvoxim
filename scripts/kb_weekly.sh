#!/bin/bash
# Weekly KB upkeep: stop the writer, prune duplicate titles + catch index/vectors up, restart, report.
# Installed as ExecStart of aelvoxim-kbgc.service (see deploy/). Safe to run by hand.
set -u
PY=/opt/aelvoxim/venv/bin/python
K=/opt/aelvoxim/scripts/kb_gc.py

echo "=== kb_weekly $(date '+%F %T')"
systemctl stop aelvoxim-learner
sleep 2

$PY $K --weekly --apply
rc=$?

systemctl start aelvoxim-learner
sleep 5
systemctl is-active aelvoxim-learner | sed 's/^/learner: /'

echo "--- final check"
$PY $K --check
exit $rc
