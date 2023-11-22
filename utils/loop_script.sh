#!/bin/bash

trap '_delete_clusters ${EXECUTION_SEED}' SIGINT SIGTERM

### EDIT VARIABLES
LOG_FILE="${LOG_FILE:-/tmp/install_loop.log}"
NUMBER_OF_CLUSTERS="${NUMBER_OF_CLUSTERS:-1}"
CLUSTER_NAME_SEED="${CLUSTER_NAME_SEED:-loop}"
AWS_SUBNETS="${AWS_SUBNETS:-subnet-xxxxxxx,subnet-xxxxxx,subnet-xxxxxx,subnet-xxxxxx,subnet-xxxxxx,subnet-xxxxxx}"
PROVISION_SHARD="${PROVISION_SHARD:-provision_shard_id:xxxxxx}"
OIDC_CONFIG_ID="${OIDC_CONFIG_ID:-xxxxxxxxxxxx}"
OPERATOR_ROLES_PREFIX="${OPERATOR_ROLES_PREFIX:loop-role}"
export AWS_REGION="${AWS_REGION:-us-east-2}"
###

exec 3>&1 1>>${LOG_FILE} 2>&1

log() {
  message=$1
  timestamp=$(date +%Y-%m-%d\ %H:%M:%S)
  echo -e "$timestamp\t$message"
}

_delete_clusters() {
  log "INFO: Captured Control-C key, deleting all clusters with ${1} seed" | tee /dev/fd/3
  for CLUSTER in $(rosa list clusters | grep $1 | awk '{print $1}'); do
    log "INFO: Deleting cluster ${CLUSTER}" | tee /dev/fd/3
    _delete_cluster "${CLUSTER}"
    log "INFO: Cluster ${CLUSTER} deleted" | tee /dev/fd/3
  done
  exit 0
}

_create_cluster() {
  log "INFO: Creating cluster $1" | tee /dev/fd/3
  # Timeout of 15 minutes for creating the cluster (3 times of normal execution)
  timeout --foreground -k 900 900 rosa create cluster -c $1 --replicas 3 --hosted-cp --sts --mode auto -y --watch --multi-az --subnet-ids $2 --properties $3 --oidc-config-id ${OIDC_CONFIG_ID} --operator-roles-prefix ${OPERATOR_ROLES_PREFIX} --compute-machine-type m5.xlarge --version 4.14.2 || (log "ERROR: Failed to create cluster $1 after 15 minutes" && return 1)
  log "INFO: Cluster $1 created" 3>&1 1>>${LOG_FILE} | tee /dev/fd/3
}

_delete_cluster(){
  # Timeout of 60 minutes for cleaning the cluster (3 times of normal execution)
  timeout --foreground -k 3600 3600 rosa delete cluster -c $1 -y --watch|| (log "ERROR: Failed to delete cluster $1 after 30 minutes" && return 1)
#  # Timeout of 5 minutes for Roles and OIDC
#  timeout --foreground -k 300 300 rosa delete operator-roles -c $1 -m auto -y || (log "ERROR: Failed to delete operator roles of cluster $1 after 5 minutes" && return 1)
#  timeout --foreground -k 300 300 rosa delete oidc-provider -c $1 -m auto -y || (log "ERROR: Failed to delete OIDC providers of cluster $1 after 5 minutes" && return 1)
}

CLUSTER_INDEX=0
EXECUTION_SEED="${CLUSTER_NAME_SEED}"-$(tr -dc 'a-z' < /dev/urandom | fold -w 3 | head -n 1)
while true; do
  CLUSTERS_CREATED_LIST=($(rosa list clusters | grep "${EXECUTION_SEED}" | awk '{print $2}'))
  CLUSTERS_CREATED_TOTAL=$(rosa list clusters | grep "${EXECUTION_SEED}" | wc -l)
  if [ "${CLUSTERS_CREATED_TOTAL}" -lt  "${NUMBER_OF_CLUSTERS}" ] ; then
    log "INFO: Clusters created (${CLUSTERS_CREATED_TOTAL}) under threshold (${NUMBER_OF_CLUSTERS}), creating a new one" | tee /dev/fd/3
    ((CLUSTER_INDEX+=1))
    _create_cluster "${EXECUTION_SEED}-$(printf "%0*d\n" 4 ${CLUSTER_INDEX})" "${AWS_SUBNETS}" "${PROVISION_SHARD}"
    log "INFO: Waiting 60 seconds for the next check" | tee /dev/fd/3
    sleep 60
  else
    log "INFO: Clusters created (${CLUSTERS_CREATED_TOTAL}) matching the threshold (${NUMBER_OF_CLUSTERS}), waiting 60 seconds to delete one of them" | tee /dev/fd/3
    sleep 60
    RANDOM_CLUSTER_TO_DELETE="${CLUSTERS_CREATED_LIST[$((RANDOM % ${#CLUSTERS_CREATED_LIST[@]}))]}"
    log "INFO: Selected cluster ${RANDOM_CLUSTER_TO_DELETE} for deletion" | tee /dev/fd/3
    _delete_cluster "${RANDOM_CLUSTER_TO_DELETE}"
    log "INFO: Waiting 60 seconds for the next check" | tee /dev/fd/3
    sleep 60
  fi
done