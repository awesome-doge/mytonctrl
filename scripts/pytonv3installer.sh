#!/bin/bash
set -e

# Ensure the script is run as root
if [ "$(id -u)" != "0" ]; then
    echo "Error: Please run this script as root."
    exit 1
fi

# Define colors for output
COLOR_GREEN='\033[92m'
COLOR_RESET='\033[0m'

# Parse script arguments
while getopts u: flag; do
    case "${flag}" in
        u) user=${OPTARG};;
    esac
done

# If user is not specified, exit
if [ -z "${user}" ]; then
    echo "Error: User not specified. Use the -u flag."
    exit 1
fi

# Install required Python packages
echo -e "${COLOR_GREEN}[1/4]${COLOR_RESET} Installing required packages"
pip3 install pipenv==2022.3.28

# Clone the specified repository
SRC_DIR="/usr/src"
REPO_DIR="pytonv3"
echo -e "${COLOR_GREEN}[2/4]${COLOR_RESET} Cloning GitHub repository"
cd ${SRC_DIR}
[ -d "${REPO_DIR}" ] && rm -rf ${REPO_DIR}
git clone https://github.com/igroman787/${REPO_DIR}

# Install the module
echo "Installing ${REPO_DIR} module"
cd ${SRC_DIR}/${REPO_DIR}
python3 setup.py install

# Compile any missing binaries
TON_DIR="/usr/bin/ton"
echo "Compiling missing binaries"
cd ${TON_DIR}
make tonlibjson

# Add service to startup
echo -e "${COLOR_GREEN}[3/4]${COLOR_RESET} Adding to startup"
cmd="from sys import path; path.append('/usr/src/mytonctrl/'); from mypylib.mypylib import *; Add2Systemd(name='${REPO_DIR}', user='${user}', workdir='${SRC_DIR}/${REPO_DIR}', start='/usr/bin/python3 -m pyTON --liteserverconfig ${TON_DIR}/local.config.json --libtonlibjson ${TON_DIR}/tonlib/libtonlibjson.so')"
python3 -c "${cmd}"
systemctl restart ${REPO_DIR}

# Completion message
echo -e "${COLOR_GREEN}[4/4]${COLOR_RESET} ${REPO_DIR} installation complete"
exit 0
