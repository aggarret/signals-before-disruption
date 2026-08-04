#!/bin/bash
# run-update.sh — Manual data update with logging
cd /Users/alfredleegarrettjr/dev/signals-before-disruption
.venv/bin/python3 update_data.py 2>&1 | tee -a data/update-manual.log
.venv/bin/python3 scripts/healthcheck.py 2>&1 | tee -a data/update-manual.log
