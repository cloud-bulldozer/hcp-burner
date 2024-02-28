Classes related to Hypershift subplatform

As hypershift is a subplatform or ROSA, select rosa as platform parameter and hypershift as subplatform:
`hcp-burner --platform rosa --subplatform hypershift`

### Arguments

To use the config file, define parameters related to platform under the `[Platform:Rosa:Hypershift]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --create-vpcs             |                   |                                |                                          |
| --clusters-per-vpc        | 1                 |                                | HCP_BURNER_CLUSTERS_PER_VPC              |
| --terraform-retry         | 5                 |                                |                                          |
| --service-cluster         |                   | hypershift_service_cluster     | HCP_BURNER_HYPERSHIFT_SERVICE_CLUSTER    |
| --delete-vpcs             |                   |                                |                                          |
