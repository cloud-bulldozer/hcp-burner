# Rosa Platforms

Classes related to ROSA platform

To use this platform, select rosa as platform parameter:
`rosa-burner --platform rosa`

## Platforms Arguments

To use the config file, define parameters related to platform under the `[Platform:Rosa]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --rosa-env                      | staging           |                              | ROSA_BURNER_ROSA_ENV             |
| --aws-account-file              |                   |                              | ROSA_BURNER_AWS_ACCOUNT_FILE     |
| --aws-profile                   |                   |                              | ROSA_BURNER_AWS_PROFILE          |
| --aws-region                    | us-east-2         |                              | ROSA_BURNER_AWS_REGION           |
| --oidc-config-id                |                   |                              | ROSA_BURNER_OIDC_CONFIG_ID       |
| --common-operator-roles         |                   |                              |                                  |
| --extra-machinepool-name        |                   |                              | ROSA_BURNER_MACHINE_POOL_NAME    |
| --extra-machinepool-machine-type| m5.xlarge         |                              | ROSA_BURNER_MACHINE_POOL_MACHINE_TYPE |
| --extra-machinepool-replicas    | 3                 |                              | ROSA_BURNER_MACHINE_POOL_REPLICAS |
| --extra-machinepool-labels      |                   |                              | ROSA_BURNER_MACHINEPOOL_LABELS   |
| --extra-machinepool-taints      |                   |                              | ROSA_BURNER_MACHINEPOOL_TAINTS   |
