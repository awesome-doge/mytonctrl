#!/bin/bash
set -euo pipefail

# Check for root permissions
if [[ "$(id -u)" != "0" ]]; then
    echo "Error: This script must be run as root."
    exit 1
fi

# Default parameters
AUTHOR="ton-blockchain"
REPO="mytonctrl"
BRANCH="master"
SRC_DIR="/usr/src/"
BIN_DIR="/usr/bin/"

# ANSI color codes
GREEN='\033[92m'
RESET='\033[0m'

# Parse arguments
while getopts a:r:b: option; do
    case "${option}" in
        a) AUTHOR=${OPTARG} ;;
        r) REPO=${OPTARG} ;;
        b) BRANCH=${OPTARG} ;;
    esac
done

# Install required Python packages
pip3 install fastcrc

# Navigate to the working directory
pushd ${SRC_DIR}
rm -rf ${SRC_DIR}/${REPO}

# Clone the repository and checkout the specific branch
echo "Fetching: https://github.com/${AUTHOR}/${REPO}.git (Branch: ${BRANCH})"
git clone --recursive https://github.com/${AUTHOR}/${REPO}.git
pushd ${REPO}
git checkout ${BRANCH}
git submodule update --init --recursive

# Restart the service
systemctl restart mytoncore

# Navigate back to the original directory
popd
popd

# Completion message
echo -e "${GREEN}[1/1]${RESET} MyTonCtrl components update completed"
exit 0
