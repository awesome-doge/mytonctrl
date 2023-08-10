#!/bin/bash

ensure_root() {
    if [ "$(id -u)" != "0" ]; then
        echo "Please run script as root"
        exit 1
    fi
}

stop_services() {
    systemctl stop validator
    systemctl stop mytoncore
    systemctl stop dht-server
}

retrieve_user_from_service() {
    local service_content=$(systemctl cat mytoncore)
    echo "$(echo "${service_content}" | grep User | cut -d '=' -f2)"
}

remove_services() {
    local services=(validator mytoncore dht-server)
    for service in "${services[@]}"; do
        rm -rf "/etc/systemd/system/${service}.service"
    done
    systemctl daemon-reload
}

remove_files() {
    local dirs=(/usr/src/ton /usr/src/mytonctrl /usr/bin/ton /var/ton-work /var/ton-dht-server /tmp/myton* /usr/local/bin/mytoninstaller/ /usr/local/bin/mytoncore/mytoncore.db "/home/${user}/.local/share/mytonctrl" "/home/${user}/.local/share/mytoncore/mytoncore.db")
    for dir in "${dirs[@]}"; do
        rm -rf "${dir}"
    done
}

remove_links() {
    local links=(/usr/bin/fift /usr/bin/liteclient /usr/bin/validator-console /usr/bin/mytonctrl)
    for link in "${links[@]}"; do
        rm -rf "${link}"
    done
}

main() {
    ensure_root

    # Color codes
    local COLOR='\033[34m'
    local ENDC='\033[0m'

    stop_services

    user=$(retrieve_user_from_service)
    remove_services
    remove_files
    remove_links

    echo -e "${COLOR}Uninstall Complete${ENDC}"
}

main
