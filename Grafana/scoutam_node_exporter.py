#!/usr/bin/env python3
#
# Copyright 2024 Versity Software
#

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

def get_filesystems():
    # Run samcli system and get list of filesystems
    process = subprocess.Popen(["samcli", "system"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()
    output = output.decode('utf-8')

    filesystems = []
    # Parse FSID lines to get mount points
    for line in output.split('\n'):
        if line.startswith('FSID:'):
            # Format: FSID: /mnt/scoutfs/fs03 (979b51)
            match = re.match(r'FSID:\s+(\S+)\s+\([a-f0-9]+\)', line)
            if match:
                filesystems.append(match.group(1))

    return filesystems

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

def parse_cache_stats(metrics, mount):
    # Define regular expressions to match the counts and data sizes
    noarchive_pattern = r"NoArchive\s+count:\s+(\d+)\s+data:(\d+)"
    unmatched_pattern = r"Archset Unmatched\s+count:\s+(\d+)\s+data:(\d+)"
    releasable_pattern = r"Releasable\s+count:\s+(\d+)\s+data:(\d+)"
    damaged_pattern = r"Files with damaged copy:\s+(\d+)"

    # Run the command and capture its output
    process = subprocess.Popen(["samcli", "fs", "acct", "--cache", "--mount", mount], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()

    # Decode the output before searching
    output = output.decode('utf-8')

    # Search for the counts and data sizes in the output
    noarchive_match = re.search(noarchive_pattern, output)
    unmatched_match = re.search(unmatched_pattern, output)
    releasable_match = re.search(releasable_pattern, output)
    damaged_match = re.search(damaged_pattern, output)

    # Extract the counts and sizes if matches are found
    noarchive_count = int(noarchive_match.group(1)) if noarchive_match else 0
    noarchive_size = int(noarchive_match.group(2)) if noarchive_match else 0
    unmatched_count = int(unmatched_match.group(1)) if unmatched_match else 0
    unmatched_size = int(unmatched_match.group(2)) if unmatched_match else 0
    releasable_count = int(releasable_match.group(1)) if releasable_match else 0
    releasable_size = int(releasable_match.group(2)) if releasable_match else 0
    damaged_count = int(damaged_match.group(1)) if damaged_match else 0

    # Add file count metrics
    metrics.append('scoutam_acct{{name="noarchive", fs="{fs}", type="cache", metric="files"}} {}'.format(noarchive_count, fs=mount))
    metrics.append('scoutam_acct{{name="unmatched", fs="{fs}", type="cache", metric="files"}} {}'.format(unmatched_count, fs=mount))
    metrics.append('scoutam_acct{{name="releasable", fs="{fs}", type="cache", metric="files"}} {}'.format(releasable_count, fs=mount))
    metrics.append('scoutam_acct{{name="damaged", fs="{fs}", type="cache", metric="files"}} {}'.format(damaged_count, fs=mount))

    # Add data size metrics
    metrics.append('scoutam_acct{{name="noarchive", fs="{fs}", type="cache", metric="size"}} {}'.format(noarchive_size, fs=mount))
    metrics.append('scoutam_acct{{name="unmatched", fs="{fs}", type="cache", metric="size"}} {}'.format(unmatched_size, fs=mount))
    metrics.append('scoutam_acct{{name="releasable", fs="{fs}", type="cache", metric="size"}} {}'.format(releasable_size, fs=mount))

def acct_metrics(metrics, projects):
    # Read the contents of /etc/projects and create a dictionary to map project names to IDs
    project_map = {}
    with open(projects, 'r') as f:
        for line in f:
            name, proj_id = line.strip().split(':')
            project_map[proj_id] = name

    # Execute "samcli quota use" command and capture its output
    process = subprocess.Popen(["samcli", "quota", "use"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate()

    # Decode the output and parse it
    output = output.decode("utf-8")
    for line in output.split('\n'):
        parts = line.split()
        if len(parts) > 0 and parts[1] == 'PROJ':
            fs = parts[0]
            proj_id = parts[2]
            name = project_map.get(proj_id, '')
            onln_files = parts[4]
            onln_size = parts[6]
            tot_files = parts[8]
            tot_size = parts[10]

            # Creating metrics
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", fs="{fs}", type="project", category="online", metric="files"}} {}'.format(onln_files, proj_id=proj_id, name=name, fs=fs))
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", fs="{fs}", type="project", category="online", metric="size"}} {}'.format(onln_size, proj_id=proj_id, name=name, fs=fs))
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", fs="{fs}", type="project", category="total", metric="files"}} {}'.format(tot_files, proj_id=proj_id, name=name, fs=fs))
            metrics.append('scoutam_acct{{id="{proj_id}", name="{name}", fs="{fs}", type="project", category="total", metric="size"}} {}'.format(tot_size, proj_id=proj_id, name=name, fs=fs))

def main(args):
    leader = False
    metrics = []

    fqdn = socket.gethostname()
    hostname = fqdn.split('.')[0]

    # Only run on the leader node
    if is_leader():
        leader = True

    if leader:
        if os.path.exists(args.projects):
            acct_metrics(metrics, args.projects)

        # Get list of filesystems and collect cache stats for each
        filesystems = get_filesystems()
        for fs in filesystems:
            parse_cache_stats(metrics, fs)

        scheduler_metrics(metrics)

    metrics.append('scoutam_leader{{fqdn="{fqdn}", hostname="{hostname}", leader="{leader}"}} 1'.format(fqdn=fqdn, hostname=hostname, leader=leader))

    # Write metrics to file or STDOUT
    output = '\n'.join(metrics) + '\n'
    if args.file:
        with open(args.file, 'w') as f:
            f.write(output)
    else:
        print(output, end='')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate metrics file")
    parser.add_argument("--file", type=str, help="Path to the metrics file (if not specified, prints to STDOUT)")
    parser.add_argument("--projects", type=str, default="/etc/scoutam/projects", help="Path to project ID to name mapping (default /etc/scoutam/projects")
    args = parser.parse_args()

    main(args)
