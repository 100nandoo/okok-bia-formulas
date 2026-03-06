#!/bin/zsh

# --- User Profile (EDIT THESE) ---
HEIGHT=165
AGE=31
SEX="male"
CSV_FILE="readings.csv"
# --------------------------------

# Ensure we're in the right directory
cd "/Users/fernando/Codes/okok-bia-formulas"

# Path to uv (adjust if it's elsewhere)
UV_PATH="/opt/homebrew/bin/uv"


# Run the reader script
# The --csv flag makes it exit after one stable reading
$UV_PATH run okok_ble_reader.py \
    --height $HEIGHT \
    --age $AGE \
    --sex $SEX \
    --csv "$CSV_FILE" \
    --output mac