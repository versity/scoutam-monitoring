#!/usr/bin/env python3

import subprocess
import re
import argparse
import os
import socket

def is_leader():
    # Execute "scoutam-monitor -print" command and capture its output
    process = subprocess.Popen(["scoutam-monitor", "-print"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()

    # Decode the output and parse it to check if IsLeader is true
    output = output.decode("utf-8")
    is_leader = re.search(r'IsLeader: \(bool\) (true|false)', output)
    if is_leader:
        return is_leader.group(1) == 'true'
    return False

def scheduler_metrics(metrics):
    # Run the command and capture its output
    process = subprocess.Popen(["samcli", "scheduler"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()

    # Decode the output before searching
    output = output.decode('utf-8')

    # Define the queues
    queues = ["SCHEDULER", "ARCHIVING", "STAGING"]

    # Initialize a dictionary to store idle status for each queue
    idle_status = {queue: False for queue in queues}

    # Parse the output and update idle status for each queue
    for line in output.split('\n'):
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "IDLED":
            queue = parts[0]
            idle_status[queue] = True

    # Generate metrics based on idle status
    for queue, idle in idle_status.items():
        if idle:
            metrics.append('scoutam_scheduler{{queue="{queue}", idle="{idle}"}} 0'.format(queue=queue, idle=idle))
        else:
            metrics.append('scoutam_scheduler{{queue="{queue}", idle="{idle}"}} 1'.format(queue=queue, idle=idle))

def parse_cache_stats(metrics):
    # Define regular expressions to match the counts
    noarchive_pattern = r"NoArchive\s+count:\s+(\d+)\s+data:\d+"
    unmatched_pattern = r"Archset Unmatched\s+count:\s+(\d+)\s+data:\d+"
    damaged_pattern = r"Files with damaged copy:\s+(\d+)"

    # Run the command and capture its output
    process = subprocess.Popen(["samcli", "fs", "acct", "--cache"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()

    # Decode the output before searching
    output = output.decode('utf-8')

    # Search for the counts in the output
    noarchive_match = re.search(noarchive_pattern, output)
    unmatched_match = re.search(unmatched_pattern, output)
    damaged_match = re.search(damaged_pattern, output)

    # Extract the counts if matches are found
    noarchive_count = int(noarchive_match.group(1)) if noarchive_match else 0
    unmatched_count = int(unmatched_match.group(1)) if unmatched_match else 0
    damaged_count = int(damaged_match.group(1)) if damaged_match else 0

    metrics.append('scoutam_acct{{name="noarchive", type="cache", metric="files"}} {}'.format(noarchive_count))
    metrics.append('scoutam_acct{{name="unmatched", type="cache", metric="files"}} {}'.format(unmatched_count))
    metrics.append('scoutam_acct{{name="damaged", type="cache", metric="files"}} {}'.format(damaged_count))

def acct_metrics(metrics):
    # Read the contents of /etc/projects and create a dictionary to map project names to IDs
    project_map = {}
    with open('/etc/projects', 'r') as f:
        for line in f:
            name, proj_id = line.strip().split(':')
            project_map[proj_id] = name

    # Execute "samcli quota use" command and capture its output
    process = subprocess.Popen(["samcli", "quota", "use"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()

    # Decode the output and parse it
    output = output.decode("utf-8")
    for line in output.split('\n'):
        if line.startswith('PROJ'):
            parts = line.split()
            proj_id = parts[1]
            name = project_map.get(proj_id, '')
            onln_files = parts[3]
            onln_size = parts[5]
            tot_files = parts[7]
            tot_size = parts[9]

            # Creating metrics
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", type="project", category="online", metric="files"}} {}'.format(onln_files, proj_id=proj_id, name=name))
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", type="project", category="online", metric="size"}} {}'.format(onln_size, proj_id=proj_id, name=name))
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", type="project", category="total", metric="files"}} {}'.format(tot_files, proj_id=proj_id, name=name))
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", type="project", category="total", metric="size"}} {}'.format(tot_size, proj_id=proj_id, name=name))

def main(metrics_file):
    leader = False
    metrics = []

    fqdn = socket.getfqdn()
    hostname = socket.gethostname().split('.')[0]

    # Only run on the leader node
    if is_leader():
        leader = True

    if leader:
        if os.path.exists("/etc/projects"):
            acct_metrics(metrics)
        parse_cache_stats(metrics)
        scheduler_metrics(metrics)

    metrics.append('scoutam_leader{{fqdn="{fqdn}", hostname="{hostname}", leader="{leader}"}} 1'.format(fqdn=fqdn, hostname=hostname, leader=leader))

    # Write metrics to the specified file
    with open(metrics_file, 'w') as f:
        f.write('\n'.join(metrics))
        f.write('\n')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate metrics file")
    parser.add_argument("--file", help="Path to the metrics file", required=True)
    args = parser.parse_args()

    if args.file:
        main(args.file)
