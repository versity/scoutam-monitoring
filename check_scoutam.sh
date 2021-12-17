#!/bin/bash

## NRPE exit codes
NRPE_OK=0
NRPE_WARNING=1
NRPE_CRITICAL=2
NRPE_UNKNOWN=3

function usage {
  local status=$1

  echo ""
  echo "check_scoutam.sh operation"
  echo ""
  echo "The following check operations are available:"
  echo ""
  echo "    mount [warn_thresh] [crit_thresh] - check if scoutfs filesystem is mounted"
  echo "    scoutam   - check if the scoutam system is up and running"
  echo "    scheduler - check if the scheduler is running on the leader node"
  echo "    gateway   - check if all the configured ScoutAM S3 gateway services are running"
  echo ""

  exit "$status"
}

OPERATION=$1
if [[ -z "$OPERATION" ]]
then
  usage $NRPE_UNKNOWN
fi

## Capture current config
while read -r line
do
  if [[ $line =~ MountPoint ]]
  then
    SCOUTFS_MOUNT=$(echo "$line" | awk -F"\"" '{print $2}')
  fi

  if [[ $line =~ LeaderAddress ]]
  then
    LEADER_ADDRESS=$(echo "$line" | awk -F"\"" '{print $2}')
  fi

  if [[ $line =~ IsLeader ]]
  then
    if [[ $line =~ false ]]
    then
      IS_LEADER=0
    else
      IS_LEADER=1
    fi
  fi
done < <(/sbin/scoutam-monitor -print)

if [[ $(rpm -qa | grep scoutam >/dev/null 2>&1;echo $?) -ne 0 ]]
then
  echo "UNKNOWN: ScoutAM is not installed"
  exit $NRPE_UNKNOWN
fi

case $OPERATION in 
  mount)
    df_warn="${2:-80}"
    df_crit="${3:-95}"

    grep scoutfs /proc/mounts > /dev/null 2>&1
    if [[ $? -ne 0 ]]
    then
      echo "CRITICAL: ScoutFS is not mounted"
      exit $NRPE_CRITICAL
    fi

    while read -r line
    do
      if [[ $line =~ MetaData ]]
      then
        meta_df=$(echo "$line" | awk ' { print $NF }')
      fi

      if [[ $line =~ Data ]]
      then
        data_df=$(echo "$line" | awk ' { print $NF }')
      fi
    done < <(/sbin/scoutfs df -p "$SCOUTFS_MOUNT")

    if [[ $meta_df -ge $df_warn ]]
    then
      if [[ $meta_df -ge $df_crit ]]
      then
        echo "CRITIAL: ScoutFS metadata usage is over critical threshold of ${df_crit}%, metadata ${meta_df}%"
        exit $NRPE_CRITICAL
      else
        echo "WARNING: ScoutFS metadata usage is over warning threshold of ${df_warn}%, metadata ${meta_df}%"
        exit $NRPE_WARNING
      fi
    fi

    if [[ $data_df -ge $df_warn ]]
    then
      if [[ $data_df -ge $df_crit ]]
      then
        echo "CRITIAL: ScoutFS data usage is over critical threshold of ${df_crit}%, data ${data_df}%"
        exit $NRPE_CRITICAL
      else
        echo "WARNING: ScoutFS data usage is over warning threshold of ${df_warn}%, data ${data_df}%"
        exit $NRPE_WARNING
      fi
    fi

    echo "OK: ScoutFS is mounted: metadata ${meta_df}%, data ${data_df}%"
    exit $NRPE_OK

    ;;

  scoutam)
    if [[ $(systemctl status scoutam.service >/dev/null 2>&1;echo $?) -ne 0 ]]
    then
      echo "CRITICAL: ScoutAM service is offline"
      exit $NRPE_CRITICAL
    fi

    echo "OK: ScoutAM is running"
    exit $NRPE_OK

    ;;

  scheduler)
    samcli status 2>&1 | grep "SCHEDULER IS RUNNING" > /dev/null 2>&1
    if [[ $? -ne 0 ]]
    then
      echo "CRITICAL: ScoutAM scheduler is not running"
      exit $NRPE_CRITICAL
    fi

    echo "OK: ScoutAM scheduler is running at ${LEADER_ADDRESS}"
    exit $NRPE_OK

    ;;

  gateway)
    critical=0
    warn=0

    for gwn in /etc/scoutgw.d/*
    do
      gwn=${gwn//\/etc\/scoutgw.d\//}
      gwn=${gwn//.conf/}

      systemctl status scoutgw@"${gwn}" > /dev/null 2>&1
      if [[ $? -ne 0 ]]
      then
        warn=$((warn+1))
      fi
      critical=$((critical+1))
    done

    if [[ $warn -eq $critical ]] && [[ $critical -gt 0 ]]
    then
      echo "CRITICAL: All ScoutAM S3 Gateway services are down"
      exit $NRPE_CRITICAL
    fi

    if [[ $warn -gt 0 ]]
    then
      echo "WARNING: $warn number of ScoutAM S3 Gateway services are down"
      exit $NRPE_WARNING
    fi

    echo "OK: All ScoutAM S3 Gateway instances are running"
    exit $NRPE_OK

    ;;
  *)
    usage $NRPE_UNKNOWN
esac
