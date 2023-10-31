# Terraform Subplatform


Classes related to Terraform subplatform

As terraform is a subplatform or ROSA, select rosa as platform parameter and terraform as subplatform:
`rosa-burner --platform rosa --subplatform terraform`

## Platforms Arguments

To use the config file, define parameters related to platform under the `[Platform:Rosa:Terraform]` section

| Argument                 | Default Value     | Config file variable | Environment Variable           |
|--------------------------|-------------------|----------------------|--------------------------------|
| --terraform-retry         | 5                 |                                |                                          |
