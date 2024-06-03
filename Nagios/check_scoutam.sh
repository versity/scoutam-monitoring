#!/bin/bash
#
# Copyright 2024 Versity Software
#
# check_scoutam.sh - Nagios NRPE check script
#

## NRPE exit codes
NRPE_OK=0
NRPE_WARNING=1
NRPE_CRITICAL=2
NRPE_UNKNOWN=3

## State flags
OK_CHECKS=0
WARN_CHECKS=0
CRITICAL_CHECKS=0
IS_LEADER="false"

## Configuration
PASS_FLAG=0
OPERATION=""

function usage {
    local status=$1

    echo ""
    echo "check_scoutam.sh [--passfail|-p] operation"
    echo ""
    echo "    --passfail|-p   Exit with either a 0 (success), 0 for warning, or 2 for critical"
    echo ""
    echo "The following check operations are available:"
    echo ""
    echo "    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted"
    echo "    service     - check if the ScoutAM service is running"
    echo "    scheduler   - check if the scheduler is running on the leader node"
    echo "    gateway     - check if all the configured ScoutAM S3 gateway services are running"
    echo "    scoutam     - check mount, scoutam, and scheduler"
    echo "    all         - check all including S3 gateways"
    echo ""

    exit "$status"
}

function log_message {
    message="$*"

    [[ "$PASS_FLAG" -eq 1 ]] && return

    echo "$message"
}

function check_mount {
    lwm_regex='Low Watermark:[[:blank:]]+([[:digit:]]+)%'
    hwm_regex='High Watermark:[[:blank:]]+([[:digit:]]+)%'

    while read -r line
    do
        if [[ "$line" =~ $lwm_regex ]]
        then
            default_lwm="${BASH_REMATCH[1]}"
        fi

        if [[ "$line" =~ $hwm_regex ]]
        then
            default_hwm="${BASH_REMATCH[1]}"
        fi
    done < <(/bin/samcli fs stat 2> /dev/null)

    df_warn="${2:-$default_lwm}"
    df_crit="${3:-$default_hwm}"

    grep scoutfs /proc/mounts > /dev/null 2>&1
    if [[ "$?" -ne 0 ]]
    then
        log_message "CRITICAL: ScoutFS is not mounted"
        return "$NRPE_CRITICAL"
    fi

    if [[ "$IS_LEADER" = "false" ]]
    then
        log_message "OK: ScoutFS is mounted"
        return "$NRPE_OK"
    fi

    while read -r line
    do
        if [[ "$line" =~ MetaData ]]
        then
            meta_df=$(echo "$line" | awk ' { print $NF }')
        fi

        if [[ "$line" =~ Data ]]
        then
            data_df=$(echo "$line" | awk ' { print $NF }')
        fi
    done < <(/sbin/scoutfs df -p "$SCOUTFS_MOUNT")

    if [[ "$meta_df" -ge "$df_warn" ]]
    then
        if [[ "$meta_df" -ge "$df_crit" ]]
        then
            log_message "CRITICAL: ScoutFS metadata usage is over critical threshold of ${df_crit}%, metadata ${meta_df}%"
            [[ $PASS_FLAG -eq 1 ]] && return "$NRPE_OK"
            return "$NRPE_CRITICAL"
        else
            log_message "WARNING: ScoutFS metadata usage is over warning threshold of ${df_warn}%, metadata ${meta_df}%"
            [[ $PASS_FLAG -eq 1 ]] && return "$NRPE_OK"
            return "$NRPE_WARNING"
        fi
    fi

    if [[ "$data_df" -ge "$df_warn" ]]
    then
        if [[ "$data_df" -ge "$df_crit" ]]
        then
            log_message "CRITICAL: ScoutFS data usage is over critical threshold of ${df_crit}%, data ${data_df}%"
            [[ $PASS_FLAG -eq 1 ]] && return "$NRPE_OK"
            return "$NRPE_CRITICAL"
        else
            log_message "WARNING: ScoutFS data usage is over warning threshold of ${df_warn}%, data ${data_df}%"
            [[ $PASS_FLAG -eq 1 ]] && return "$NRPE_OK"
            return "$NRPE_WARNING"
        fi
    fi

    log_message "OK: ScoutFS is mounted: metadata ${meta_df}%, data ${data_df}%"
    return "$NRPE_OK"
}

function check_service {
    if [[ $(systemctl status scoutam.service >/dev/null 2>&1;echo $?) -ne 0 ]]
    then
        log_message "CRITICAL: ScoutAM service is offline"
        return "$NRPE_CRITICAL"
    fi

    log_message "OK: ScoutAM service is running"
    return "$NRPE_OK"
}

function check_scheduler {
    local sched_idled=()

    while read -r line
    do
        if [[ "$line" = "SCHEDULER IS IDLED" ]]
        then
            sched_idled+=("SCHEDULER")
        fi

        if [[ "$line" = "ARCHIVING IS IDLED" ]]
        then
            sched_idled+=("ARCHIVING")
        fi

        if [[ "$line" = "STAGING IS IDLED" ]]
        then
            sched_idled+=("STAGING")
        fi
    done < <(samcli scheduler)

    if [[ "${sched_idled[*]}" != "" ]]
    then
        local IFS=","
        log_message "WARNING: ScoutAM scheduler is idled: ${sched_idled[*]}"
        [[ $PASS_FLAG -eq 1 ]] && return "$NRPE_OK"
        return "$NRPE_WARNING"
    fi

    log_message "OK: ScoutAM scheduler is running"
    return "$NRPE_OK"
}

function check_gateway {
    offline_gateways=()
    online_gateways=()
    count=0
    critical=0
    warn=0

    for gwn in /etc/scoutgw.d/*.conf
    do
        [ -e "$gwn" ] || continue

        gwn=${gwn//\/etc\/scoutgw.d\//}
        gwn=${gwn//.conf/}

        ((count++))

        systemctl status scoutgw@"${gwn}" > /dev/null 2>&1
        if [[ "$?" -ne 0 ]]
        then
            ((warn++))
            offline_gateways+=($gwn)
        else
            online_gateways+=($gwn)
        fi
        ((critical++))
    done

    if [[ "$count" -eq 0 ]]
    then
        log_message "OK: No ScoutAM S3 gateways configured"
        return "$NRPE_OK"
    fi

    if [[ "$warn" -eq "$critical" ]] && [[ "$critical" -gt 0 ]]
    then
        local IFS=','
        log_message "CRITICAL: All ScoutAM S3 Gateway services are down: ${offline_gateways[*]}"
        return "$NRPE_CRITICAL"
    fi

    if [[ "$warn" -gt 0 ]]
    then
        log_message "WARNING: $warn of $critical ScoutAM S3 Gateway services are down - offline: ${offline_gateways[*]}, online: ${online_gateways[*]}"
        [[ $PASS_FLAG -eq 1 ]] && return "$NRPE_OK"
        return "$NRPE_WARNING"
    fi

    log_message "OK: All ScoutAM S3 Gateway instances are running: ${online_gateways[*]}"

    IFS="$OLD_IFS"
    return "$NRPE_OK"
}

while [[ $# -gt 0 ]]
do
    case "$1" in
        --help|-h)
            usage 0
            ;;
        --passfail|-p)
            PASS_FLAG=1
            shift
            ;;
        *)
            if [[ -z "$OPERATION" ]]
            then
                OPERATION=$1
                break
            else
                break
            fi
            ;;
    esac
done

OPERATION_ARGS="$*"

## Check for ScoutAM/ScoutFS binaries
if [[ ! -x /sbin/scoutam-monitor || ! -x /bin/samcli || ! -x /sbin/scoutfs ]]
then
    log_message "UNKNOWN: ScoutAM and/or ScoutFS not installed"
    exit "$NRPE_UNKNOWN"
fi

## Capture current config
while read -r line
do
    if [[ "$line" =~ MountPoint ]]
    then
        SCOUTFS_MOUNT=$(echo "$line" | awk -F"\"" '{print $2}')
    fi

    if [[ "$line" =~ LeaderAddress ]]
    then
        LEADER_ADDRESS=$(echo "$line" | awk -F"\"" '{print $2}')
    fi

    if [[ "$line" =~ IsLeader ]]
    then
        IS_LEADER=$(echo "$line" | awk ' { print $3 } ' | sed -e 's/,//')
    fi
done < <(/sbin/scoutam-monitor -print 2> /dev/null)

## Exit if the filesystem isn't mounted
if [[ -z "$SCOUTFS_MOUNT" || -z "$LEADER_ADDRESS" ]]
then
    log_message "CRITICAL: No ScoutFS filesystem mount detected"
    exit "$NRPE_CRITICAL"
fi

case "$OPERATION" in
    mount)
        check_mount "$OPERATION_ARGS"
        exit "$?"
        ;;
    service)
        check_service
        exit "$?"
        ;;
    scheduler)
        check_scheduler
        exit "$?"
        ;;
    gateway)
        check_gateway
        exit "$?"
        ;;
    scoutam | all)
        check_service
        case "$?" in
            "$NRPE_OK")
                ((OK_CHECKS++))
                ;;
            "$NRPE_WARNING")
                ((WARN_CHECKS++))
                ;;
            "$NRPE_CRITICAL")
                ((CRITICAL_CHECKS++))
                ;;
        esac

        check_mount "$OPERATION_ARGS"
        case "$?" in
            "$NRPE_OK")
                ((OK_CHECKS++))
                ;;
            "$NRPE_WARNING")
                ((WARN_CHECKS++))
                ;;
            "$NRPE_CRITICAL")
                ((CRITICAL_CHECKS++))
                ;;
        esac

        check_scheduler
        case "$?" in
            "$NRPE_OK")
                ((OK_CHECKS++))
                ;;
            "$NRPE_WARNING")
                ((WARN_CHECKS++))
                ;;
            "$NRPE_CRITICAL")
                ((CRITICAL_CHECKS++))
                ;;
        esac

        if [[ "$OPERATION" = "all" ]]
        then
            check_gateway
            case "$?" in
                "$NRPE_OK")
                    ((OK_CHECKS++))
                    ;;
                "$NRPE_WARNING")
                    ((WARN_CHECKS++))
                    ;;
                "$NRPE_CRITICAL")
                    ((CRITICAL_CHECKS++))
                    ;;
            esac
        fi

        if [[ "$CRITICAL_CHECKS" -gt 0 ]]
        then
            exit "$NRPE_CRITICAL"
        elif [[ "$WARN_CHECKS" -gt 0 ]]
        then
            exit "$NRPE_WARNING"
        else
            exit "$NRPE_OK"
        fi

        ;;
    *)
        usage "$NRPE_UNKNOWN"
        ;;
esac

exit "$NRPE_OK"
