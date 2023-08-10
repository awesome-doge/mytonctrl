import subprocess
from pathlib import Path

# Constants
SERVICE_FILE_PATH = Path("/etc/systemd/system/validator.service")
SEARCH_TEXT = "validator-engine/ton-global.config.json"
REPLACE_TEXT = "global.config.json"


def read_file(file_path: Path) -> str:
    """
    Read the content of a file.
    
    :param file_path: The path of the file to be read.
    :return: Content of the file.
    """
    with file_path.open('r') as file:
        return file.read()


def write_file(file_path: Path, content: str) -> None:
    """
    Write the content to a file.
    
    :param file_path: The path of the file to be written.
    :param content: The content to be written to the file.
    """
    with file_path.open('w') as file:
        file.write(content)


def modify_service_file_content(content: str) -> str:
    """
    Modify the content of the service file by replacing specific text.
    
    :param content: The original content of the service file.
    :return: Modified content.
    """
    lines = content.split('\n')
    modified_lines = [
        line.replace(SEARCH_TEXT, REPLACE_TEXT) if "ExecStart" in line and SEARCH_TEXT in line else line
        for line in lines
    ]
    return "\n".join(modified_lines)


def update_service_file():
    """
    Update the content of the service file with modifications.
    """
    content = read_file(SERVICE_FILE_PATH)
    modified_content = modify_service_file_content(content)
    write_file(SERVICE_FILE_PATH, modified_content)


def reload_systemctl_daemon():
    """
    Reload the systemctl daemon.
    """
    subprocess.run(["systemctl", "daemon-reload"])


def main():
    """
    Main function to orchestrate the updating of the service file and reload the systemctl daemon.
    """
    update_service_file()
    reload_systemctl_daemon()


if __name__ == "__main__":
    main()
