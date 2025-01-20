# Nagios #

Repository for Nagios NRPE check script(s) and configuration template for ScoutAM.

# Structure #

## ScoutAM Version 3.X ##

For ScoutAM versions 3.X use the `check_scoutam.py` script located in the `ScoutAM 3.X` directory in the repository.

## ScoutAM Version 2.X ##

For ScoutAM versions 2.X use the `check_scoutam.sh` script located in the `ScoutAM 2.X` directory in the repository.

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
