#!/bin/bash
if [[ -z $CLIENT ]]; then
    echo "CLIENT environment variable not set"
    exit 1
fi
if [[ -z $TINYFL_CONFIG ]]; then
    echo "TINYFL_CONFIG environment variable not set"
    exit 1
fi
if [[ $CLIENT -eq 0 ]]; then
    echo "Starting aggregator"
    exec psrecord "poetry run agg $TINYFL_CONFIG" --log perf.log --include-children --interval 1
    exit 0
elif [[ $CLIENT -eq 1 ]]; then
    echo "Starting party"
    exec psrecord "poetry run party $TINYFL_CONFIG" --log perf.log --include-children --interval 1
    exit 0
elif [[ $CLIENT -eq 2 ]]; then 
    echo "Starting super agg"
    exec psrecord "poetry run sup $TINYFL_CONFIG" --log perf.log --include-children --interval 1
else
    echo "CLIENT environment variable must be 0 or 1"
    exit 1
fi
