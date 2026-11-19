#!/bin/bash

# vast.ai On-Start Script
# Paste this content into the "On-start script" field in vast.ai configuration.

# 0. Rclone Configuration (OPTIONAL BUT RECOMMENDED)
# Run 'rclone config' locally on your PC, copy the content of ~/.config/rclone/rclone.conf
# and paste it between the EOF markers below.
mkdir -p /root/.config/rclone
cat <<EOF > /root/.config/rclone/rclone.conf
[mydrive]
type = drive
scope = drive
token = {"access_token":"YOUR_ACCESS_TOKEN_HERE","token_type":"Bearer","refresh_token":"YOUR_REFRESH_TOKEN_HERE","expiry":"2026-01-01T00:00:00.000000000-00:00"}
EOF

# 1. Define your Google Drive File ID for the dataset
# REPLACE THIS WITH YOUR ACTUAL FILE ID
DATASET_GDRIVE_ID="1m0ElnC8RA9hvkOwElnwtiLCVX-9wZKkm"
DATASET_FILENAME="sample.tar.gz"

echo "Starting On-Start Script..."

# 2. Download Dataset if it doesn't exist or is empty
if [ ! -d "/workspace/data" ] || [ -z "$(ls -A /workspace/data)" ]; then
    echo "Downloading dataset..."
    mkdir -p /workspace/data
    
    # Try Rclone first (Robust)
    if grep -q "access_token" /root/.config/rclone/rclone.conf; then
        echo "Rclone config found. Using rclone..."
        # Assumes the file is in the root of your Drive
        rclone copy mydrive:$DATASET_FILENAME /workspace/
    else
        echo "No rclone config found. Falling back to gdown..."
        gdown $DATASET_GDRIVE_ID -O /workspace/$DATASET_FILENAME
    fi
    
    echo "Extracting dataset..."
    tar -xzvf /workspace/$DATASET_FILENAME -C /workspace/data
    
    # Cleanup
    rm /workspace/$DATASET_FILENAME
    echo "Dataset ready."
else
    echo "Data directory already exists. Skipping download."
fi

# 3. (Optional) Update code from git
# git pull origin main

echo "On-Start Script Complete."
