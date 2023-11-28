The Tests Workflow, defined in the `tests.yml` file, has one job: **test-rosa** which includes a matrix on each subplatforms, **hypershift** and **terraform**.

Before installing the clusters, the automation prepares the environment with following tasks:

- Checkout code
- Download tools (ocm, rosa, terraform and aws cli)
- Create AWS account file
- Install python requirements with pip

#### Test Hypershift

It deploys one Hosted Cluster on the Service Cluster assigned to Perf&Scale

#### Test Terraform

It deploys one Rosa Cluster using the terraform ocm provider on Stage Environment
