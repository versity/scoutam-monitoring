#!/usr/bin/env python3
#
# Copyright 2025 Versity Software, Inc.
#
# NRPE Check Script for ScoutAM 3.X
#

import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

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

# State file for sequence restart monitoring
STATE_FILE="/var/lib/nagios/check_scoutam_sequences.json"

# NRPE exit status
NRPE_EXIT_OK = 0
NRPE_EXIT_WARN = 1
NRPE_EXIT_CRIT = 2

# Debug/verbose mode flags
DEBUG = False
VERBOSE = False

def debug_print(message, level="DEBUG"):
    """Print debug/verbose messages if enabled."""
    global DEBUG, VERBOSE
    if level == "DEBUG" and DEBUG:
        print(f"[DEBUG] {message}", file=sys.stderr)
    elif level == "VERBOSE" and (VERBOSE or DEBUG):
        print(f"[VERBOSE] {message}", file=sys.stderr)

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

def cmd(command, timeout=30):
    debug_print(f"Executing command: {command}", "DEBUG")
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=True,
            shell=isinstance(command, str),
            timeout=timeout
        )

        stdout = result.stdout.splitlines()
        debug_print(f"Command completed successfully, return code: {result.returncode}", "DEBUG")
        if stdout and len(stdout) > 0:
            preview = stdout[0] if len(stdout[0]) <= 100 else stdout[0][:100] + "..."
            debug_print(f"Output preview (first line): {preview}", "DEBUG")
        return None, stdout, result.returncode
    except subprocess.TimeoutExpired as e:
        error_msg = f"Command timed out after {timeout} seconds"
        debug_print(f"Command timeout: {error_msg}", "DEBUG")
        return [error_msg], [], -1
    except subprocess.CalledProcessError as e:
        debug_print(f"Command failed with return code: {e.returncode}", "DEBUG")
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

    # Validate that watermarks were found
    if hwm is None or lwm is None:
        return ["High or Low watermark not found in samcli fs stat output"], None

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

def is_scheduler_node():
    """
    Check if the current node is the active scheduler node.

    Returns:
        tuple: (is_scheduler: bool, scheduler_name: str or None, error: str or None)
    """
    # Execute samcli system command
    command = [SAMCLI_CMD, "system"]
    error, stdout, ret = cmd(command)

    if ret != 0:
        error_msg = f"Failed to execute samcli system: {error}"
        return False, None, error_msg

    # Parse output to find "scheduler name"
    scheduler_name = None
    scheduler_regex = re.compile(r'^scheduler name\s*:\s*(.+)$', re.MULTILINE)

    output = "\n".join(stdout)
    match = scheduler_regex.search(output)

    if not match:
        return False, None, "Could not parse scheduler name from samcli system output"

    scheduler_name = match.group(1).strip()
    debug_print(f"Parsed scheduler name from samcli system: {scheduler_name}", "VERBOSE")

    # Get current hostname
    try:
        current_hostname = socket.gethostname()
        debug_print(f"Current hostname: {current_hostname}", "VERBOSE")
    except Exception as e:
        return False, scheduler_name, f"Could not get current hostname: {e}"

    # Compare hostnames (handle FQDN vs short name)
    # Extract short name (before first dot) for both
    scheduler_short = scheduler_name.split('.')[0]
    current_short = current_hostname.split('.')[0]
    debug_print(f"Comparing short names: scheduler='{scheduler_short}' current='{current_short}'", "VERBOSE")

    is_scheduler = (scheduler_short.lower() == current_short.lower())
    debug_print(f"Is this the scheduler node? {is_scheduler}", "VERBOSE")

    return is_scheduler, scheduler_name, None

def load_sequence_state():
    """Load persisted state from JSON file with file locking, return empty dict if missing or corrupt."""
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, 'r') as f:
            # Acquire shared lock for reading
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                state = json.load(f)
                return state
            finally:
                # Release lock
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, IOError) as e:
        # If file is corrupt or unreadable, log warning and return empty state
        print(f"WARN: State file corrupt or unreadable, resetting: {e}", file=sys.stderr)
        return {}

def save_sequence_state(state):
    """Save state dict to JSON file with atomic write and file locking."""
    # Ensure directory exists with secure permissions
    state_dir = os.path.dirname(STATE_FILE)
    if state_dir and not os.path.exists(state_dir):
        try:
            os.makedirs(state_dir, mode=0o750)
        except OSError as e:
            print(f"WARN: Could not create state directory {state_dir}: {e}", file=sys.stderr)
            return

    # Write to temporary file and rename for atomicity
    temp_file = STATE_FILE + ".tmp"
    try:
        with open(temp_file, 'w') as f:
            # Acquire exclusive lock for writing
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2)
            finally:
                # Release lock
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        # Set secure permissions before rename
        os.chmod(temp_file, 0o640)
        os.rename(temp_file, STATE_FILE)
    except (IOError, OSError) as e:
        print(f"WARN: Could not save state file {STATE_FILE}: {e}", file=sys.stderr)
        # Clean up temp file if it exists
        if os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except OSError:
                pass

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
        return nrpe_status, []

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

# Check sequence restart status for Arfind and Stfind
def check_sequences(args):
    debug_print("Starting sequence restart check", "VERBOSE")
    nrpe_status = NRPE_EXIT_OK
    nrpe_msgs = []
    current_time = time.time()

    # Check if this is the scheduler node
    is_scheduler, scheduler_name, error = is_scheduler_node()

    if error:
        # Could not determine scheduler status - return warning
        debug_print(f"Error determining scheduler node: {error}", "VERBOSE")
        nrpe_msgs.append(f"WARN: Could not determine scheduler node: {error}")
        return NRPE_EXIT_WARN, nrpe_msgs

    if not is_scheduler:
        # Not the scheduler node - skip check with OK status
        debug_print(f"Not scheduler node (scheduler is {scheduler_name}), skipping check", "VERBOSE")
        # Remove state file if it exists (it's outdated if node becomes scheduler later)
        if os.path.exists(STATE_FILE):
            try:
                os.unlink(STATE_FILE)
                debug_print("Removed stale state file", "VERBOSE")
                nrpe_msgs.append(f"OK: Not scheduler node, skipping sequence check (scheduler: {scheduler_name}), removed stale state file")
            except OSError as e:
                # Failed to remove state file - warn but don't fail the check
                nrpe_msgs.append(f"OK: Not scheduler node, skipping sequence check (scheduler: {scheduler_name}), warning: could not remove stale state file: {e}")
        else:
            nrpe_msgs.append(f"OK: Not scheduler node, skipping sequence check (scheduler: {scheduler_name})")
        return NRPE_EXIT_OK, nrpe_msgs

    # This is the scheduler node - proceed with sequence check
    # Execute samcli debug seq -c command
    command = [SAMCLI_CMD, "debug", "seq", "-c"]
    error, stdout, ret = cmd(command)
    if ret != 0:
        nrpe_msgs.append(f"CRITICAL: Sequence check failed: {error}")
        return NRPE_EXIT_CRIT, nrpe_msgs

    # Join output into single string for multi-line regex
    output = "\n".join(stdout)

    # Parse filesystem blocks - each starts with ### FSID
    # Match FSID, Mount, and capture everything until next ### or end
    fs_regex = re.compile(
        r'### FSID: (?P<fsid>[a-zA-Z0-9]+)\s+Mount: (?P<mount>[^\n]+)\n'
        r'(?P<content>.*?)'
        r'(?=### FSID:|$)',
        re.DOTALL
    )

    # Parse Arfind/Stfind status lines
    arfind_blocked_regex = re.compile(r'Arfind Restart Blocked:\s*(\d+):\s*(.+)')
    arfind_not_blocked_regex = re.compile(r'Arfind Restart Not Blocked')
    stfind_blocked_regex = re.compile(r'Stfind Restart Blocked:\s*(\d+):\s*(.+)')
    stfind_not_blocked_regex = re.compile(r'Stfind Restart Not Blocked')
    current_seq_regex = re.compile(r'Current FS Seq:\s*(\d+)')

    # Load previous state
    state = load_sequence_state()
    debug_print(f"Loaded state for {len(state)} filesystem(s)", "VERBOSE")

    # Track which mounts we've seen (to clean up stale entries)
    seen_mounts = set()

    # Process each filesystem
    fs_found = False
    for fs_match in fs_regex.finditer(output):
        fs_found = True
        fsid = fs_match.group("fsid")
        mount = fs_match.group("mount").strip()
        content = fs_match.group("content")
        debug_print(f"Processing filesystem {mount} (FSID: {fsid})", "VERBOSE")

        # Filter by mount if specified
        if args.mount and mount != args.mount:
            continue

        seen_mounts.add(mount)

        # Extract current FS sequence
        current_fs_seq = None
        seq_match = current_seq_regex.search(content)
        if seq_match:
            current_fs_seq = int(seq_match.group(1))

        # Initialize or update filesystem state
        if mount not in state:
            state[mount] = {
                "fsid": fsid,
                "last_check": current_time,
                "current_fs_seq": current_fs_seq,
                "arfind": {"status": "not_blocked"},
                "stfind": {"status": "not_blocked"}
            }
        else:
            # Update existing entry
            state[mount]["fsid"] = fsid
            state[mount]["last_check"] = current_time
            state[mount]["current_fs_seq"] = current_fs_seq

        # Check Arfind status
        arfind_blocked = arfind_blocked_regex.search(content)
        if arfind_blocked:
            inode = arfind_blocked.group(1)
            reason = arfind_blocked.group(2)

            # Check if this is newly blocked or ongoing
            if state[mount]["arfind"].get("status") != "blocked" or state[mount]["arfind"].get("inode") != inode:
                # Newly blocked or inode changed - record timestamp
                state[mount]["arfind"] = {
                    "status": "blocked",
                    "blocked_since": current_time,
                    "inode": inode,
                    "reason": reason
                }
                duration = 0
            else:
                # Already blocked - calculate duration
                duration = current_time - state[mount]["arfind"]["blocked_since"]
                # Update reason in case it changed
                state[mount]["arfind"]["reason"] = reason

            # Check thresholds
            if duration >= args.arfind_crit:
                nrpe_msgs.append((
                    f"CRITICAL: Arfind blocked for {int(duration)}s on {mount} ",
                    f"(inode {inode}: {reason})"
                ))
                nrpe_status = max(nrpe_status, NRPE_EXIT_CRIT)
            elif duration >= args.arfind_warn:
                nrpe_msgs.append((
                    f"WARN: Arfind blocked for {int(duration)}s on {mount} ",
                    f"(inode {inode}: {reason})"
                ))
                nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)
            else:
                nrpe_msgs.append(f"OK: Arfind blocked for {int(duration)}s on {mount} (under threshold)")
        elif arfind_not_blocked_regex.search(content):
            # Arfind not blocked
            state[mount]["arfind"] = {"status": "not_blocked"}
            nrpe_msgs.append(f"OK: Arfind not blocked on {mount}")

        # Check Stfind status
        stfind_blocked = stfind_blocked_regex.search(content)
        if stfind_blocked:
            inode = stfind_blocked.group(1)
            reason = stfind_blocked.group(2)

            # Check if this is newly blocked or ongoing
            if state[mount]["stfind"].get("status") != "blocked" or state[mount]["stfind"].get("inode") != inode:
                # Newly blocked or inode changed - record timestamp
                state[mount]["stfind"] = {
                    "status": "blocked",
                    "blocked_since": current_time,
                    "inode": inode,
                    "reason": reason
                }
                duration = 0
            else:
                # Already blocked - calculate duration
                duration = current_time - state[mount]["stfind"]["blocked_since"]
                # Update reason in case it changed
                state[mount]["stfind"]["reason"] = reason

            # Check thresholds
            if duration >= args.stfind_crit:
                nrpe_msgs.append((
                    f"CRITICAL: Stfind blocked for {int(duration)}s on {mount} ",
                    f"(inode {inode}: {reason})"
                ))
                nrpe_status = max(nrpe_status, NRPE_EXIT_CRIT)
            elif duration >= args.stfind_warn:
                nrpe_msgs.append((
                    f"WARN: Stfind blocked for {int(duration)}s on {mount} ",
                    f"(inode {inode}: {reason})"
                ))
                nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)
            else:
                nrpe_msgs.append(f"OK: Stfind blocked for {int(duration)}s on {mount} (under threshold)")
        elif stfind_not_blocked_regex.search(content):
            # Stfind not blocked
            state[mount]["stfind"] = {"status": "not_blocked"}
            nrpe_msgs.append(f"OK: Stfind not blocked on {mount}")

    if not fs_found:
        nrpe_msgs.append("CRITICAL: No filesystems found in sequence output")
        return NRPE_EXIT_CRIT, nrpe_msgs

    # Clean up stale entries from state (filesystems no longer present)
    stale_mounts = [mnt for mnt in state.keys() if mnt not in seen_mounts]
    for mnt in stale_mounts:
        del state[mnt]

    # Save updated state
    save_sequence_state(state)

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
            "    --passfail|-p     Exit with either a 0 (success), 1 for warning, or 2 for critical\n"
            "\n"
            "The following check operations are available:\n"
            "\n"
            "    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted\n"
            "    service     - check if the ScoutAM service is running\n"
            "    scheduler   - check if the scheduler is running on the leader node\n"
            "    sequences   - check if Arfind/Stfind restart are blocked (requires threshold args)\n"
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
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output for troubleshooting")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug output (includes command output)")
    parser.add_argument("--arfind-warn", type=int, default=300, help="Arfind warning threshold in seconds (default: 300)")
    parser.add_argument("--arfind-crit", type=int, default=600, help="Arfind critical threshold in seconds (default: 600)")
    parser.add_argument("--stfind-warn", type=int, default=300, help="Stfind warning threshold in seconds (default: 300)")
    parser.add_argument("--stfind-crit", type=int, default=600, help="Stfind critical threshold in seconds (default: 600)")
    parser.add_argument("operation", choices=["mount", "service", "scheduler", "sequences", "gateway", "versitygw", "scoutam", "all"])
    parser.add_argument("crit_thresh", type=int, nargs="?", default=90)
    parser.add_argument("warn_thresh", type=int, nargs="?", default=70)

    return parser.parse_args()

def main():
    args = parse_args()

    # Set global debug/verbose flags
    global DEBUG, VERBOSE
    DEBUG = args.debug
    VERBOSE = args.verbose

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

    if args.operation == "sequences":
        nrpe_status, msgs = check_sequences(args)
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
