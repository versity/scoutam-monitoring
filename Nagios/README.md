# scoutam-nagios #

Repository for Nagios NRPE check script(s) and configuration template for ScoutAM.

## check_scoutam.sh ##

The main NRPE check script that will be run on all ScoutAM nodes. The script has multiple checks embedded and will be called multiple times with each individual check.

```
check_scoutam.sh [--passfail|-p] operation

    --passfail|-p   Exit with either a 0 (success), 0 for warning, or 2 for critical

The following check operations are available:

    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted
    service     - check if the ScoutAM service is running
    scheduler   - check if the scheduler is running on the leader node
    gateway     - check if all the configured ScoutAM S3 gateway services are running
    scoutam     - check mount, scoutam, and scheduler
    all         - check all including S3 gateways
```

### `check_scoutam.sh mount [warn_thresh] [crit_thresh]` ###

Will check if the filesystem is mounted then evaluate both the metadata and data capacity as reported by `/sbin/scoutfs df` against the optional warning and critical thresholds.

### `check_scoutam.sh service` ###

Simply verifies that the `scoutam` service is running using systemd.

### `check_scoutam.sh scheduler` ###

Uses `samcli status` to see if the scheduler is running and reports the IP address of the leader.

### `check_scoutam.sh scoutam` ###

Checks the `mount`, `service`, and `scheduler` checks.

### `check_scoutam.sh gateway` ###

Checks to see if the gateways configured in `/etc/scoutgw.d` are running using systems.

### `check_scoutam.sh all` ###

Will check all services including the gateway.
