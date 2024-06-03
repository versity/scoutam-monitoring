# Nagios #

Repository for Nagios NRPE check script(s) and configuration template for ScoutAM.

# NRPE Check Script `check_scoutam.sh` #

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

## `check_scoutam.sh mount [warn_thresh] [crit_thresh]` ##

Will check if the filesystem is mounted then evaluate both the metadata and data capacity as reported by `/sbin/scoutfs df` against the optional warning and critical thresholds.

## `check_scoutam.sh service` ##

Verifies that the `scoutam` service is running using systemd.

## `check_scoutam.sh scheduler` ##

Uses `samcli status` to see if the scheduler is running and reports the IP address of the leader.

### `check_scoutam.sh scoutam` ##

Checks the `mount`, `service`, and `scheduler` checks.

## `check_scoutam.sh gateway` ##

Checks to see if the gateways configured in `/etc/scoutgw.d` are running using systems.

## `check_scoutam.sh all` ##

Will check all services including the gateway.

# Nagios Configuration #

This section details only the ScoutAM specific configuration items for Nagios and NRPE.

## Example `scoutam.cfg` ##

The following `scoutam.cfg` file can be placed in your Nagios `objects` configuration directory with your other Nagios configurations.

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
    service_description     ScoutAM S3 Gateway
    servicegroups           ScoutAM
    check_command           check_nrpe!check_scoutam_gateway
}

define servicegroup {
    servicegroup_name       ScoutAM
    alias                   ScoutAM Services
}
```

## Example `nrpe.cfg` ##

The following are example entries in the `nrpe.cfg` file for the various ScoutAM checks supported by the script:

```
# ScoutAM checks
command[check_scoutam_scoutfs]=sudo /usr/local/sbin/check_scoutam.sh mount
command[check_scoutam_service]=sudo /usr/local/sbin/check_scoutam.sh service
command[check_scoutam_scheduler]=sudo /usr/local/sbin/check_scoutam.sh scheduler
command[check_scoutam_gateway]=sudo /usr/local/sbin/check_scoutam.sh all
command[check_scoutam]=sudo /usr/local/sbin/check_scoutam.sh scoutam
command[check_scoutam_all]=sudo /usr/local/sbin/check_scoutam.sh all
```
