#!/usr/bin/env bash
# Sample Runner script for hcp-burner
# Copy and fill in empty environment variables

ts="$(date -u +%Y%m%d)"
external_log_file_ts="$(date -u +%Y%m%d-%H%M%S)"

# Adjust for each new run
iteration=${ts}-run1

# Logging level and working directory
export HCP_BURNER_PATH=/root/rosa/hcpb-${iteration}
export HCP_BURNER_LOG_LEVEL=INFO
#export HCP_BURNER_LOG_LEVEL=DEBUG

# Cluster name prefix
export HCP_BURNER_STATIC_CLUSTER_NAME=p4-hcpb0

export HCP_BURNER_SUBPLATFORM=hypershift

# Rate related arguments
export HCP_BURNER_CLUSTER_COUNT=2
export HCP_BURNER_BATCH_SIZE=1
# Seconds
export HCP_BURNER_DELAY_BETWEEN_BATCH=60

# Seconds
export HCP_BURNER_DELAY_BETWEEN_CLEANUP=15

export HCP_BURNER_WORKERS=3
# Minutes
export HCP_BURNER_WORKERS_WAIT_TIME=60

export HCP_BURNER_HYPERSHIFT_SERVICE_CLUSTER=
export HCP_BURNER_CLUSTERS_PER_VPC=5
#export HCP_BURNER_WILDCARD_OPTIONS='--tags TicketId:XXXX'
export HCP_BURNER_WILDCARD_OPTIONS='--compute-machine-type m5.2xlarge --tags TicketId:XXXX'

export HCP_BURNER_AWS_REGION=us-east-2
export AWS_SECRET_ACCESS_KEY=
export AWS_ACCESS_KEY_ID=
export AWS_REGION=us-east-2

export HCP_BURNER_OCM_URL=
export HCP_BURNER_OCM_TOKEN=

# Workload vars
export HCP_BURNER_WORKLOAD_REPO=https://github.com/cloud-bulldozer/e2e-benchmarking.git
export HCP_BURNER_WORKLOAD_EXECUTOR=/usr/bin/kube-burner
export HCP_BURNER_WORKLOAD_DURATION=15m
export HCP_BURNER_WORKLOAD_JOBS=9

# Elastic search Vars
export HCP_BURNER_ES_URL=
export HCP_BURNER_ES_INDEX=hypershift-wrapper-timers

# Extra vars passed into the workload
export PPROF=false
export QPS=20
export BURST=20
export ES_INDEX=ripsaw-kube-burner
export ES_SERVER=
# Set ES_Server to "" to prevent workload from indexing if concurrency of workloads expected to exceed available memory or to speed up workload phase
#export ES_SERVER=""
export TF_CLI_ARGS_apply="-parallelism=50"

# create, workload, delete the clusters
#export HCP_BURNER_LOG_FILE=/root/rosa/hcpb-${iteration}.log
#python3 hcp-burner.py --platform rosa --wait-for-workers --es-insecure --install-clusters --create-vpcs --enable-workload --cleanup-clusters --delete-vpcs 2>&1 | tee ${external_log_file_ts}-complete.log
# create, no workload, delete
#python3 hcp-burner.py --platform rosa --wait-for-workers --es-insecure --install-clusters --create-vpcs --cleanup-clusters --delete-vpcs 2>&1 | tee ${external_log_file_ts}-install-delete.log

# Each a separate step
#export HCP_BURNER_LOG_FILE=/root/rosa/hcpb-${iteration}-install.log
#python3 hcp-burner.py --platform rosa --wait-for-workers --es-insecure --install-clusters --create-vpcs 2>&1 | tee ${external_log_file_ts}-install.log

#export HCP_BURNER_LOG_FILE=/root/rosa/hcpb-${iteration}-workload.log
#python3 hcp-burner.py --platform rosa --wait-for-workers --es-insecure --enable-workload 2>&1 | tee ${external_log_file_ts}-workload.log

#export HCP_BURNER_LOG_FILE=/root/rosa/hcpb-${iteration}-cleanup.log
#python3 hcp-burner.py --platform rosa --wait-for-workers --es-insecure --cleanup-clusters --delete-vpcs 2>&1 | tee ${external_log_file_ts}-cleanup.log
