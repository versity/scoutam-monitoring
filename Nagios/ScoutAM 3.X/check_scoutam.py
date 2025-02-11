#!/usr/bin/env python3
#
# Copyright 2025 Versity Software, Inc.
#
# NRPE Check Script for ScoutAM 3.X
#

import argparse
import os
import re
import shutil
import subprocess
import sys

# ScoutAM executables
SCOUTFS_CMD = "/usr/sbin/scoutfs"
SCOUTAM_MONITOR_CMD = "/usr/sbin/scoutam-monitor"
SAMCLI_CMD = "/usr/bin/samcli"

# SystemD services
SCOUTAM_SERVICE="scoutam"
SCOUTFS_FENCED_SERVICE="scoutfs-fenced"
VERSITYGW_SERVICE="versitygw@"
SCOUTGW_SERVICE="scoutgw@"

# Configuration locations
VERSITYGW_CONF_DIR="/etc/versitygw.d"
SCOUTGW_CONF_DIR="/etc/scoutgw.d"
MULTIFS_CONF="/etc/scoutam/multifs.yaml"

# NRPE exit status
NRPE_EXIT_OK = 0
NRPE_EXIT_WARN = 1
NRPE_EXIT_CRIT = 2

def convert_bytes(size_str):
    unit_multipliers = {
        "B": 1, "KB": 1024, "K": 1024, "MB": 1024 ** 2, "M": 1024 ** 2,
        "GB": 1024 ** 3, "G": 1024 ** 3, "TB": 1024 ** 4, "T": 1024 ** 4,
        "PB": 1024 ** 5, "P": 1024 ** 5,
    }

    # Match numeric part and unit part
    match = re.match(r"^\s*([\d.]+)\s*([a-zA-Z]+)\s*$", size_str)
    if not match:
        raise ValueError(f"Invalid size format: '{size_str}'")

    size_value, unit = match.groups()
    unit = unit.upper()  # Normalize unit to uppercase
    if unit in unit_multipliers:
        try:
            return int(float(size_value) * unit_multipliers[unit])
        except ValueError:
            raise ValueError(f"Invalid numeric value in '{size_str}'")

    raise ValueError(f"Unknown size unit in '{size_str}'")

def b2h(b):
    if b < 0:
        raise ValueError("Byte value cannot be negative.")

    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    index = 0
    value = float(b)

    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1

    return f"{value:.2f} {units[index]}"

def cmd(command):
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=True,
            shell=isinstance(command, str)
        )

        stdout = result.stdout.splitlines()
        return None, stdout, result.returncode
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.splitlines() if e.stderr else []
        return stderr, [], e.returncode

def get_mounts():
    command = [SCOUTAM_MONITOR_CMD, "-print"]
    stderr, stdout, ret = cmd(command)
    if ret != 0:
        return stderr, []

    # Regular expression to capture each field
    mount_regex = re.compile(
        r'MountPoint: \(string\) \(len=\d+\) "(?P<MountPoint>[^"]+)",\s*'
        r'IsLeader: \(bool\) (?P<IsLeader>\w+),\s*'
        r'Device: \(string\) \(len=\d+\) "(?P<Device>[^"]+)",\s*'
        r'Fsid: \(fs\.FSID\) (?P<Fsid>[a-zA-Z0-9]+),\s*'
        r'QuorumSlot: \(int64\) (?P<QuorumSlot>\d+)'
    )

    output = "\n".join(stdout)

    mounts = []
    for match in mount_regex.finditer(output):
        mounts.append({
            "mount": match.group("MountPoint"),
            "leader": match.group("IsLeader") == "true",
            "device": match.group("Device"),
            "fsid": match.group("Fsid"),
            "slot": int(match.group("QuorumSlot")),
        })

    return None, mounts

def get_usage(mount):
    usage = {"MetaData": {}, "Data": {}}
    usage_regex = re.compile(
        r"^\s*(MetaData|Data)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
    )

    command = [SCOUTFS_CMD, "df", "--path", mount]
    error, stdout, ret = cmd(command)
    if ret != 0:
        return error, None

    # Get filesystem usage
    for line in stdout:
        line = line.strip()
        match = usage_regex.match(line)
        if match:
            usage_type = match.group(1)
            usage[usage_type] = {
                "block_size": convert_bytes(match.group(2)),
                "blocks_total": int(match.group(3)),
                "blocks_used": int(match.group(4)),
                "blocks_free": int(match.group(5)),
                "pct_used": int(match.group(6)),
            }
            usage[usage_type]["bytes_total"] = usage[usage_type]["blocks_total"] * usage[usage_type]["block_size"]
            usage[usage_type]["bytes_used"] = usage[usage_type]["blocks_used"] * usage[usage_type]["block_size"]
            usage[usage_type]["bytes_free"] = usage[usage_type]["blocks_free"] * usage[usage_type]["block_size"]

    # Get high and low watermarks
    command = [SAMCLI_CMD, "fs", "stat", "-m", mount]
    error, stdout, ret = cmd(command)
    if ret != 0:
        return error, None

    hwm = None
    lwm = None

    for line in stdout:
        line = line.strip()

        if line.startswith("High Watermark:"):
            match = re.search(r"(\d+)%", line)
            if match:
                hwm = int(match.group(1))

        if line.startswith("Low Watermark:"):
            match = re.search(r"(\d+)%", line)
            if match:
                lwm = int(match.group(1))

    usage["hwm_pct"] = hwm
    usage["lwm_pct"] = lwm
    usage["hwm_exceeded"] = False
    usage["hwm_bytes"] = usage["Data"]["bytes_total"] * (hwm / 100)

    if usage["Data"]["bytes_used"] > usage["hwm_bytes"]:
        usage["hwm_exceeded"] = True

    return None, usage

def get_service_status(service):
    state = "inactive"

    command = ["systemctl", "--state=ACTIVE", "--type=service", "status", service]
    error, stdout, ret = cmd(command)
    if ret == 0:
        state = "active"

    return state

# Check the state of the scheduler
def check_scheduler(args):
    nrpe_status = NRPE_EXIT_OK
    nrpe_state = "OK"
    nrpe_msgs = []

    command = [SAMCLI_CMD, "scheduler"]
    error, stdout, ret = cmd(command)
    if ret != 0:
        nrpe_msgs.append((
            f"CRITICAL: ScoutAM scheduler check failed: {error}"
        ))
        return NRPE_EXIT_CRIT, nrpe_msgs

    scheduler = {}

    scheduler["scheduler"] = "running"
    scheduler["archiving"] = "running"
    scheduler["staging"] = "running"

    for line in stdout:
        line = line.strip()

        if line == "SCHEDULER IS IDLED":
            scheduler["scheduler"] = "idle"
            nrpe_status = NRPE_EXIT_WARN
            nrpe_state = "WARN"

        if line == "ARCHIVING IS IDLED":
            scheduler["archiving"] = "idle"
            nrpe_status = NRPE_EXIT_WARN
            nrpe_state = "WARN"

        if line == "STAGING IS IDLED":
            scheduler["staging"] = "idle"
            nrpe_status = NRPE_EXIT_WARN
            nrpe_state = "WARN"

    nrpe_msgs.append((
        f"{nrpe_state}: ScoutAM ",
        f"scheduler: {scheduler['scheduler']}, ",
        f"archiver: {scheduler['archiving']}, ",
        f"staging: {scheduler['staging']}"
    ))

    return nrpe_status, nrpe_msgs

# Check all things ScoutFS
def check_mounts(args):
    nrpe_status = NRPE_EXIT_OK
    nrpe_msgs = []

    status = get_service_status("scoutfs-fenced")
    if status != "active":
        nrpe_msgs.append(f"CRITICAL: ScoutFS fencing service is not active")
        nrpe_status = NRPE_EXIT_CRIT

    # Get all mounted filesystems
    error, mounts = get_mounts()
    if error is not None:
        nrpe_msgs.append(f"CRITICAL: ScoutFS check failed: {error}")
        return NRPE_EXIT_CRIT, nrpe_msgs

    if not mounts:
        nrpe_msgs.append(f"CRITICAL: No ScoutFS filesystems mounted")
        return NRPE_EXIT_CRIT, nrpe_msgs

    if args.mount:
        found = next((m for m in mounts if m.get("mount") == args.mount), None)
        if found is None:
            nrpe_msgs.append(
                f"CRITICAL: ScoutFS filesystem {args.mount} not found or mounted"
            )
            return NRPE_EXIT_CRIT, nrpe_msgs

    for mount in mounts:
        if args.mount and mount['mount'] != args.mount:
            continue

        usage = {}

        error, usage = get_usage(mount['mount'])
        if error is not None:
            nrpe_msgs.append(f"CRITICAL: ScoutFS failed to get usage: {error}")
            return NRPE_EXIT_CRIT, nrpe_msgs

        hwm_bytes = b2h(usage['hwm_bytes'])

        data_used = b2h(usage['Data']['bytes_used'])
        data_free = b2h(usage['Data']['bytes_free'])

        meta_used = b2h(usage['MetaData']['bytes_used'])
        meta_free = b2h(usage['MetaData']['bytes_free'])

        data_crit_bytes = usage['Data']['bytes_total'] * (args.crit_thresh / 100)
        data_warn_bytes = usage['Data']['bytes_total'] * (args.warn_thresh / 100)

        meta_crit_bytes = usage['MetaData']['bytes_total'] * (args.crit_thresh / 100)
        meta_warn_bytes = usage['MetaData']['bytes_total'] * (args.warn_thresh / 100)

        if usage['Data']['bytes_used'] > data_crit_bytes:
            nrpe_msgs.append((
                f"CRITICAL: ScoutFS filesystem {mount['mount']} data usage ",
                f"above critical threshold of {b2h(data_crit_bytes)}, used {data_used}, free {data_free}"
            ))
            nrpe_status = max(nrpe_status, NRPE_EXIT_CRIT)
        elif usage['Data']['bytes_used'] > data_warn_bytes:
            nrpe_msgs.append((
                f"WARN: ScoutFS filesystem {mount['mount']} data usage ",
                f"above warning threshold of {b2h(data_warn_bytes)}, used {data_used}, free {data_free}"
            ))
            nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)
        else:
            nrpe_msgs.append((
                f"OK: ScoutFS filesystem {mount['mount']} data used {data_used}, ",
                f"free: {data_free}, high watermark: {hwm_bytes}"
            ))

        if usage['MetaData']['bytes_used'] > meta_crit_bytes:
            nrpe_msgs.append((
                f"CRITICAL: ScoutFS filesystem {mount['mount']} metadata usage ",
                f"above critical threshold of {b2h(meta_crit_bytes)}, used {meta_used}, free {meta_free}"
            ))
            nrpe_status = max(nrpe_status, NRPE_EXIT_CRIT)
        elif usage['MetaData']['bytes_used'] > meta_warn_bytes:
            nrpe_msgs.append((
                f"WARN: ScoutFS filesystem {mount['mount']} metadata usage ",
                f"above warning threshold of {b2h(meta_warn_bytes)}, used {meta_used}, free {meta_free}"
            ))
            nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)
        else:
            nrpe_msgs.append((
                f"OK: ScoutFS filesystem {mount['mount']} metadata used {meta_used}, ",
                f"free: {meta_free}, high watermark: {hwm_bytes}"
        ))

        if usage['hwm_exceeded']:
            nrpe_msgs.append((
                f"CRITICAL: ScoutFS filesystem {mount['mount']} ",
                f"exceeded high watermark (used: {data_used}, ",
                f"high watermark: {hwm_bytes}, free: {data_free})"
            ))
            nrpe_status = max(nrpe_status, NRPE_EXIT_CRIT)

    return nrpe_status, nrpe_msgs

def check_gateway(args, gateway="versitygw"):
    nrpe_status = NRPE_EXIT_OK
    nrpe_msgs = []
    name = "VersityGW"
    conf_dir = VERSITYGW_CONF_DIR
    service_prefix = VERSITYGW_SERVICE
    configs = []

    if gateway == "scoutgw":
        name = "ScoutGW"
        conf_dir = SCOUTGW_CONF_DIR
        service_prefix = SCOUTGW_SERVICE
        if not shutil.which("scoutgw"):
            nrpe_msgs.append((
                f"OK: {name} is not installed, skipping check"))
            return nrpe_status, nrpe_msgs
    else:
        if not shutil.which("versitygw"):
            nrpe_msgs.append((
                f"OK: {name} is not installed, skipping check"))
            return nrpe_status, nrpe_msgs

    if not os.path.isdir(conf_dir):
        return nrep_status, []

    try:
        configs = [f for f in os.listdir(conf_dir)
            if f.endswith('.conf')]
    except Exception as e:
        nrpe_msgs.append((
            f"CRITICAL: {name} cannot access configuration directory ",
            f"{conf_dir}: {e}"
        ))

    if not configs:
        nrpe_msgs.append(
            f"WARN: No {name} configurations found in {conf_dir}"
        )

    for conf in configs:
        # Skip example configuration file
        if conf == "example.conf":
            continue

        base = os.path.splitext(conf)[0]
        service = f"{service_prefix}{base}"

        status = get_service_status(service)
        if status != "active":
            nrpe_msgs.append(
                f"CRITICAL: {name} instance {base} is not running"
            )
            nrpe_status = NRPE_EXIT_CRIT
        else:
            nrpe_msgs.append(
                f"OK: {name} instance {base} is running"
            )

    return nrpe_status, nrpe_msgs

# Check ScoutAM service
def check_scoutam(args):
    nrpe_status = NRPE_EXIT_OK
    nrpe_msgs = []

    status = get_service_status("scoutam")
    if status != "active":
        nrpe_msgs.append("CRITICAL: ScoutAM service is not running")
        if nrpe_status < NRPE_EXIT_CRIT:
            nrpe_status = NRPE_EXIT_CRIT
    else:
        nrpe_msgs.append("OK: ScoutAM service is running")

    return nrpe_status, nrpe_msgs

def parse_args():
    parser = argparse.ArgumentParser(
        usage=(
            "\n"
            "check_scoutam.py [--help|-h] [--mount|-m MOUNT] [--passfail|-p] operation\n"
            "\n"
            "Optional arguments:\n"
            "\n"
            "    --help|-h         Print help message and exit\n"
            "    --mount|-m MOUNT  Mount point to check\n"
            "    --passfail|-p     Exit with either a 0 (success), 0 for warning, or 2 for critical\n"
            "\n"
            "The following check operations are available:\n"
            "\n"
            "    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted\n"
            "    service     - check if the ScoutAM service is running\n"
            "    scheduler   - check if the scheduler is running on the leader node\n"
            "    gateway     - check if all the configured ScoutAM S3 gateway services are running\n"
            "    versitygw   - check if all the configured Versity S3 gateway services are running\n"
            "    scoutam     - check mount, scoutam, and scheduler\n"
            "    all         - check all including S3 gateways\n"
            "\n"
        ),
        add_help=False
    )

    parser.add_argument("--passfail", "-p", action="store_true")
    parser.add_argument("--mount", "-m", type=str)
    parser.add_argument("operation", choices=["mount", "service", "scheduler", "gateway", "versitygw", "scoutam", "all"])
    parser.add_argument("crit_thresh", type=int, nargs="?", default=90)
    parser.add_argument("warn_thresh", type=int, nargs="?", default=70)

    return parser.parse_args()

def main():
    args = parse_args()
    nrpe_msgs = []
    nrpe_checks = {"ok": 0, "warn": 0, "crit": 0}

    status_map = {
        NRPE_EXIT_CRIT: "crit",
        NRPE_EXIT_WARN: "warn",
        NRPE_EXIT_OK: "ok",
    }

    if not os.path.isfile(SCOUTFS_CMD) and not os.access(SCOUTFS_CMD, os.X_OK):
        print("CRITICAL: ScoutFS is not installed or missing binaries")
        sys.exit(NRPE_EXIT_CRIT)

    if not os.path.isfile(SCOUTAM_MONITOR_CMD) and not os.access(SCOUTAM_MONITOR_CMD, os.X_OK):
        print("CRITICAL: ScoutAM is not installed or missing binaries")
        sys.exit(NRPE_EXIT_CRIT)
        
    if args.operation in {"mount", "scoutam", "all"}:
        nrpe_status, msgs = check_mounts(args)
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"service", "scoutam", "all"}:
        nrpe_status, msgs = check_scoutam(args)
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"scheduler", "scoutam", "all"}:
        nrpe_status, msgs = check_scheduler(args)
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"gateway", "all"}:
        nrpe_status, msgs = check_gateway(args, "scoutgw")
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"versitygw", "all"}:
        nrpe_status, msgs = check_gateway(args)
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if not args.passfail:
        for line in nrpe_msgs:
            if isinstance(line, tuple):
                print("".join(line))
            else:
                print(line)

    if nrpe_checks['crit'] > 0:
        sys.exit(NRPE_EXIT_CRIT)
    elif nrpe_checks['warn'] > 0:
        sys.exit(NRPE_EXIT_WARN)

    sys.exit(NRPE_EXIT_OK)

if __name__ == "__main__":
    main()
