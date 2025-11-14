# NRPE Check Script `check_scoutam.py`

The main NRPE check script that runs on all ScoutAM nodes. The script has multiple checks embedded and will be called multiple times with each individual check.

## Requirements

- Python 3.6+
- ScoutFS and ScoutAM installed
- Commands available: `/usr/sbin/scoutfs`, `/usr/sbin/scoutam-monitor`, `/usr/bin/samcli`
- State file directory: `/var/lib/nagios` (writable by nagios user for sequence checks)
- Permissions: Script should run as nagios user or equivalent

## Usage

```
check_scoutam.py [OPTIONS] operation [warn_thresh] [crit_thresh]

Options:
    --help|-h           Print help message and exit
    --mount|-m MOUNT    Filter checks to specific mount point
    --passfail|-p       Exit with 0 (success), 1 (warning), or 2 (critical)
    --verbose|-v        Enable verbose output for troubleshooting
    --debug|-d          Enable debug output (shows all commands executed)
    --arfind-warn SEC   Arfind warning threshold in seconds (default: 300)
    --arfind-crit SEC   Arfind critical threshold in seconds (default: 600)
    --stfind-warn SEC   Stfind warning threshold in seconds (default: 300)
    --stfind-crit SEC   Stfind critical threshold in seconds (default: 600)

Operations:
    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted
    service     - check if the ScoutAM service is running
    scheduler   - check if the scheduler is running on the leader node
    sequences   - check if Arfind/Stfind restart are blocked (scheduler node only)
    gateway     - check if all configured ScoutAM S3 gateway services are running
    versitygw   - check if all configured Versity S3 gateway services are running
    scoutam     - check mount, scoutam, and scheduler
    all         - check all including S3 gateways
```

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

**Example:**
```bash
./check_scoutam.py service
```

### `check_scoutam.py scheduler`

Uses `samcli scheduler` to check if the scheduler and archiver/staging processes are running.

**Example:**
```bash
./check_scoutam.py scheduler
```

**Sample output:**
```
OK: ScoutAM scheduler: running, archiver: running, staging: running
WARN: ScoutAM scheduler: running, archiver: idle, staging: running
```

### `check_scoutam.py sequences`

**Scheduler Node Only**: This check only executes on the active scheduler node. On non-scheduler nodes, it returns OK and skips the check.

Monitors Arfind (archiver) and Stfind (staging) restart status using `samcli debug seq -c`. Tracks how long files have been blocking restart operations and alerts when thresholds are exceeded.

#### How It Works

1. **Detects scheduler node**: Uses `samcli system` to determine the active scheduler
2. **Hostname matching**: Compares short hostnames (e.g., "s82" matches "s82.vpn.versity.com")
3. **Non-scheduler nodes**: Returns OK and removes stale state file
4. **Scheduler node**:
   - Checks for blocked restart conditions
   - Persists state to track duration
   - Alerts when thresholds exceeded

#### State File Management

- **Location:** `/var/lib/nagios/check_scoutam_sequences.json`
- **Permissions:** 0640 (owner read/write, group read)
- **Directory permissions:** 0750 for `/var/lib/nagios`
- **Contents:** Last check timestamp, current FS sequence, Arfind/Stfind status per filesystem
- **Locking:** Uses `fcntl` file locking to prevent race conditions
- **Cleanup:** Automatically removed on non-scheduler nodes to prevent stale data

#### Threshold Configuration

Use separate thresholds for Arfind (archiver) and Stfind (staging):

```bash
# Default: 5 minutes warning, 10 minutes critical for both
./check_scoutam.py sequences

# Custom thresholds
./check_scoutam.py sequences \
    --arfind-warn 600 --arfind-crit 1200 \
    --stfind-warn 300 --stfind-crit 600

# Check specific mount only
./check_scoutam.py sequences --mount /mnt/scoutfs/fs01

# Conservative thresholds (30min warn, 2hr crit)
./check_scoutam.py sequences \
    --arfind-warn 1800 --arfind-crit 7200 \
    --stfind-warn 1800 --stfind-crit 7200

# Aggressive thresholds (30sec warn, 60sec crit)
./check_scoutam.py sequences \
    --arfind-warn 30 --arfind-crit 60 \
    --stfind-warn 30 --stfind-crit 60
```

#### Example Output

**Normal state:**
```
OK: Arfind not blocked on /mnt/scoutfs/fs01
OK: Stfind not blocked on /mnt/scoutfs/fs01
OK: Arfind not blocked on /mnt/scoutfs/fs02
OK: Stfind not blocked on /mnt/scoutfs/fs02
```

**Blocked but under threshold:**
```
OK: Arfind blocked for 250s on /mnt/scoutfs/fs01 (under threshold)
OK: Stfind not blocked on /mnt/scoutfs/fs01
```

**Warning threshold exceeded:**
```
WARN: Arfind blocked for 350s on /mnt/scoutfs/fs01 (inode 513024: not archdone)
OK: Stfind not blocked on /mnt/scoutfs/fs01
```

**Critical threshold exceeded:**
```
CRITICAL: Arfind blocked for 650s on /mnt/scoutfs/fs01 (inode 513024: not archdone)
OK: Stfind not blocked on /mnt/scoutfs/fs01
```

**Non-scheduler node:**
```
OK: Not scheduler node, skipping sequence check (scheduler: s82.vpn.versity.com)
```

### `check_scoutam.py scoutam`

Runs the `mount`, `service`, and `scheduler` checks together.

**Example:**
```bash
./check_scoutam.py scoutam
```

### `check_scoutam.py gateway`

Checks if the gateways configured in `/etc/scoutgw.d` are running using systemd.

**Example:**
```bash
./check_scoutam.py gateway
```

### `check_scoutam.py versitygw`

Checks if the gateways configured in `/etc/versitygw.d` are running using systemd.

**Example:**
```bash
./check_scoutam.py versitygw
```

### `check_scoutam.py all`

Runs all checks including S3 gateways (scoutgw and versitygw).

**Example:**
```bash
./check_scoutam.py all
```

## Usage Examples

### Basic Checks

```bash
# Check if filesystems are mounted (default thresholds: 70% warn, 90% crit)
./check_scoutam.py mount

# Check with custom thresholds
./check_scoutam.py mount 80 95

# Check specific mount point only
./check_scoutam.py mount --mount /mnt/scoutfs/fs01

# Check all services
./check_scoutam.py all

# Check scheduler status
./check_scoutam.py scheduler
```

### Sequence Monitoring

```bash
# Default thresholds (5min warn, 10min crit)
./check_scoutam.py sequences

# Aggressive thresholds (30sec warn, 60sec crit)
./check_scoutam.py sequences \
    --arfind-warn 30 --arfind-crit 60 \
    --stfind-warn 30 --stfind-crit 60

# Conservative thresholds (30min warn, 2hr crit)
./check_scoutam.py sequences \
    --arfind-warn 1800 --arfind-crit 7200 \
    --stfind-warn 1800 --stfind-crit 7200

# Monitor specific filesystem
./check_scoutam.py sequences --mount /mnt/scoutfs/fs01
```

### Troubleshooting

```bash
# See what's happening (high-level flow)
./check_scoutam.py sequences --verbose

# Debug failed checks (detailed command output)
./check_scoutam.py mount --debug

# Combine options
./check_scoutam.py sequences --mount /mnt/scoutfs/fs01 --verbose

# Debug specific mount
./check_scoutam.py mount --mount /mnt/scoutfs/fs01 --debug
```

## Troubleshooting

### Verbose Mode

Shows high-level operational flow including hostname checks, state loading, and decision-making:

```bash
./check_scoutam.py sequences --verbose
```

**Example output:**
```
[VERBOSE] Starting sequence restart check
[VERBOSE] Parsed scheduler name from samcli system: s82.vpn.versity.com
[VERBOSE] Current hostname: s81.vpn.versity.com
[VERBOSE] Comparing short names: scheduler='s82' current='s81'
[VERBOSE] Is this the scheduler node? False
[VERBOSE] Not scheduler node (scheduler is s82.vpn.versity.com), skipping check
```

### Debug Mode

Shows detailed command execution, return codes, output previews, and state transitions:

```bash
./check_scoutam.py mount --debug
```

**Example output:**
```
[DEBUG] Executing command: ['/usr/sbin/scoutfs', 'df', '--path', '/mnt/scoutfs/fs01']
[DEBUG] Command completed successfully, return code: 0
[DEBUG] Output preview (first line): Type  Size    Total    Used     Free  Use%
[DEBUG] Executing command: ['/usr/bin/samcli', 'fs', 'stat', '-m', '/mnt/scoutfs/fs01']
[DEBUG] Command completed successfully, return code: 0
[DEBUG] Output preview (first line): Data Total:      20 GiB
```

### Common Issues

#### State file permission errors

Ensure `/var/lib/nagios` directory exists and is writable by the nagios user:

```bash
# Create directory
sudo mkdir -p /var/lib/nagios

# Set ownership
sudo chown nagios:nagios /var/lib/nagios

# Set permissions
sudo chmod 750 /var/lib/nagios
```

#### Command timeouts

Default timeout is 30 seconds per command. If commands hang, check:
- ScoutFS/ScoutAM service health: `systemctl status scoutam scoutfs-fenced`
- Network connectivity between nodes
- System load: `uptime`, `iostat`
- Filesystem responsiveness: `scoutfs df --path /mnt/scoutfs/fs01`

Use debug mode to see which command is timing out:
```bash
./check_scoutam.py mount --debug
```

#### Sequence check always returns "Not scheduler node"

Verify the current node is actually the scheduler:

```bash
# Check which node is scheduler
samcli system | grep "scheduler name"

# Check current hostname
hostname

# Compare (short names must match)
# Example: "s82" matches "s82.vpn.versity.com"
```

The script compares short hostnames (before the first dot) in a case-insensitive manner.

#### Watermark not found errors

If you see errors about high/low watermark not found:

```bash
# Check if watermarks are set
samcli fs stat -m /mnt/scoutfs/fs01 | grep -i watermark

# Set watermarks if missing (example values)
samcli fs watermark set --high 80 --low 70 /mnt/scoutfs/fs01
```

#### State file shows old data

The state file is automatically managed:
- **On scheduler node:** Updated on each check run
- **On non-scheduler nodes:** Automatically removed

To manually inspect state:
```bash
cat /var/lib/nagios/check_scoutam_sequences.json | python3 -m json.tool
```

To manually reset state (as nagios user):
```bash
rm -f /var/lib/nagios/check_scoutam_sequences.json
```

## Technical Details

### File Locking

The script uses `fcntl` file locking to prevent race conditions when multiple NRPE checks run simultaneously:
- **Shared lock (LOCK_SH):** Used when reading state file
- **Exclusive lock (LOCK_EX):** Used when writing state file
- Locks are automatically released when file handles close

### Timeout Handling

All subprocess commands have a 30-second timeout to prevent hanging:
- Commands that exceed timeout return error with timeout message
- Use `--debug` to see timeout errors in detail

### Security

- **State file permissions:** 0640 (owner read/write, group read)
- **State directory permissions:** 0750 (not world-accessible)
- **No shell execution:** All commands use list form to prevent injection
- **Absolute paths:** All executables referenced by absolute path

### Performance

- **Concurrent safe:** File locking allows multiple checks to run safely
- **State caching:** Sequence state persisted between runs for accurate duration tracking
- **Minimal overhead:** Only scheduler node performs sequence checks

## Exit Codes

The script follows standard NRPE exit codes:
- **0 (OK):** Check passed successfully
- **1 (WARNING):** Warning threshold exceeded or non-critical issue
- **2 (CRITICAL):** Critical threshold exceeded or service failure
- **-1 (UNKNOWN):** Command timeout or unexpected error

When `--passfail` is used, warnings return 0 instead of 1.
