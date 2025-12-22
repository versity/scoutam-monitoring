# ScoutAM Monitoring

Repository for monitoring check scripts and configuration templates for ScoutAM.

Supports both Nagios/NRPE and Zabbix agent integration.

## Requirements

- **Python 3.7+** (for `check_scoutam.py`)

## Structure

### ScoutAM Version 3.X

For ScoutAM versions 3.X use the `check_scoutam.py` script located in the `ScoutAM 3.X` directory in the repository.

### ScoutAM Version 2.X

For ScoutAM versions 2.X use the `check_scoutam.sh` script located in the `ScoutAM 2.X` directory in the repository.

## check_scoutam.py Usage

```
check_scoutam.py [options] operation [warn_thresh] [crit_thresh]
```

### Operations

| Operation | Description |
|-----------|-------------|
| `mount` | Check if ScoutFS filesystem is mounted and usage thresholds |
| `service` | Check if the ScoutAM service is running |
| `scheduler` | Check if the ScoutAM scheduler is running and not idled |
| `sequences` | Check if Arfind/Stfind restart are blocked |
| `resources` | Check the state of resources (tape drives, media changers) |
| `gateway` | Check if ScoutGW S3 gateway services are running |
| `versitygw` | Check if VersityGW S3 gateway services are running |
| `scoutsync` | Check if ScoutSync services are running |
| `scoutam` | Combined check: mount, service, and scheduler |
| `all` | Run all checks |

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `-m, --mount MOUNT` | Specific mount point to check | All mounts |
| `-p, --passfail` | Exit with status code only (no output) | False |
| `-v, --verbose` | Enable verbose output (INFO level logging) | False |
| `-d, --debug` | Enable debug output (DEBUG level logging) | False |
| `--arfind-warn SEC` | Arfind blocked warning threshold (seconds) | 300 |
| `--arfind-crit SEC` | Arfind blocked critical threshold (seconds) | 600 |
| `--stfind-warn SEC` | Stfind blocked warning threshold (seconds) | 300 |
| `--stfind-crit SEC` | Stfind blocked critical threshold (seconds) | 600 |
| `--flap-count N` | State changes to trigger flap warning | 3 |
| `--flap-window TIME` | Time window for flap detection (e.g., 10m, 1h) | 10m |
| `--state-dir DIR` | Directory for state files | /var/lib/nagios |
| `--check-timeout SEC` | Timeout per command in seconds | 30 |
| `--zabbix` | Zabbix mode: always exit 0, status in text | False |
| `--json` | Output JSON format for Zabbix dependent items | False |

### Exit Codes (NRPE Mode)

| Code | Status |
|------|--------|
| 0 | OK |
| 1 | WARNING |
| 2 | CRITICAL |

### Examples

```bash
# Check all ScoutAM components
check_scoutam.py all

# Check mount with custom thresholds (warn at 80%, critical at 95%)
check_scoutam.py mount 95 80

# Check specific mount point
check_scoutam.py -m /mnt/scoutfs mount

# Check sequences with custom thresholds
check_scoutam.py --arfind-warn 600 --arfind-crit 1200 sequences

# Check resources with flap detection
check_scoutam.py --flap-count 5 --flap-window 30m resources

# Verbose output for troubleshooting
check_scoutam.py -v all

# JSON output for Zabbix
check_scoutam.py --json all
```

---

## Nagios/NRPE Configuration

This section details the ScoutAM specific configuration items for Nagios and NRPE.

### Example `scoutam.cfg`

The following `scoutam.cfg` file can be placed in your Nagios `objects` configuration directory:

```
###############################################################################
#
# HOST DEFINITION
#
###############################################################################

define host {
    use                     linux-server
    host_name               s1
    address                 172.21.0.160
}

define host {
    use                     linux-server
    host_name               s2
    address                 172.21.0.161
}

define host {
    use                     linux-server
    host_name               s3
    address                 172.21.0.162
}

###############################################################################
#
# HOST GROUP DEFINITION
#
###############################################################################

define hostgroup {
    hostgroup_name          scoutam01
    alias                   ScoutAM Cluster
    members                 s1,s2,s3
}

define command {
    command_name            check_nrpe
    command_line            $USER1$/check_nrpe -H $HOSTADDRESS$ -c $ARG1$
}

###############################################################################
#
# SERVICE DEFINITIONS
#
###############################################################################

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutAM Service
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_service
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutFS Mount
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_scoutfs
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutAM Scheduler
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_scheduler
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutAM Sequences
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_sequences
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutAM Resources
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_resources
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutAM S3 Gateway
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_gateway
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     VersityGW S3 Gateway
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_versitygw
}

define service {
    use                     generic-service
    hostgroup_name          scoutam01
    service_description     ScoutSync
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_scoutsync
}

define servicegroup {
    servicegroup_name       ScoutAM
    alias                   ScoutAM Services
}
```

### Example `nrpe.cfg`

Add these entries to your `nrpe.cfg` file (or create `/etc/nrpe.d/scoutam.cfg`):

```bash
# ScoutAM checks
command[check_scoutam_scoutfs]=sudo /usr/local/sbin/check_scoutam.py mount
command[check_scoutam_service]=sudo /usr/local/sbin/check_scoutam.py service
command[check_scoutam_scheduler]=sudo /usr/local/sbin/check_scoutam.py scheduler
command[check_scoutam_sequences]=sudo /usr/local/sbin/check_scoutam.py sequences
command[check_scoutam_resources]=sudo /usr/local/sbin/check_scoutam.py resources
command[check_scoutam_gateway]=sudo /usr/local/sbin/check_scoutam.py gateway
command[check_scoutam_versitygw]=sudo /usr/local/sbin/check_scoutam.py versitygw
command[check_scoutam_scoutsync]=sudo /usr/local/sbin/check_scoutam.py scoutsync
command[check_scoutam]=sudo /usr/local/sbin/check_scoutam.py scoutam
command[check_scoutam_all]=sudo /usr/local/sbin/check_scoutam.py all
```

---

## Zabbix Integration

The check script supports Zabbix agent integration with two output modes:

1. **Text mode (`--zabbix`)**: Human-readable output, always exits 0
2. **JSON mode (`--json`)**: Structured JSON output for dependent items with JSONPath preprocessing

### Why Special Flags?

Zabbix agent treats non-zero exit codes as "unsupported item" errors, which breaks triggers silently. The `--zabbix` and `--json` flags ensure the script always exits 0, with status information embedded in the output.

### Zabbix Agent Configuration

Create `/etc/zabbix/zabbix_agentd.d/scoutam.conf` (or add to your main config):

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

Restart the Zabbix agent after adding the configuration:

```bash
sudo systemctl restart zabbix-agent
# or for zabbix-agent2:
sudo systemctl restart zabbix-agent2
```

### Sudoers Configuration

The check script requires root privileges to access ScoutAM commands. Add to `/etc/sudoers.d/zabbix`:

```bash
# Allow zabbix user to run check_scoutam.py without password
zabbix ALL=(ALL) NOPASSWD: /usr/local/sbin/check_scoutam.py
```

### Testing the Agent Configuration

Test from the ScoutAM node:

```bash
# Test text mode
sudo -u zabbix zabbix_agentd -t scoutam.all

# Test JSON mode
sudo -u zabbix zabbix_agentd -t scoutam.json

# For zabbix-agent2:
sudo -u zabbix zabbix_agent2 -t scoutam.all
```

### Zabbix Server/Frontend Configuration

#### Option 1: Simple Text Items

Create items for each check using the text mode UserParameters:

| Item Name | Key | Type | Value Type |
|-----------|-----|------|------------|
| ScoutAM Mount Status | `scoutam.mount` | Zabbix agent | Text |
| ScoutAM Service Status | `scoutam.service` | Zabbix agent | Text |
| ScoutAM Scheduler Status | `scoutam.scheduler` | Zabbix agent | Text |
| ScoutAM Sequences Status | `scoutam.sequences` | Zabbix agent | Text |
| ScoutAM Resources Status | `scoutam.resources` | Zabbix agent | Text |
| ScoutGW Status | `scoutam.gateway` | Zabbix agent | Text |
| VersityGW Status | `scoutam.versitygw` | Zabbix agent | Text |
| ScoutSync Status | `scoutam.scoutsync` | Zabbix agent | Text |

**Trigger Examples (Text Mode):**

```
# Critical if output contains "CRITICAL"
{host:scoutam.mount.regexp(CRITICAL)}=1

# Warning if output contains "WARN"
{host:scoutam.mount.regexp(WARN)}=1

# OK if output starts with "OK"
{host:scoutam.service.regexp(^OK)}=0
```

#### Option 2: JSON Mode with Dependent Items (Recommended)

This approach uses a single master item that collects JSON data, with dependent items that extract specific values using JSONPath preprocessing.

**Step 1: Create Master Item**

| Setting | Value |
|---------|-------|
| Name | ScoutAM JSON Data |
| Key | `scoutam.json` |
| Type | Zabbix agent |
| Type of information | Text |
| Update interval | 1m |

**Step 2: Create Dependent Items**

Create dependent items that reference the master item and use JSONPath preprocessing:

| Item Name | Key | Preprocessing | JSONPath |
|-----------|-----|---------------|----------|
| ScoutAM Overall Status | `scoutam.status` | JSONPath | `$.status` |
| ScoutAM Overall Status Text | `scoutam.status.text` | JSONPath | `$.status_text` |
| ScoutAM Mounts Status | `scoutam.mounts.status` | JSONPath | `$.checks[?(@.name=='mounts')].status.first()` |
| ScoutAM Service Status | `scoutam.service.status` | JSONPath | `$.checks[?(@.name=='service')].status.first()` |
| ScoutAM Scheduler Status | `scoutam.scheduler.status` | JSONPath | `$.checks[?(@.name=='scheduler')].status.first()` |
| ScoutAM Sequences Status | `scoutam.sequences.status` | JSONPath | `$.checks[?(@.name=='sequences')].status.first()` |
| ScoutAM Resources Status | `scoutam.resources.status` | JSONPath | `$.checks[?(@.name=='resources')].status.first()` |
| ScoutGW Status | `scoutam.gateway.status` | JSONPath | `$.checks[?(@.name=='gateway')].status.first()` |
| VersityGW Status | `scoutam.versitygw.status` | JSONPath | `$.checks[?(@.name=='versitygw')].status.first()` |
| ScoutSync Status | `scoutam.scoutsync.status` | JSONPath | `$.checks[?(@.name=='scoutsync')].status.first()` |

**Step 3: Create Triggers**

With numeric status values (0=OK, 1=WARNING, 2=CRITICAL), create triggers:

```
# Overall Critical
Name: ScoutAM Critical
Expression: last(/host/scoutam.status)=2
Severity: High

# Overall Warning
Name: ScoutAM Warning
Expression: last(/host/scoutam.status)=1
Severity: Warning

# Individual check critical (example for mounts)
Name: ScoutAM Mounts Critical
Expression: last(/host/scoutam.mounts.status)=2
Severity: High

# Individual check warning
Name: ScoutAM Mounts Warning
Expression: last(/host/scoutam.mounts.status)=1
Severity: Warning
```

### JSON Output Format

The `--json` flag produces output in this format:

```json
{
  "status": 0,
  "status_text": "OK",
  "checks": [
    {
      "name": "mounts",
      "status": 0,
      "status_text": "OK",
      "messages": [
        "OK: ScoutFS filesystem /mnt/scoutfs data used 1.23 TiB, free: 4.56 TiB, high watermark: 7.89 TiB"
      ]
    },
    {
      "name": "service",
      "status": 0,
      "status_text": "OK",
      "messages": [
        "OK: ScoutAM service is running"
      ]
    }
  ]
}
```

### Zabbix Template Export

You can import the following template XML into Zabbix (Configuration > Templates > Import):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<zabbix_export>
    <version>6.0</version>
    <templates>
        <template>
            <uuid>scoutam-monitoring-template</uuid>
            <template>Template ScoutAM</template>
            <name>Template ScoutAM</name>
            <groups>
                <group>
                    <name>Templates</name>
                </group>
            </groups>
            <items>
                <item>
                    <uuid>scoutam-json-master</uuid>
                    <name>ScoutAM JSON Data</name>
                    <key>scoutam.json</key>
                    <delay>1m</delay>
                    <value_type>TEXT</value_type>
                </item>
                <item>
                    <uuid>scoutam-overall-status</uuid>
                    <name>ScoutAM Overall Status</name>
                    <type>DEPENDENT</type>
                    <key>scoutam.status</key>
                    <value_type>UNSIGNED</value_type>
                    <master_item>
                        <key>scoutam.json</key>
                    </master_item>
                    <preprocessing>
                        <step>
                            <type>JSONPATH</type>
                            <parameters>
                                <parameter>$.status</parameter>
                            </parameters>
                        </step>
                    </preprocessing>
                </item>
            </items>
            <triggers>
                <trigger>
                    <uuid>scoutam-critical-trigger</uuid>
                    <expression>last(/Template ScoutAM/scoutam.status)=2</expression>
                    <name>ScoutAM Critical</name>
                    <priority>HIGH</priority>
                </trigger>
                <trigger>
                    <uuid>scoutam-warning-trigger</uuid>
                    <expression>last(/Template ScoutAM/scoutam.status)=1</expression>
                    <name>ScoutAM Warning</name>
                    <priority>WARNING</priority>
                </trigger>
            </triggers>
        </template>
    </templates>
</zabbix_export>
```

---

## State Files

The script maintains state files for tracking sequence blocks and resource flapping:

| File | Purpose |
|------|---------|
| `<state-dir>/check_scoutam_sequences.json` | Tracks Arfind/Stfind block duration |
| `<state-dir>/resources.json` | Tracks resource state history for flap detection |

Default state directory: `/var/lib/nagios`

Override with `--state-dir`:

```bash
check_scoutam.py --state-dir /var/lib/zabbix all
```

Ensure the monitoring user (nagios, zabbix) has write access to the state directory.

---

## Troubleshooting

### Enable Debug Output

```bash
# Verbose mode (INFO level)
check_scoutam.py -v all

# Debug mode (DEBUG level - includes command output)
check_scoutam.py -d all
```

### Common Issues

**"CRITICAL: ScoutFS is not installed or missing binaries"**
- Ensure ScoutFS is installed and `/usr/sbin/scoutfs` exists

**"CRITICAL: ScoutAM is not installed or missing binaries"**
- Ensure ScoutAM is installed and `/usr/sbin/scoutam-monitor` exists

**"WARN: Could not determine scheduler node"**
- The `samcli system` command failed - check ScoutAM service status

**Zabbix shows "ZBX_NOTSUPPORTED"**
- Ensure the `--zabbix` or `--json` flag is used
- Check sudoers configuration
- Verify the script path is correct

**State file permission errors**
- Ensure the state directory exists and is writable by the monitoring user
- Use `--state-dir` to specify an alternative directory
