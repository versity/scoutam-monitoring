# scoutam-nagios #

Repository for Nagios NRPE check script(s) and configuration template for ScoutAM.

## check_scoutam.sh ##

The main NRPE check script that will be run on all ScoutAM nodes. The script has multiple checks embedded and will be called multiple times with each individual check.

### `check_scoutam.sh mount [warn_thresh] [crit_thresh]` ###

Will check if the filesystem is mounted then evaluate both the metadata and data capacity as reported by `/sbin/scoutfs df` against the optional warning and critical thresholds.

### `check_scoutam.sh scoutam` ###

Simply verifies that the `scoutam` service is running using systemd.

### `check_scoutam.sh scheduler` ###

Uses `samcli status` to see if the scheduler is running and reports the IP address of the leader.

### `check_scoutam.sh gateway` ###

Checks to see if the gateways configured in `/etc/scoutgw.d` are running using systemd.

## To Do ##

* Add separate thresholds for metadata and dataa
* Potentially just have an `all` check or check everything if no argument is given and fail on the first critical item
