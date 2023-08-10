import sys
import pwd
import time
import subprocess
from pathlib import Path

SERVICE_FILE_PATH = Path("/etc/systemd/system/validator.service")

def guard():
    """
    Periodically check the timestamp and execute the command after the timestamp is surpassed.
    """
    timestamp = int(sys.argv[1])
    args = sys.argv[2:]
    while True:
        time.sleep(1)
        current_time = int(time.time())
        if current_time > timestamp:
            execute_command(args)
            print("exit")
            sys.exit(0)

def execute_command(input_args):
    """
    Execute the command with the specified arguments.
    
    :param input_args: List of input arguments to be appended to the ExecStart command.
    """
    print("inputArgs:", input_args)
    
    # Stop validator
    subprocess.run(["systemctl", "stop", "validator"])

    with SERVICE_FILE_PATH.open('r') as file:
        text = file.read()
        for line in text.split('\n'):
            if "ExecStart" in line:
                exec_start = line.replace("ExecStart = ", '').split(' ')
                print("ExecStart args:", exec_start)
                args = exec_start + input_args

    pw_record = pwd.getpwnam("validator")
    user_uid = pw_record.pw_uid
    user_gid = pw_record.pw_gid
    
    # Start with args
    print("args:", args)
    process = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, preexec_fn=demote(user_uid, user_gid)
    )
    process.wait()
    output_text = process.stdout.read().decode()
    print("Output:", output_text)
    
    # Exit program
    sys.exit(0)

def demote(user_uid, user_gid):
    """
    Return a function to demote the process to the specified user ID and group ID.
    
    :param user_uid: User ID to demote to.
    :param user_gid: Group ID to demote to.
    :return: A function that performs the demotion.
    """
    def result():
        os.setgid(user_gid)
        os.setuid(user_uid)
        os.system("ulimit -n 1")
        os.system("ulimit -u 1")
        os.system("ulimit -l 1")
    return result

if __name__ == "__main__":
    guard()
