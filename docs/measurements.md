Installation timers are collected for every cluster installation.

Timers could be global or platform-subplatform related depending on the tools or procedures used to obtain them. We can define following timers collected at this moment:


## Preflight Checks

| | |
|:-------------:|:------:|
| Platform    | ROSA |
| Subplatform | ALL  |

Just after the cluster installation is launched on ROSA, it transitions for diferent stages (Pending, Waiting, Installing & Ready).

We measure (during 60 minutes) the time on each stage.

## Cluster Access

| | |
|:-------------:|:------:|
| Platform    | ROSA |
| Subplatform | ALL  |

Once the cluster is ready, we create a user on it, login on the cluster, and perform an admin operation, collecting following timers:


| Timer | Operation |
|:-------------:|------|
| cluster_admin_create | Time to execute `rosa create admin` |
| cluster_admin_login | Time to execute `oc login` using the admin user |
| cluster_oc_adm | Time to execute `oc adm top images` on the cluster

## Namespace Waiting

| | |
|:-------------:|:------:|
| Platform    | ROSA |
| Subplatform | Hypershift  |

On hypershift, namespaces are created on Management & Service Clusters for each Hosted Cluster, we measure the time needed for them to be created

| Timer | Operation |
|:-------------:|------|
| sc_namespace_timing | Time for namespace on Service Cluster to be created |
| mc_namespace_timing | Time for namespace on Management Cluster to be created |

## Workers Ready

| | |
|:-------------:|:------:|
| Platform    | ROSA |
| Subplatform | Hypershift  |

Cluster is considered installed when the installation process is completed and cluster is ready. We also measure the time required for all the workers required by the installation are created and ready on the OCP side

| Timer | Operation |
|:-------------:|------|
| workers_ready | All workers from default machinepool are Ready |
| extra_pool_workers_ready | If extra machinepool is created, time for workers on it to be Ready |

## Cleanup

| | |
|:-------------:|:------:|
| Platform    | ROSA |
| Subplatform | Hypershift  |

Times related to destroy clusters

| Timer | Operation |
|:-------------:|------|
| destroy_duration | Time to execute `rosa delete cluster` |
| destroy_all_duration | Time to execute `rosa delete cluster` + STS resources|
