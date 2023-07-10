# What is rosa-burner

Rosa-burner is a wrapper to automate create-use-destroy of OCP clusters in the different managed platforms.

## How works rosa-burner

Following helpers are available for every platform:
- Schedulers:
  - Install:
  - Execute Workloads
  - Cleanup

Every platform needs to overload following functions to be integrated in the cluster:
- Initialize
- Create Cluster
- Delete Cluster
- Platform Cleanup
- Watcher

## Available Platforms
Following platforms are available at this moment:
- Rosa
  - Hypershift

# Arguments, parameters and configuration options

Almost all the parameters can be defined in three ways:
- Wrapper parameters:
`rosa-burner.py --cluster-count 100`
- Environment Variable:
`ROSA_BURNER_CLUSTER_COUNT=100 rosa-burner.py`
- Configuration File:
`rosa-burner.py --config-file ./rosa-burner.conf`

**Only parameters --platform and --subplatform must be defined as wrapper arguments, platform is always required but subplatform is optional**

To add any other parameter to the config file, remove `--` from the argument and change `_` to `_`, for example:

**--cluster-name-seed** will be:
```
[Defaults]
cluster_name_seed = test
```

Full version of a config file can be found on **rosa-burner.conf** file

## Preference

All parameters will have following preference when they will be defined in more than one place:

Argument > Environment Variable > Config File

## Common arguments

To use the config file, define common parameters under the `[Defaults]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --config-file            |                   |                      | ROSA_BURNER_CONFIG_FILE        |
| --platform               |                   | platform             | ROSA_BURNER_PLATFORM           |
| --subplatform            |                   | subplatform          | ROSA_BURNER_SUBPLATFORM        |
| --uuid                   |                   |                      | ROSA_BURNER_UUID               |
| --path                   |                   |                      | ROSA_BURNER_PATH               |
| --cluster-name-seed      |                   |                      | ROSA_BURNER_CLUSTER_NAME_SEED  |
| --workers                | 3                 |                      | ROSA_BURNER_WORKERS            |
| --workers-wait-time      | 60                |                      | ROSA_BURNER_WORKERS_WAIT_TIME  |
| --wait-for-workers       |                   |                      |                                |
| --cluster-count          | 1                 |                      | ROSA_BURNER_CLUSTER_COUNT      |
| --delay-between-batch    | 60                |                      | ROSA_BURNER_DELAY_BETWEEN_BATCH|
| --batch-size             | 0                 |                      | ROSA_BURNER_BATCH_SIZE         |
| --watcher-delay          | 60                |                      | ROSA_BURNER_WATCHER_DELAY      |
| --wildcard-options       |                   |                      | ROSA_BURNER_WILDCARD_OPTIONS   |
| --enable-workload        |                   |                      |                                |
| --workload-repo          | https://github.com/cloud-bulldozer/e2e-benchmarking.git | workload_repo | ROSA_BURNER_WORKLOAD_REPO |
| --workload               | cluster-density-ms | workload             | ROSA_BURNER_WORKLOAD           |
| --workload-script        | workloads/kube-burner-ocp-wrapper/run.sh | workload_script | ROSA_BURNER_WORKLOAD_SCRIPT |
| --workload-executor      | /usr/bin/kube-burner | workload_executor | ROSA_BURNER_WORKLOAD_EXECUTOR |
| --workload-duration      | 1h                |                      | ROSA_BURNER_WORKLOAD_DURATION  |
| --workload-jobs          | 10                |                      | ROSA_BURNER_WORKLOAD_JOBS      |
| --cleanup-clusters       |                   |                      |                                |
| --wait-before-cleanup    | 0                 |                      | ROSA_BURNER_WAIT_BEFORE_CLEANUP|
| --delay-between-cleanup  | 0                 |                      | ROSA_BURNER_DELAY_BETWEEN_CLEANUP |

# ElasticSearch arguments

To use the config file, define common parameters under the `[Elasticsearch]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --es-url               |                   |                         | ROSA_BURNER_ES_URL              |
| --es-index             | rosa-burner       |                         | ROSA_BURNER_ES_INDEX            |
| --es-index-retry       | 5                 |                         | ROSA_BURNER_ES_INDEX_RETRY      |
| --es-insecure          |                   |                         |                                 |

# Logging arguments

To use the config file, define common parameters under the `[Logging]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --log-level              | INFO              |                      | ROSA_BURNER_LOG_LEVEL          |
| --log-file               |                   |                      | ROSA_BURNER_LOG_FILE           |
