#!/bin/bash
set -euo pipefail

# Ensure script is run with root permissions
if [[ "$(id -u)" != "0" ]]; then
    echo "Error: This script must be run as root."
    exit 1
fi

# Default parameters
USER=""

# ANSI color codes
MAGENTA='\033[95m'
RESET='\033[0m'

# Parse arguments
while getopts u: option; do
    case "${option}" in
        u) USER=${OPTARG} ;;
    esac
done

# Validate user argument
if [[ -z "${USER}" ]]; then
    echo "Error: Missing -u (user) argument."
    exit 1
fi

# Function to print step
print_step() {
    echo -e "${MAGENTA}[$1/4]${RESET} $2"
}

# Install python3 components
print_step 1 "Installing required packages"
pip3 install Werkzeug json-rpc cloudscraper pyotp

# Clone repositories from GitHub
print_step 2 "Cloning github repository"
REPO_DIR="/usr/src/mtc-jsonrpc"
if [[ -d "${REPO_DIR}" ]]; then
    rm -rf "${REPO_DIR}"
fi
git clone --recursive https://github.com/igroman787/mtc-jsonrpc.git "${REPO_DIR}"

# Add to system startup
print_step 3 "Add to startup"
PYTHON_CMD="from sys import path
path.append('/usr/src/mytonctrl/')
from mypylib.mypylib import *
Add2Systemd(name='mtc-jsonrpc', user='${USER}', start='/usr/bin/python3 /usr/src/mtc-jsonrpc/mtc-jsonrpc.py')"
python3 -c "${PYTHON_CMD}"
systemctl restart mtc-jsonrpc

# Completion message
print_step 4 "JsonRPC installation complete"
exit 0
