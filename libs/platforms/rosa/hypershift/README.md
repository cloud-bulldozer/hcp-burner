# Hypershift Platform


Classes related to Hypershift platform

As hypershift is a subplatform or ROSA, select rosa as platform parameter and hypershift as subplatform:
`rosa-burner --platform rosa --subplatform hypershift`

## Platforms Arguments

To use the config file, define parameters related to platform under the `[Platform:Rosa:Hypershift]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --create-vpcs             |                   |                                |                                          |
| --clusters-per-vpc        | 1                 |                                | ROSA_BURNER_CLUSTERS_PER_VPC              |
| --terraform-retry         | 5                 |                                |                                          |
| --service-cluster         |                   | hypershift_service_cluster     | ROSA_BURNER_HYPERSHIFT_SERVICE_CLUSTER    |
