#!/usr/bin/bash

LOCAL_DIR="/home/vnla/Univ/Workbench/Toy"
REMOTE_ADDRESS="user3@114.70.23.210"
REMOTE_PORT="7099"
REMOTE_DIR="/home/user3/test/"

rsync -avz \
    --filter=':- .gitignore' \
    --exclude='.git' \
    --exclude='remotesync' \
    --exclude='send.sh' \
    --exclude='bring.sh' \
    --exclude='.gitignore' \
    -e "ssh -p ${REMOTE_PORT}" \
    "${REMOTE_ADDRESS}:${REMOTE_DIR}" \
    "${LOCAL_DIR}" \



