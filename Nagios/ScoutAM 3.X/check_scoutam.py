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

# Command to escalate privileges for samcli usage
SUDO_CMD = "/bin/sudo"

# SystemD services
SCOUTAM_SERVICE = "scoutam"
SCOUTFS_FENCED_SERVICE = "scoutfs-fenced"
VERSITYGW_SERVICE = "versitygw@"
SCOUTGW_SERVICE = "scoutgw@"
SCOUTSYNC_SERVICE = "scoutsync@"

# Configuration locations
VERSITYGW_CONF_DIR = "/etc/versitygw.d"
SCOUTGW_CONF_DIR = "/etc/scoutgw.d"
SCOUTSYNC_CONF_DIR = "/etc/scoutsync.d"

# State file for sequence restart monitoring
STATE_FILE = "/var/lib/nagios/check_scoutam_sequences.json"

# State file for stuck scheduler packet monitoring
JOBS_STATE_FILE = "/var/lib/nagios/check_scoutam_jobs.json"

# Scheduler queues that indicate a stuck/waiting packet
STUCK_QUEUE_NAMES = {"PENDING-Q", "WAIT-Q"}

# Date format used by samcli scheduler --detail
SCHEDULER_DATE_FMT = "%b %d %H:%M:%S %Z %Y"

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
        if stdout:
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
    command = [SUDO_CMD, SAMCLI_CMD, "fs", "stat", "-m", mount]
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
    command = [SUDO_CMD, SAMCLI_CMD, "system"]
    error, stdout, ret = cmd(command)

    if ret != 0:
        error_msg = f"Failed to execute samcli system: {'; '.join(error) if error else 'unknown error'}"
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

def load_jobs_state():
    """Load previous jobs state for archset-level notification tracking."""
    if not os.path.exists(JOBS_STATE_FILE):
        return {}
    try:
        with open(JOBS_STATE_FILE, 'r') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"WARN: Jobs state file unreadable, skipping transition detection: {e}", file=sys.stderr)
        return {}

def send_notify(message, severity, timestamp):
    """Send a notification via samcli notify message. Failures are logged as warnings."""
    command = [SUDO_CMD, SAMCLI_CMD, "notify", "message",
               "--message", message,
               "--severity", str(severity),
               "--timestamp", str(int(timestamp))]
    error, stdout, ret = cmd(command)
    if ret != 0:
        error_str = "; ".join(error) if error else "unknown error"
        print(f"WARN: Failed to send notification: {error_str}", file=sys.stderr)
    else:
        debug_print(f"Notification sent (severity {severity}): {message}", "VERBOSE")

def save_jobs_state(state):
    """Save job state dict to JSON file with atomic write and file locking."""
    state_dir = os.path.dirname(JOBS_STATE_FILE)
    if state_dir and not os.path.exists(state_dir):
        try:
            os.makedirs(state_dir, mode=0o750)
        except OSError as e:
            print(f"WARN: Could not create state directory {state_dir}: {e}", file=sys.stderr)
            return

    temp_file = JOBS_STATE_FILE + ".tmp"
    try:
        with open(temp_file, 'w') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(state, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.chmod(temp_file, 0o640)
        os.rename(temp_file, JOBS_STATE_FILE)
    except (IOError, OSError) as e:
        print(f"WARN: Could not save jobs state file {JOBS_STATE_FILE}: {e}", file=sys.stderr)
        if os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except OSError:
                pass

def check_jobs(args):
    """
    Check for ScoutAM scheduler packets stuck in non-running queues.

    Parses `samcli scheduler --detail` output. Each packet has a Created
    timestamp so age is computed directly. A JSON state file is maintained
    as a live snapshot for external monitoring tools — entries are added or
    updated each run and removed when packets complete or leave the queue.

    Queues considered stuck: PENDING-Q, WAIT-Q (anything not RUNNING/RESERVING).
    Reports WARN/CRIT when a packet has been queued beyond the configured thresholds.
    """
    debug_print("Starting stuck jobs check", "VERBOSE")
    nrpe_status = NRPE_EXIT_OK
    nrpe_msgs = []
    now = time.time()

    command = [SUDO_CMD, SAMCLI_CMD, "scheduler", "--detail"]
    error, stdout, ret = cmd(command)
    if ret != 0:
        error_str = "; ".join(error) if error else "unknown error"
        nrpe_msgs.append(f"CRITICAL: Scheduler job check failed: {error_str}")
        return NRPE_EXIT_CRIT, nrpe_msgs

    warn_secs = args.job_warn * 3600
    crit_secs = args.job_crit * 3600

    prev_archsets = load_jobs_state().get("archsets", {})
    packets_snapshot = []

    # Detail output format per queue section:
    #   <QUEUE_NAME>
    #   ----------
    #   ID: <id> TYPE: <type> FSID: <fsid> Archset: <archset>   (or Volume:/Library:)
    #   Created: Jan 02 15:04:05 MST 2006
    #   resource: <r>, priority: <p>
    #   [message: <reason>]
    #   total sections: <n>, data size: <size>
    #   <blank>

    packet_type_names = {"A": "Archive", "S": "Stage", "M": "Media", "L": "Library"}

    id_re      = re.compile(r'^ID:\s*(\S+)\s+TYPE:\s*(\S+)')
    detail_re  = re.compile(r'(?:Archset|Volume|Library):\s*(\S+)')
    created_re = re.compile(r'^Created:\s+(.+)$')
    message_re = re.compile(r'^message:\s+(.+)$')

    current_queue = None
    current_packet = None
    packets_checked = 0
    stuck_found = 0

    def _evaluate_packet(pkt):
        nonlocal nrpe_status, packets_checked, stuck_found
        packets_checked += 1
        try:
            created_t = time.mktime(time.strptime(pkt["created"], SCHEDULER_DATE_FMT))
        except ValueError as e:
            debug_print(f"Could not parse Created timestamp '{pkt['created']}': {e}", "VERBOSE")
            return
        age = now - created_t
        age_h = age / 3600
        ptype_name = packet_type_names.get(pkt['ptype'], pkt['ptype'])
        label = f"{ptype_name} packet {pkt['id']}"
        if pkt['detail']:
            label += f" ({pkt['detail']})"
        label += f" queued in {pkt['queue']} for {age_h:.1f}h"
        if pkt['reason']:
            label += f" - reason: {pkt['reason']}"
        debug_print(f"Evaluating {label}", "VERBOSE")
        if age >= crit_secs:
            pkt_status = "crit"
            nrpe_status = max(nrpe_status, NRPE_EXIT_CRIT)
            stuck_found += 1
        elif age >= warn_secs:
            pkt_status = "warn"
            nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)
            stuck_found += 1
        else:
            pkt_status = "ok"

        packets_snapshot.append({
            "id": pkt['id'],
            "type": ptype_name,
            "detail": pkt['detail'],
            "queue": pkt['queue'],
            "created_epoch": int(created_t),
            "created_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_t)),
            "reason": pkt['reason'],
            "age_hours": round(age_h, 2),
            "status": pkt_status,
        })

    for line in stdout:
        line = line.rstrip()

        # Detect queue header (non-indented word, not a dashed separator or packet field)
        if line and not line.startswith(' ') and not line.startswith('\t') \
                and not line.startswith('-') and not line.startswith('ID:') \
                and not line.startswith('Created:') and not line.startswith('resource:') \
                and not line.startswith('message:') and not line.startswith('total'):
            current_queue = line.strip()
            debug_print(f"Entering queue section: {current_queue}", "VERBOSE")
            current_packet = None
            continue

        # Only care about stuck queues
        if current_queue not in STUCK_QUEUE_NAMES:
            continue

        id_match = id_re.match(line)
        if id_match:
            current_packet = {
                "id": id_match.group(1),
                "ptype": id_match.group(2),
                "detail": detail_re.search(line),
                "queue": current_queue,
                "created": None,
                "reason": None,
            }
            if current_packet["detail"]:
                current_packet["detail"] = current_packet["detail"].group(1)
            continue

        if current_packet is None:
            continue

        created_match = created_re.match(line)
        if created_match:
            current_packet["created"] = created_match.group(1).strip()
            continue

        message_match = message_re.match(line)
        if message_match:
            current_packet["reason"] = message_match.group(1).strip()
            continue

        # Blank line — packet block complete, evaluate it
        if line == "" and current_packet.get("created"):
            pkt = current_packet
            current_packet = None
            _evaluate_packet(pkt)

    # Flush final packet if output had no trailing blank line
    if current_packet and current_packet.get("created"):
        _evaluate_packet(current_packet)

    # Emit one grouped message per (status, type, detail, queue, reason) combination.
    # OK packets are suppressed individually — only the end summary is shown.
    groups = {}
    for p in packets_snapshot:
        if p["status"] == "ok":
            continue
        key = (p["status"], p["type"], p["detail"], p["queue"], p["reason"])
        if key not in groups:
            groups[key] = []
        groups[key].append(p)

    for (pkt_status, ptype_name, detail, queue, reason), grp in groups.items():
        count = len(grp)
        oldest_h = max(p["age_hours"] for p in grp)
        threshold = args.job_crit if pkt_status == "crit" else args.job_warn
        prefix = "CRITICAL" if pkt_status == "crit" else "WARN"
        noun = "packet" if count == 1 else "packets"
        msg = f"{prefix}: {count} {ptype_name} {noun}"
        if detail:
            msg += f" ({detail})"
        msg += f" queued in {queue}"
        if count == 1:
            msg += f" for {oldest_h:.1f}h"
        else:
            msg += f", oldest {oldest_h:.1f}h"
        if reason:
            msg += f" - reason: {reason}"
        msg += f" (threshold: {threshold}h)"
        nrpe_msgs.append(msg)

    if packets_checked == 0:
        nrpe_msgs.append("OK: No queued packets found in PENDING-Q or WAIT-Q")
    elif stuck_found == 0 and nrpe_status == NRPE_EXIT_OK:
        nrpe_msgs.append(f"OK: {packets_checked} queued packet(s) checked, none stuck beyond threshold")

    # Build archset-keyed structure for the state file.
    # Each archset (e.g. "archive-test.1") is the root key; per-packet fields
    # that don't vary (type, queue, reason) live at the archset level.
    # Individual packets carry only the fields that differ: id, age, timestamps, status.
    status_rank = {"ok": 0, "warn": 1, "crit": 2}
    archsets = {}
    for p in packets_snapshot:
        key = p["detail"] or f"{p['type']}-{p['id']}"
        if key not in archsets:
            archsets[key] = {
                "type": p["type"],
                "queue": p["queue"],
                "status": p["status"],
                "packet_count": 0,
                "oldest_age_hours": 0.0,
                "reason": p["reason"],
                "packets": [],
            }
        entry = archsets[key]
        if status_rank[p["status"]] > status_rank[entry["status"]]:
            entry["status"] = p["status"]
        if p["age_hours"] > entry["oldest_age_hours"]:
            entry["oldest_age_hours"] = p["age_hours"]
        entry["packet_count"] += 1
        entry["packets"].append({
            "id": p["id"],
            "created_epoch": p["created_epoch"],
            "created_iso": p["created_iso"],
            "age_hours": p["age_hours"],
            "status": p["status"],
        })

    # Fire one notification per archset on status transitions
    for key, entry in archsets.items():
        prev_notified = prev_archsets.get(key, {}).get("notified_status", "ok")
        curr_status = entry["status"]
        count = entry["packet_count"]
        oldest_h = entry["oldest_age_hours"]
        noun = "packet" if count == 1 else "packets"
        notify_msg = f"{count} {entry['type']} {noun} ({key}) queued in {entry['queue']}, oldest {oldest_h:.1f}h"
        if entry["reason"]:
            notify_msg += f" - reason: {entry['reason']}"
        if curr_status == "ok":
            entry["notified_status"] = "ok"
        elif curr_status == "crit" and prev_notified != "crit":
            send_notify(notify_msg, 3, now)
            entry["notified_status"] = "crit"
            debug_print(f"Archset {key}: transition {prev_notified} -> crit, notified", "VERBOSE")
        elif curr_status == "warn" and prev_notified == "ok":
            send_notify(notify_msg, 2, now)
            entry["notified_status"] = "warn"
            debug_print(f"Archset {key}: transition ok -> warn, notified", "VERBOSE")
        else:
            entry["notified_status"] = prev_notified

    status_label = {NRPE_EXIT_OK: "ok", NRPE_EXIT_WARN: "warn", NRPE_EXIT_CRIT: "crit"}
    counts = {"ok": 0, "warn": 0, "crit": 0}
    for p in packets_snapshot:
        counts[p["status"]] += 1

    save_jobs_state({
        "generated_at": int(now),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "hostname": socket.gethostname(),
        "overall_status": status_label.get(nrpe_status, "ok"),
        "summary": {
            "total_packets": len(packets_snapshot),
            "total_archsets": len(archsets),
            "ok": counts["ok"],
            "warn": counts["warn"],
            "crit": counts["crit"],
        },
        "archsets": archsets,
    })

    return nrpe_status, nrpe_msgs

# Check the state of the scheduler
def check_scheduler(args):
    nrpe_status = NRPE_EXIT_OK
    nrpe_state = "OK"
    nrpe_msgs = []

    command = [SUDO_CMD, SAMCLI_CMD, "scheduler"]
    error, stdout, ret = cmd(command)
    if ret != 0:
        error_str = "; ".join(error) if error else "unknown error"
        nrpe_msgs.append(f"CRITICAL: ScoutAM scheduler check failed: {error_str}")
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
        nrpe_msgs.append(f"CRITICAL: ScoutFS check failed: {'; '.join(error) if error else 'unknown error'}")
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

        error, usage = get_usage(mount['mount'])
        if error is not None:
            nrpe_msgs.append(f"CRITICAL: ScoutFS failed to get usage: {'; '.join(error) if error else 'unknown error'}")
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
        nrpe_msgs.append(f"OK: {name} configuration directory {conf_dir} not found, skipping check")
        return nrpe_status, nrpe_msgs

    try:
        configs = [f for f in os.listdir(conf_dir)
            if f.endswith('.conf')]
    except Exception as e:
        nrpe_msgs.append(f"CRITICAL: {name} cannot access configuration directory {conf_dir}: {e}")
        return NRPE_EXIT_CRIT, nrpe_msgs

    if not configs:
        nrpe_msgs.append(f"WARN: No {name} configurations found in {conf_dir}")
        return NRPE_EXIT_WARN, nrpe_msgs

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

def check_scoutsync(args):
    nrpe_status = NRPE_EXIT_OK
    nrpe_msgs = []
    name = "scoutsync"
    conf_dir = SCOUTSYNC_CONF_DIR
    service_prefix = SCOUTSYNC_SERVICE

    if not shutil.which("scoutsync"):
        nrpe_msgs.append((
            f"OK: {name} is not installed, skipping check"))
        return nrpe_status, nrpe_msgs

    if not os.path.isdir(conf_dir):
        nrpe_msgs.append(f"OK: {name} configuration directory {conf_dir} not found, skipping check")
        return nrpe_status, nrpe_msgs

    try:
        configs = [f for f in os.listdir(conf_dir)
            if f.endswith('.conf')]
    except Exception as e:
        nrpe_msgs.append(f"CRITICAL: {name} cannot access configuration directory {conf_dir}: {e}")
        return NRPE_EXIT_CRIT, nrpe_msgs

    if not configs:
        nrpe_msgs.append(f"WARN: No {name} configurations found in {conf_dir}")
        return NRPE_EXIT_WARN, nrpe_msgs

    for conf in configs:
        # Skip example configuration file
        if conf.startswith("example"):
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
    command = [SUDO_CMD, SAMCLI_CMD, "debug", "seq", "-c"]
    error, stdout, ret = cmd(command)
    if ret != 0:
        nrpe_msgs.append(f"CRITICAL: Sequence check failed: {'; '.join(error) if error else 'unknown error'}")
        return NRPE_EXIT_CRIT, nrpe_msgs

    # Join output into single string for multi-line regex
    output = "\n".join(stdout)

    notify_warn_secs = args.seq_notify_warn * 3600
    notify_crit_secs = args.seq_notify_crit * 3600

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

        seen_mounts.add(mount)

        # Filter by mount if specified
        if args.mount and mount != args.mount:
            continue

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

            # Check NRPE thresholds
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

            # Fire ScoutAM notification on first crossing of notify thresholds
            prev_notified = state[mount]["arfind"].get("notified_status", "ok")
            if duration >= notify_crit_secs and prev_notified != "crit":
                send_notify(f"Arfind blocked for {int(duration)}s on {mount} (inode {inode}: {reason})", 3, current_time)
                state[mount]["arfind"]["notified_status"] = "crit"
            elif duration >= notify_warn_secs and prev_notified == "ok":
                send_notify(f"Arfind blocked for {int(duration)}s on {mount} (inode {inode}: {reason})", 2, current_time)
                state[mount]["arfind"]["notified_status"] = "warn"
        elif arfind_not_blocked_regex.search(content):
            state[mount]["arfind"] = {"status": "not_blocked"}
            nrpe_msgs.append(f"OK: Arfind not blocked on {mount}")
        else:
            nrpe_msgs.append(f"WARN: Arfind status not found in sequence output for {mount}")
            nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)

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

            # Check NRPE thresholds
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

            # Fire ScoutAM notification on first crossing of notify thresholds
            prev_notified = state[mount]["stfind"].get("notified_status", "ok")
            if duration >= notify_crit_secs and prev_notified != "crit":
                send_notify(f"Stfind blocked for {int(duration)}s on {mount} (inode {inode}: {reason})", 3, current_time)
                state[mount]["stfind"]["notified_status"] = "crit"
            elif duration >= notify_warn_secs and prev_notified == "ok":
                send_notify(f"Stfind blocked for {int(duration)}s on {mount} (inode {inode}: {reason})", 2, current_time)
                state[mount]["stfind"]["notified_status"] = "warn"
        elif stfind_not_blocked_regex.search(content):
            state[mount]["stfind"] = {"status": "not_blocked"}
            nrpe_msgs.append(f"OK: Stfind not blocked on {mount}")
        else:
            nrpe_msgs.append(f"WARN: Stfind status not found in sequence output for {mount}")
            nrpe_status = max(nrpe_status, NRPE_EXIT_WARN)

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
            "check_scoutam.py [--help|-h] [--mount|-m MOUNT] [--passfail|-p] [options] operation\n"
            "\n"
            "Optional arguments:\n"
            "\n"
            "    --help|-h              Print help message and exit\n"
            "    --mount|-m MOUNT       Mount point to check\n"
            "    --passfail|-p          Exit with 0 (ok), 1 (warn), or 2 (crit) only, no output\n"
            "    --verbose|-v           Enable verbose output for troubleshooting\n"
            "    --debug|-d             Enable debug output (includes command output)\n"
            "\n"
            "Filesystem usage thresholds (mount operation):\n"
            "\n"
            "    warn_thresh            Data/metadata usage warning percent (default: 70)\n"
            "    crit_thresh            Data/metadata usage critical percent (default: 90)\n"
            "\n"
            "Arfind/Stfind sequence thresholds (sequences operation):\n"
            "\n"
            "    --arfind-warn SECS     Arfind NRPE warning threshold in seconds (default: 300)\n"
            "    --arfind-crit SECS     Arfind NRPE critical threshold in seconds (default: 600)\n"
            "    --stfind-warn SECS     Stfind NRPE warning threshold in seconds (default: 300)\n"
            "    --stfind-crit SECS     Stfind NRPE critical threshold in seconds (default: 600)\n"
            "    --seq-notify-warn HRS  ScoutAM notify warn threshold in hours (default: 2)\n"
            "    --seq-notify-crit HRS  ScoutAM notify critical threshold in hours (default: 6)\n"
            "\n"
            "Stuck job thresholds (jobs operation):\n"
            "\n"
            "    --job-warn HRS         PENDING-Q/WAIT-Q warning threshold in hours (default: 5)\n"
            "    --job-crit HRS         PENDING-Q/WAIT-Q critical threshold in hours (default: 12)\n"
            "\n"
            "Operations:\n"
            "\n"
            "    mount       Check ScoutFS filesystem usage against warn/crit thresholds\n"
            "    service     Check if the ScoutAM service is running\n"
            "    scheduler   Check if the scheduler is running and not idled\n"
            "    sequences   Check if Arfind/Stfind restart sequences are blocked\n"
            "    jobs        Check for scheduler packets stuck in PENDING-Q or WAIT-Q\n"
            "    gateway     Check if all configured ScoutAM S3 gateway instances are running\n"
            "    versitygw   Check if all configured VersityGW S3 gateway instances are running\n"
            "    scoutsync   Check if all configured ScoutSync instances are running\n"
            "    scoutam     Run mount, service, and scheduler checks together\n"
            "    all         Run all checks including gateways and scoutsync\n"
            "\n"
        ),
        add_help=False
    )

    parser.add_argument("--passfail", "-p", action="store_true")
    parser.add_argument("--mount", "-m", type=str)
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output for troubleshooting")
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug output (includes command output)")
    parser.add_argument("--arfind-warn", type=int, default=300, help="Arfind NRPE warning threshold in seconds (default: 300)")
    parser.add_argument("--arfind-crit", type=int, default=600, help="Arfind NRPE critical threshold in seconds (default: 600)")
    parser.add_argument("--stfind-warn", type=int, default=300, help="Stfind NRPE warning threshold in seconds (default: 300)")
    parser.add_argument("--stfind-crit", type=int, default=600, help="Stfind NRPE critical threshold in seconds (default: 600)")
    parser.add_argument("--seq-notify-warn", type=int, default=2, help="Arfind/Stfind notification warning threshold in hours (default: 2)")
    parser.add_argument("--seq-notify-crit", type=int, default=6, help="Arfind/Stfind notification critical threshold in hours (default: 6)")
    parser.add_argument("--job-warn", type=int, default=5, help="Stuck job warning threshold in hours (default: 5)")
    parser.add_argument("--job-crit", type=int, default=12, help="Stuck job critical threshold in hours (default: 12)")
    parser.add_argument("operation", choices=["mount", "service", "scheduler", "sequences", "jobs", "gateway", "versitygw", "scoutam", "scoutsync", "all"])
    parser.add_argument("warn_thresh", type=int, nargs="?", default=70)
    parser.add_argument("crit_thresh", type=int, nargs="?", default=90)

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

    if not os.path.isfile(SCOUTFS_CMD) or not os.access(SCOUTFS_CMD, os.X_OK):
        print("CRITICAL: ScoutFS is not installed or missing binaries")
        sys.exit(NRPE_EXIT_CRIT)

    if not os.path.isfile(SCOUTAM_MONITOR_CMD) or not os.access(SCOUTAM_MONITOR_CMD, os.X_OK):
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

    if args.operation in {"sequences", "all"}:
        nrpe_status, msgs = check_sequences(args)
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"jobs", "all"}:
        nrpe_status, msgs = check_jobs(args)
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"gateway", "all"}:
        nrpe_status, msgs = check_gateway(args, "scoutgw")
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"versitygw", "all"}:
        nrpe_status, msgs = check_gateway(args, "versitygw")
        nrpe_msgs.extend(msgs)
        nrpe_checks[status_map[nrpe_status]] += 1

    if args.operation in {"scoutsync", "all"}:
        nrpe_status, msgs = check_scoutsync(args)
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
