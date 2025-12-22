# NRPE Check Script `check_scoutam.py`

The main NRPE check script that runs on all ScoutAM nodes. The script has multiple checks embedded and will be called multiple times with each individual check.

Supports both Nagios/NRPE and Zabbix agent integration.

## Requirements

- Python 3.7+
- ScoutFS and ScoutAM installed
- Commands available: `/usr/sbin/scoutfs`, `/usr/sbin/scoutam-monitor`, `/usr/bin/samcli`
- State file directory: `/var/lib/nagios` (configurable via `--state-dir`)
- Permissions: Script should run as nagios/zabbix user or equivalent

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
    --flap-count N      Number of state changes to trigger flap warning (default: 3)
    --flap-window TIME  Time window for flap detection, e.g., 10m, 30m, 1h (default: 10m)
    --state-dir DIR     Directory for state files (default: /var/lib/nagios)
    --check-timeout SEC Timeout per command in seconds (default: 30)
    --zabbix            Zabbix mode: always exit 0, status in output text
    --json              Output JSON format for Zabbix dependent items

Operations:
    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted
    service     - check if the ScoutAM service is running
    scheduler   - check if the scheduler is running on the leader node
    sequences   - check if Arfind/Stfind restart are blocked (scheduler node only)
    resources   - check the state of resources (scheduler node only)
    gateway     - check if all configured ScoutAM S3 gateway services are running
    versitygw   - check if all configured Versity S3 gateway services are running
    scoutsync   - check if all configured ScoutSync services are running
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

- **Location:** `<state-dir>/check_scoutam_sequences.json` (default: `/var/lib/nagios`)
- **Permissions:** 0640 (owner read/write, group read)
- **Directory permissions:** 0750 for state directory
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

### `check_scoutam.py resources`

**Scheduler Node Only**: This check only executes on the active scheduler node. On non-scheduler nodes, it returns OK and skips the check.

Monitors the state of resources (tape drives, media changers) using `samcli resource`. Reports resources by domain and device type, with flap detection to identify unstable resources.

#### Flap Detection

Resources that frequently change state (e.g., cycling between `on` and `error`) are flagged as "FLAPPING". This helps identify hardware or configuration issues.

```bash
# Default: 3 state changes in 10 minutes triggers flap warning
./check_scoutam.py resources

# Custom flap detection
./check_scoutam.py resources --flap-count 5 --flap-window 30m

# More sensitive flap detection
./check_scoutam.py resources --flap-count 2 --flap-window 5m
```

#### State File Management

- **Location:** `<state-dir>/resources.json` (default: `/var/lib/nagios`)
- **Contents:** Resource state history for flap detection
- **Cleanup:** Automatically removed on non-scheduler nodes

#### Example Output

**Normal state:**
```
OK: 904A000001: LTO8 Drive (4 on), Media Changer (1 on)
OK: 904B000002: LTO8 Drive (2 on), Media Changer (1 on)
```

**Resources with issues:**
```
CRITICAL: 904A000001: LTO8 Drive (2 on, 1 error, 1 down), Media Changer (1 on)
WARN: 904B000002: LTO8 Drive (2 on, 1 off), Media Changer (1 on)
```

**Flapping resource:**
```
WARN: 904A000001: LTO8 Drive (3 on, 1 FLAPPING), Media Changer (1 on)
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

### `check_scoutam.py scoutsync`

Checks if the ScoutSync services configured in `/etc/scoutsync.d` are running using systemd.

**Example:**
```bash
./check_scoutam.py scoutsync
```

### `check_scoutam.py all`

Runs all checks including S3 gateways (scoutgw and versitygw), scoutsync, sequences, and resources.

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

### Resource Monitoring

```bash
# Default flap detection (3 changes in 10 minutes)
./check_scoutam.py resources

# Custom flap detection
./check_scoutam.py resources --flap-count 5 --flap-window 30m
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
[INFO] Starting sequence restart check
[INFO] Parsed scheduler name from samcli system: s82.vpn.versity.com
[INFO] Current hostname: s81.vpn.versity.com
[INFO] Comparing short names: scheduler='s82' current='s81'
[INFO] Is this the scheduler node? False
[INFO] Not scheduler node (scheduler is s82.vpn.versity.com), skipping check
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

Ensure the state directory exists and is writable by the monitoring user:

```bash
# Create directory
sudo mkdir -p /var/lib/nagios

# Set ownership (for nagios)
sudo chown nagios:nagios /var/lib/nagios

# Or for zabbix
sudo chown zabbix:zabbix /var/lib/nagios

# Set permissions
sudo chmod 750 /var/lib/nagios
```

Alternatively, use `--state-dir` to specify a different location:
```bash
./check_scoutam.py --state-dir /var/lib/zabbix resources
```

#### Command timeouts

Default timeout is 30 seconds per command. Override with `--check-timeout`:

```bash
# Increase timeout to 60 seconds
./check_scoutam.py --check-timeout 60 all
```

If commands hang, check:
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
cat /var/lib/nagios/resources.json | python3 -m json.tool
```

To manually reset state (as nagios user):
```bash
rm -f /var/lib/nagios/check_scoutam_sequences.json
rm -f /var/lib/nagios/resources.json
```

## Zabbix Integration

The check script supports Zabbix agent integration with two output modes:

1. **Text mode (`--zabbix`)**: Human-readable output, always exits 0
2. **JSON mode (`--json`)**: Structured JSON output for dependent items with JSONPath preprocessing

### Why Special Flags?

Zabbix agent treats non-zero exit codes as "unsupported item" errors, which breaks triggers silently. The `--zabbix` and `--json` flags ensure the script always exits 0, with status information embedded in the output.

### Zabbix Agent Configuration

Create `/etc/zabbix/zabbix_agentd.d/scoutam.conf`:

```bash
# ScoutAM Individual Checks (Text Mode)
UserParameter=scoutam.mount,sudo /usr/local/sbin/check_scoutam.py --zabbix mount
UserParameter=scoutam.service,sudo /usr/local/sbin/check_scoutam.py --zabbix service
UserParameter=scoutam.scheduler,sudo /usr/local/sbin/check_scoutam.py --zabbix scheduler
UserParameter=scoutam.sequences,sudo /usr/local/sbin/check_scoutam.py --zabbix sequences
UserParameter=scoutam.resources,sudo /usr/local/sbin/check_scoutam.py --zabbix resources
UserParameter=scoutam.gateway,sudo /usr/local/sbin/check_scoutam.py --zabbix gateway
UserParameter=scoutam.versitygw,sudo /usr/local/sbin/check_scoutam.py --zabbix versitygw
UserParameter=scoutam.scoutsync,sudo /usr/local/sbin/check_scoutam.py --zabbix scoutsync

# Combined Checks (Text Mode)
UserParameter=scoutam.scoutam,sudo /usr/local/sbin/check_scoutam.py --zabbix scoutam
UserParameter=scoutam.all,sudo /usr/local/sbin/check_scoutam.py --zabbix all

# JSON Mode for Dependent Items (recommended for complex monitoring)
UserParameter=scoutam.json,sudo /usr/local/sbin/check_scoutam.py --json all
```

Restart the Zabbix agent:
```bash
sudo systemctl restart zabbix-agent
```

### Sudoers Configuration

Add to `/etc/sudoers.d/zabbix`:
```bash
zabbix ALL=(ALL) NOPASSWD: /usr/local/sbin/check_scoutam.py
```

### Testing

```bash
# Test text mode
sudo -u zabbix zabbix_agentd -t scoutam.all

# Test JSON mode
sudo -u zabbix zabbix_agentd -t scoutam.json
```

### JSON Output Format

The `--json` flag produces structured output:

```json
{
  "status": 0,
  "status_text": "OK",
  "checks": [
    {
      "name": "mounts",
      "status": 0,
      "status_text": "OK",
      "messages": ["OK: ScoutFS filesystem /mnt/scoutfs data used 1.23 TiB..."]
    },
    {
      "name": "service",
      "status": 0,
      "status_text": "OK",
      "messages": ["OK: ScoutAM service is running"]
    }
  ]
}
```

### Zabbix Server Configuration

#### Text Mode Items

Create items with type "Zabbix agent" and value type "Text":

| Key | Description |
|-----|-------------|
| `scoutam.mount` | Mount status |
| `scoutam.service` | Service status |
| `scoutam.scheduler` | Scheduler status |
| `scoutam.sequences` | Sequences status |
| `scoutam.resources` | Resources status |

**Trigger examples:**
```
# Critical if output contains "CRITICAL"
{host:scoutam.mount.regexp(CRITICAL)}=1

# Warning if output contains "WARN"
{host:scoutam.mount.regexp(WARN)}=1
```

#### JSON Mode with Dependent Items (Recommended)

1. **Create master item:**
   - Key: `scoutam.json`
   - Type: Zabbix agent
   - Type of information: Text

2. **Create dependent items with JSONPath preprocessing:**

| Item | JSONPath |
|------|----------|
| Overall Status | `$.status` |
| Mounts Status | `$.checks[?(@.name=='mounts')].status.first()` |
| Service Status | `$.checks[?(@.name=='service')].status.first()` |
| Scheduler Status | `$.checks[?(@.name=='scheduler')].status.first()` |
| Resources Status | `$.checks[?(@.name=='resources')].status.first()` |

3. **Create triggers:**
```
# Overall Critical
Expression: last(/host/scoutam.status)=2
Severity: High

# Overall Warning
Expression: last(/host/scoutam.status)=1
Severity: Warning
```

## Technical Details

### File Locking

The script uses `fcntl` file locking to prevent race conditions when multiple NRPE checks run simultaneously:
- **Shared lock (LOCK_SH):** Used when reading state file
- **Exclusive lock (LOCK_EX):** Used when writing state file
- Locks are automatically released when file handles close

### Timeout Handling

All subprocess commands have a configurable timeout (default 30 seconds):
- Commands that exceed timeout return error with timeout message
- Override with `--check-timeout SEC`
- Use `--debug` to see timeout errors in detail

### Security

- **State file permissions:** 0640 (owner read/write, group read)
- **State directory permissions:** 0750 (not world-accessible)
- **No shell execution:** All commands use list form to prevent injection
- **Absolute paths:** All executables referenced by absolute path

### Performance

- **Concurrent safe:** File locking allows multiple checks to run safely
- **State caching:** Sequence/resource state persisted between runs for accurate duration tracking
- **Minimal overhead:** Only scheduler node performs sequence and resource checks

## Exit Codes

The script follows standard NRPE exit codes:
- **0 (OK):** Check passed successfully
- **1 (WARNING):** Warning threshold exceeded or non-critical issue
- **2 (CRITICAL):** Critical threshold exceeded or service failure

When `--zabbix` or `--json` is used, the script always exits 0 (status embedded in output).
