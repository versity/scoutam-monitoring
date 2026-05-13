# NRPE Check Script `check_scoutam.py`

The main NRPE check script that runs on all ScoutAM nodes. The script has multiple checks embedded and will be called multiple times with each individual check.

## Requirements

- Python 3.6+
- ScoutFS and ScoutAM installed
- Commands available: `/usr/sbin/scoutfs`, `/usr/sbin/scoutam-monitor`, `/usr/bin/samcli`, `/bin/sudo`
- State file directory: `/var/lib/nagios` (writable by nagios user for sequence and jobs checks)
- Permissions: Script should run as nagios user or equivalent. `samcli` is invoked via `sudo`, so the nagios user must have sudoers entries for `/usr/bin/samcli`.

## Usage

```
check_scoutam.py [OPTIONS] operation [warn_thresh] [crit_thresh]

Options:
    --help|-h              Print help message and exit
    --mount|-m MOUNT       Filter checks to a specific mount point
    --passfail|-p          Exit with 0 (ok), 1 (warn), or 2 (crit) only, no output
    --verbose|-v           Enable verbose output for troubleshooting
    --debug|-d             Enable debug output (shows all commands executed)

    Filesystem usage thresholds (mount operation):
    warn_thresh            Data/metadata usage warning percent (default: 70)
    crit_thresh            Data/metadata usage critical percent (default: 90)

    Arfind/Stfind sequence thresholds (sequences operation):
    --arfind-warn SECS     Arfind NRPE warning threshold in seconds (default: 300)
    --arfind-crit SECS     Arfind NRPE critical threshold in seconds (default: 600)
    --stfind-warn SECS     Stfind NRPE warning threshold in seconds (default: 300)
    --stfind-crit SECS     Stfind NRPE critical threshold in seconds (default: 600)
    --seq-notify-warn HRS  ScoutAM notify warning threshold in hours (default: 2)
    --seq-notify-crit HRS  ScoutAM notify critical threshold in hours (default: 6)

    Stuck job thresholds (jobs operation):
    --job-warn HRS         PENDING-Q/WAIT-Q warning threshold in hours (default: 5)
    --job-crit HRS         PENDING-Q/WAIT-Q critical threshold in hours (default: 12)

Operations:
    mount       Check ScoutFS filesystem usage against warn/crit thresholds
    service     Check if the ScoutAM service is running
    scheduler   Check if the scheduler is running and not idled
    sequences   Check if Arfind/Stfind restart sequences are blocked (scheduler node only)
    jobs        Check for scheduler packets stuck in PENDING-Q or WAIT-Q
    gateway     Check if all configured ScoutAM (scoutgw) S3 gateway instances are running
    versitygw   Check if all configured VersityGW S3 gateway instances are running
    scoutsync   Check if all configured ScoutSync instances are running
    scoutam     Run mount, service, and scheduler checks together
    all         Run all checks, including gateways, scoutsync, sequences, and jobs
```

## ScoutAM Notifications

In addition to standard NRPE return codes, several checks (`sequences`, `jobs`) raise notifications inside ScoutAM by calling `samcli notify message` on threshold transitions. This is controlled at the top of the script:

```python
SAM_NOTIFY = True
```

Set `SAM_NOTIFY = False` to disable in-product notifications while still returning NRPE statuses to Nagios. Notifications are severity-2 (warn) and severity-3 (critical) and are only fired when a check first crosses a threshold, not on every run.

## Operations

### `check_scoutam.py mount [warn_thresh] [crit_thresh]`

Checks if the filesystem is mounted, then evaluates both the metadata and data capacity as reported by `/usr/sbin/scoutfs df` against optional warning and critical thresholds.

**Default thresholds:** 70% warning, 90% critical

**Examples:**
```bash
# Use default thresholds (70% warn, 90% crit)
./check_scoutam.py mount

# Custom thresholds (80% warn, 95% crit)
./check_scoutam.py mount 80 95

# Check specific mount only
./check_scoutam.py mount --mount /mnt/scoutfs/fs01

# Check with verbose output
./check_scoutam.py mount --verbose
```

**What it checks:**
- ScoutFS fencing service is active (`scoutfs-fenced`)
- At least one ScoutFS filesystem is mounted
- Data usage vs thresholds
- Metadata usage vs thresholds
- High watermark exceeded

### `check_scoutam.py service`

Verifies that the `scoutam` service is running using systemd.

```bash
./check_scoutam.py service
```

### `check_scoutam.py scheduler`

Uses `samcli scheduler` to check if the scheduler and archiver/staging processes are running (not idled).

```bash
./check_scoutam.py scheduler
```

**Sample output:**
```
OK: ScoutAM scheduler: running, archiver: running, staging: running
WARN: ScoutAM scheduler: running, archiver: idle, staging: running
```

### `check_scoutam.py sequences`

**Scheduler Node Only**: This check only executes on the active scheduler node. On non-scheduler nodes, it returns OK and removes any stale state file.

Monitors Arfind (archiver) and Stfind (staging) restart status using `samcli debug seq -c`. Tracks how long files have been blocking restart operations and alerts when thresholds are exceeded.

#### How It Works

1. **Detects scheduler node** via `samcli system`, parsing the `scheduler name` field.
2. **Hostname matching**: Compares short hostnames case-insensitively (e.g., "s82" matches "s82.vpn.versity.com").
3. **Non-scheduler nodes**: Returns OK and removes the stale state file if present.
4. **Scheduler node**:
   - Checks for blocked Arfind/Stfind restart conditions per filesystem
   - Persists state to track how long each block has been in effect
   - Returns NRPE WARN/CRIT when seconds-based thresholds are crossed
   - Fires a ScoutAM notification (severity 2 / 3) when hours-based notify thresholds are first crossed

#### State File Management

- **Location:** `/var/lib/nagios/check_scoutam_sequences.json`
- **Permissions:** 0640 (owner read/write, group read)
- **Directory permissions:** 0750 for `/var/lib/nagios`
- **Contents:** Last check timestamp, current FS sequence, Arfind/Stfind status per filesystem, last notification severity per check
- **Locking:** Uses `fcntl` file locking to prevent race conditions
- **Cleanup:** Automatically removed on non-scheduler nodes to prevent stale data

#### Threshold Configuration

NRPE alerts are tuned in **seconds** with `--arfind-warn` / `--arfind-crit` / `--stfind-warn` / `--stfind-crit`. ScoutAM in-product notifications are tuned in **hours** with `--seq-notify-warn` / `--seq-notify-crit`.

```bash
# Defaults: NRPE 5min warn / 10min crit, ScoutAM notify 2h warn / 6h crit
./check_scoutam.py sequences

# Custom NRPE thresholds
./check_scoutam.py sequences \
    --arfind-warn 600 --arfind-crit 1200 \
    --stfind-warn 300 --stfind-crit 600

# Custom notification thresholds (only fire after 4h warn / 12h crit)
./check_scoutam.py sequences \
    --seq-notify-warn 4 --seq-notify-crit 12

# Check specific mount only
./check_scoutam.py sequences --mount /mnt/scoutfs/fs01
```

#### Example Output

**Normal state:**
```
OK: Arfind not blocked on /mnt/scoutfs/fs01
OK: Stfind not blocked on /mnt/scoutfs/fs01
```

**Blocked but under threshold:**
```
OK: Arfind blocked for 250s on /mnt/scoutfs/fs01 (under threshold)
```

**Warning / Critical threshold exceeded:**
```
WARN: Arfind blocked for 350s on /mnt/scoutfs/fs01 (inode 513024: not archdone)
CRITICAL: Arfind blocked for 650s on /mnt/scoutfs/fs01 (inode 513024: not archdone)
```

**Non-scheduler node:**
```
OK: Not scheduler node, skipping sequence check (scheduler: s82.vpn.versity.com)
```

### `check_scoutam.py jobs`

Parses `samcli scheduler --detail` looking for scheduler packets that are sitting in non-running queues — currently `PENDING-Q` and `WAIT-Q`. Each packet's age is computed from its `Created` timestamp, so no prior state is required to alert.

NRPE thresholds are in **hours** (`--job-warn`, `--job-crit`). A live snapshot of every queued packet is written to `/var/lib/nagios/check_scoutam_jobs.json` for external monitoring tools to consume. ScoutAM notifications are fired per **archset** on status transitions (ok → warn, ok/warn → crit), so a single noisy archset will not flood the operator inbox.

```bash
# Defaults: 5h warn, 12h crit
./check_scoutam.py jobs

# Tighter thresholds
./check_scoutam.py jobs --job-warn 2 --job-crit 6
```

**Sample output:**
```
OK: 4 queued packet(s) checked, none stuck beyond threshold
WARN: 2 Archive packets (archive-test.1) queued in PENDING-Q, oldest 6.3h - reason: no media available (threshold: 5h)
CRITICAL: 1 Stage packet (stage-job.2) queued in WAIT-Q for 14.1h - reason: drive contention (threshold: 12h)
```

#### Jobs State File

- **Location:** `/var/lib/nagios/check_scoutam_jobs.json`
- **Structure:** Top-level summary plus per-archset entries with packet counts, oldest age, last-notified severity, and an array of individual packets.
- **Purpose:** Live snapshot — overwritten each run. Entries are removed when packets complete or leave the queue.

### `check_scoutam.py gateway`

Checks that every gateway configured under `/etc/scoutgw.d/*.conf` has a corresponding `scoutgw@<name>` systemd service in the active state. Silently skipped if the `scoutgw` binary is not installed or the directory is missing. The file `example.conf` is ignored.

### `check_scoutam.py versitygw`

Same as `gateway`, but for `/etc/versitygw.d/*.conf` and the `versitygw@<name>` services. Silently skipped if `versitygw` is not installed.

### `check_scoutam.py scoutsync`

Checks that every config under `/etc/scoutsync.d/*.conf` has its `scoutsync@<name>` service active. Silently skipped if `scoutsync` is not installed. Config files whose names start with `example` are ignored.

### `check_scoutam.py scoutam`

Runs the `mount`, `service`, and `scheduler` checks together.

### `check_scoutam.py all`

Runs every check above: `mount`, `service`, `scheduler`, `sequences`, `jobs`, `gateway`, `versitygw`, `scoutsync`. Non-installed components are skipped with OK.

## Troubleshooting

### Verbose Mode

Shows high-level operational flow — hostname checks, queue parsing, state loading, decision-making:

```bash
./check_scoutam.py sequences --verbose
```

```
[VERBOSE] Starting sequence restart check
[VERBOSE] Parsed scheduler name from samcli system: s82.vpn.versity.com
[VERBOSE] Current hostname: s81.vpn.versity.com
[VERBOSE] Comparing short names: scheduler='s82' current='s81'
[VERBOSE] Is this the scheduler node? False
[VERBOSE] Not scheduler node (scheduler is s82.vpn.versity.com), skipping check
```

### Debug Mode

Shows the actual subprocess commands, return codes, and output previews:

```bash
./check_scoutam.py mount --debug
```

```
[DEBUG] Executing command: ['/usr/sbin/scoutfs', 'df', '--path', '/mnt/scoutfs/fs01']
[DEBUG] Command completed successfully, return code: 0
[DEBUG] Output preview (first line): Type  Size    Total    Used     Free  Use%
```

### Common Issues

#### State file permission errors

Ensure `/var/lib/nagios` exists and is writable by the nagios user — it is used by both `sequences` and `jobs`:

```bash
sudo mkdir -p /var/lib/nagios
sudo chown nagios:nagios /var/lib/nagios
sudo chmod 750 /var/lib/nagios
```

#### `sudo: a password is required`

`samcli` is invoked through `/bin/sudo`. The nagios user needs a passwordless sudoers entry, for example:

```
nagios ALL=(root) NOPASSWD: /usr/bin/samcli
```

#### Command timeouts

Default timeout is 30 seconds per command. If commands hang, check:
- ScoutFS/ScoutAM service health: `systemctl status scoutam scoutfs-fenced`
- Network connectivity between nodes
- System load: `uptime`, `iostat`
- Filesystem responsiveness: `scoutfs df --path /mnt/scoutfs/fs01`

#### Sequence check always returns "Not scheduler node"

```bash
samcli system | grep "scheduler name"
hostname
```

Short names (before the first dot) must match, case-insensitive.

#### Watermark not found errors

```bash
samcli fs stat -m /mnt/scoutfs/fs01 | grep -i watermark
samcli fs watermark set --high 80 --low 70 /mnt/scoutfs/fs01
```

#### Inspecting / resetting state files

```bash
cat /var/lib/nagios/check_scoutam_sequences.json | python3 -m json.tool
cat /var/lib/nagios/check_scoutam_jobs.json       | python3 -m json.tool

# Reset (as nagios user)
rm -f /var/lib/nagios/check_scoutam_sequences.json
rm -f /var/lib/nagios/check_scoutam_jobs.json
```

## Technical Details

### File Locking

The script uses `fcntl` file locking to prevent race conditions when multiple NRPE checks run simultaneously:
- **Shared lock (LOCK_SH):** when reading state files
- **Exclusive lock (LOCK_EX):** when writing state files
- Locks are released automatically when file handles close
- State files are written via temp file + atomic rename

### Timeout Handling

All subprocess commands have a 30-second timeout. Timed-out commands return an error message and an exit code of -1, which surfaces as CRITICAL in the operation that invoked them.

### Security

- **State file permissions:** 0640 (owner read/write, group read)
- **State directory permissions:** 0750 (not world-accessible)
- **No shell execution:** All commands use list form to prevent injection
- **Absolute paths:** All executables (`/bin/sudo`, `/usr/sbin/scoutfs`, `/usr/sbin/scoutam-monitor`, `/usr/bin/samcli`) are referenced by absolute path

## Exit Codes

The script follows standard NRPE exit codes:
- **0 (OK):** Check passed successfully
- **1 (WARNING):** Warning threshold exceeded or non-critical issue
- **2 (CRITICAL):** Critical threshold exceeded or service failure

When `--passfail` is used, the script suppresses textual output and only returns the exit code.
