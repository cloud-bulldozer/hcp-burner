#!/bin/bash
#
# Script to wait until a specific datetime. This allows you to run a script at a more precise time, example:
#
# # ./wait_until.sh "2025-08-18 18:38:00"; date -u
# Waiting for 24 seconds until 2025-08-18 18:38:00...
# Time reached! Resuming execution.
# Mon Aug 18 06:38:00 PM UTC 2025
#

# Check if a target datetime parameter was provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 \"YYYY-MM-DD HH:MM:SS\""
    exit 1
fi

TARGET_DATETIME="$1"

# Convert target datetime to a Unix timestamp
TARGET_EPOCH=$(date -d "$TARGET_DATETIME" +%s)

# Get current Unix timestamp
CURRENT_EPOCH=$(date +%s)

# Calculate the sleep duration
SLEEP_DURATION=$((TARGET_EPOCH - CURRENT_EPOCH))

# Check if the target time is in the future
if [ "$SLEEP_DURATION" -gt 0 ]; then
    echo "Waiting for $SLEEP_DURATION seconds until $TARGET_DATETIME..."
    sleep "$SLEEP_DURATION"
    echo "Time reached! Resuming execution."
else
    echo "The specified time '$TARGET_DATETIME' is in the past. Exiting."
    exit 1
fi
