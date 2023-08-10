#!/bin/bash
set -e

# Check if script is run with sudo
ensure_root() {
    if [ "$(id -u)" != "0" ]; then
        echo "Please run script as root"
        exit 1
    fi
}

show_help_and_exit() {
    echo 'Supported arguments:'
    echo ' -m [lite|full]   Choose installation mode'
    echo ' -c  PATH         Provide custom config for toninstaller.sh'
    echo ' -t               Disable telemetry'
    echo ' -i               Ignore minimum requirements'
    echo ' -d               Use pre-packaged dump for quicker sync'
    echo ' -h               Show this help'
    exit
}

check_mode() {
    if [ "${mode}" != "lite" ] && [ "${mode}" != "full" ]; then
        echo "Run script with flag '-m lite' or '-m full'"
        exit 1
    fi
}

check_resources() {
    cpus=$(lscpu | grep "CPU(s)" | head -n 1 | awk '{print $2}')
    memory=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    
    if [ "${mode}" = "lite" ] && [ "$ignore" = false ] && \
       ([ "${cpus}" -lt 2 ] || [ "${memory}" -lt 2000000 ]); then
        echo "Insufficient resources. Requires a minimum of 2 CPUs and 2GB RAM."
        exit 1
    elif [ "${mode}" = "full" ] && [ "$ignore" = false ] && \
         ([ "${cpus}" -lt 8 ] || [ "${memory}" -lt 8000000 ]); then
        echo "Insufficient resources. Requires a minimum of 8 CPUs and 8GB RAM."
        exit 1
    fi
}

check_and_install_ton() {
    file1=${BIN_DIR}/ton/crypto/fift
    file2=${BIN_DIR}/ton/lite-client/lite-client
    file3=${BIN_DIR}/ton/validator-engine-console/validator-engine-console
    if [ -f "${file1}" ] && [ -f "${file2}" ] && [ -f "${file3}" ]; then
        echo "TON exists"
        cd $SOURCES_DIR
        rm -rf mytonctrl
        git clone --recursive https://github.com/ton-blockchain/mytonctrl.git
    else
        rm -f toninstaller.sh
        wget https://raw.githubusercontent.com/ton-blockchain/mytonctrl/master/scripts/toninstaller.sh
        bash toninstaller.sh -c "${config}"
        rm -f toninstaller.sh
    fi
}

# Initialize
ensure_root

if [[ "${1-}" =~ ^-*h(elp)?$ ]]; then
    show_help_and_exit
fi

# Defaults
config="https://ton-blockchain.github.io/global.config.json"
telemetry=true
ignore=false
dump=false

# Parse arguments
while getopts m:c:tidh flag; do
    case "${flag}" in
        m) mode=${OPTARG} ;;
        c) config=${OPTARG} ;;
        t) telemetry=false ;;
        i) ignore=true ;;
        d) dump=true ;;
        h) show_help_and_exit ;;
        *)
            echo "Flag -${flag} is not recognized. Aborting"
            exit 1 ;;
    esac
done

check_mode
check_resources

# Color codes
COLOR='\033[92m'
ENDC='\033[0m'

# Begin installation
echo -e "${COLOR}[1/4]${ENDC} Starting installation of MyTonCtrl"
SOURCES_DIR=/usr/src
BIN_DIR=/usr/bin
if [[ "$OSTYPE" =~ darwin.* ]]; then
    SOURCES_DIR=/usr/local/src
    BIN_DIR=/usr/local/bin
    mkdir -p ${SOURCES_DIR}
fi

# Check for TON components
echo -e "${COLOR}[2/4]${ENDC} Checking for required TON components"
check_and_install_ton

# Launch installer
echo -e "${COLOR}[3/4]${ENDC} Launching the mytoninstaller.py"
user=$( [ "$(ps -p $PPID -o comm=)" = "sudo" ] && logname || whoami )
python3 ${SOURCES_DIR}/mytonctrl/mytoninstaller.py -m ${mode} -u ${user} -t ${telemetry} --dump ${dump}

# Exit
echo -e "${COLOR}[4/4]${ENDC} Mytonctrl installation completed"
exit 0
