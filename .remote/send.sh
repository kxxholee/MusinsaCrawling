#!/bin/bash

set -e

LOCAL_DIR='../'
REMOTE_ADDRESS='user3@114.70.23.210'
REMOTE_PORT="7099"
REMOTE_DIR='/home/user3/test/'

rsync -avz \
    --filter=':- .gitignore' \
    --exclude='.git' \
    --exclude='remotesync' \
    --exclude='send.sh' \
    --exclude='bring.sh' \
    --exclude='.gitignore' \
    --exclude='CLAUDE.md' \
    -e "ssh -p ${REMOTE_PORT}" \
    "${LOCAL_DIR}" \
    "${REMOTE_ADDRESS}:${REMOTE_DIR}"


