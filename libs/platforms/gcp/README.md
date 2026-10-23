# GCP-HCP (Google Cloud - Hosted Control Plane)

This document covers the CLI arguments for deploying and managing GCP HyperShift hosted clusters using hcp-burner (`--platform gcp --subplatform hypershiftcli`).

## Prerequisites

- `gcloud` CLI installed
- `hypershift` CLI with GCP IAM/infra support (`hypershift create iam gcp`, `hypershift create infra gcp`, `hypershift create cluster gcp`)
- `oc` / `kubectl` for management-cluster and hosted-cluster operations
- GCP service account credentials JSON with permissions to create IAM, VPC/subnet, Cloud DNS, and related resources
- Management cluster (GKE or OpenShift) kubeconfig with HyperShift operator installed
- Parent Cloud DNS public zone for `--base-domain` (child zone `<cluster>.<base-domain>` is created and NS-delegated per cluster)

## CLI Arguments

### GCP Platform Arguments (Common)

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--gcp-project-id` | `HCP_BURNER_GCP_PROJECT_ID` | *Required* | GCP project ID |
| `--gcp-region` | `HCP_BURNER_GCP_REGION` | `us-central1` | GCP region for cluster infrastructure |
| `--gcp-credentials-file` | `HCP_BURNER_GCP_CREDENTIALS_FILE` | *Required* | Path to GCP service account credentials JSON |

### GCP-HCP Hypershiftcli Arguments

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--mc-kubeconfig` | `HCP_BURNER_GCP_MC_KUBECONFIG` | *Required* | Kubeconfig for the management cluster (MC) |
| `--release-image` | `HCP_BURNER_GCP_RELEASE_IMAGE` | *Required* | OpenShift release image (e.g. `quay.io/openshift-release-dev/ocp-release:4.19.0-x86_64`) |
| `--pull-secret-path` | `HCP_BURNER_GCP_PULL_SECRET_PATH` | *Required* | Path to pull secret file |
| `--base-domain` | `HCP_BURNER_GCP_BASE_DOMAIN` | *Required* | Parent DNS domain (e.g. `gcp.hyp.azure.rhperfscale.org`). A child zone `<cluster-name>.<base-domain>` is always created and NS-delegated |
| `--hc-namespace` | `HCP_BURNER_GCP_HC_NAMESPACE` | `clusters` | HostedCluster namespace on the MC |
| `--feature-set` | `HCP_BURNER_GCP_FEATURE_SET` | `TechPreviewNoUpgrade` | Feature set for the hosted cluster |
| `--endpoint-access` | `HCP_BURNER_GCP_ENDPOINT_ACCESS` | `PublicAndPrivate` | API endpoint access type |
| `--disable-capabilities` | `HCP_BURNER_GCP_DISABLE_CAPABILITIES` | `Console,Ingress` | Comma-separated cluster capabilities to disable |

Worker count and wait behavior use the common hcp-burner flags (`--workers`, `--workers-wait-time`, `--wait-for-workers`).

## Install Flow

For each cluster, hypershiftcli roughly:

1. Creates a child Cloud DNS zone and NS delegation under `--base-domain`
2. Generates SA signing keys / JWKS
3. `hypershift create iam gcp`
4. `hypershift create infra gcp`
5. `hypershift create cluster gcp`
6. Waits for control plane ready, downloads kubeconfig, waits for workers / Completed
7. Writes `metadata_install.json` and (when `--es-url` is set) indexes install timers, then runs the kube-burner `index` workload

Cleanup reverses cluster destroy, infra/IAM cleanup, and DNS teardown.

## Usage Examples

### Basic Cluster Creation

```bash
python hcp-burner.py \
  --platform gcp \
  --subplatform hypershiftcli \
  --gcp-project-id my-gcp-project \
  --gcp-region us-central1 \
  --gcp-credentials-file /path/to/gcp-sa.json \
  --mc-kubeconfig /path/to/mc.kubeconfig \
  --release-image quay.io/openshift-release-dev/ocp-release:4.19.0-x86_64 \
  --pull-secret-path /path/to/pull-secret.json \
  --base-domain gcp.example.com \
  --cluster-name-seed muk \
  --cluster-count 1 \
  --workers 3 \
  --install-clusters
```

### With Install Timers + Metrics Indexing

ARO-style dashboards expect install docs in `hypershift-wrapper-timers`. Pass the same ES settings used for ARO:

```bash
export ES_SERVER='https://user:pass@es-host:443'
export ES_INDEX=ripsaw-kube-burner
export MC_NAME=autopilot-mc   # optional; otherwise derived from MC kubeconfig context

python hcp-burner.py \
  --platform gcp \
  --subplatform hypershiftcli \
  --gcp-project-id my-gcp-project \
  --gcp-region us-central1 \
  --gcp-credentials-file /path/to/gcp-sa.json \
  --mc-kubeconfig /path/to/mc.kubeconfig \
  --release-image quay.io/openshift-release-dev/ocp-release:4.19.0-x86_64 \
  --pull-secret-path /path/to/pull-secret.json \
  --base-domain gcp.example.com \
  --es-url "$ES_SERVER" \
  --es-index hypershift-wrapper-timers \
  --es-insecure \
  --cluster-name-seed muk \
  --cluster-count 2 \
  --workers 3 \
  --install-clusters
```

### Cleanup Clusters

```bash
python hcp-burner.py \
  --platform gcp \
  --subplatform hypershiftcli \
  --gcp-project-id my-gcp-project \
  --gcp-region us-central1 \
  --gcp-credentials-file /path/to/gcp-sa.json \
  --mc-kubeconfig /path/to/mc.kubeconfig \
  --cluster-name-seed muk \
  --cluster-count 2 \
  --cleanup-clusters \
  --uuid <run-uuid>
```

## GCP Credentials File

Standard GCP service account key JSON, for example:

```json
{
  "type": "service_account",
  "project_id": "my-gcp-project",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "hcp-burner@my-gcp-project.iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

hcp-burner activates this account via `gcloud auth activate-service-account` and sets the active project.

## Config File

Platform values can also be set in `hcp-burner.ini`:

```ini
[Platform:Gcp]
gcp_project_id = my-gcp-project
gcp_region = us-central1
gcp_credentials_file = /path/to/gcp-sa.json

[Platform:Gcp:Hypershiftcli]
mc_kubeconfig = /path/to/mc.kubeconfig
release_image = quay.io/openshift-release-dev/ocp-release:4.19.0-x86_64
pull_secret_path = /path/to/pull-secret.json
base_domain = gcp.example.com
hc_namespace = clusters
```

## Install Metadata / ES Notes

- Local timings are written to `<path>/<cluster>/metadata_install.json`.
- Top-level `status` for successful installs is `installed` (ARO parity); HostedCluster Completed state is under `metadata.status`.
- `install_duration` / `cluster_ready_time` are measured to control-plane ready (not just CLI create return).
- `mgmt_cluster_name` comes from `MC_NAME` or the current GKE context in `--mc-kubeconfig`.
- Prometheus metric indexing (kube-burner `index`) uses `ES_SERVER` / `ES_INDEX=ripsaw-kube-burner` when set in the environment; install-timer docs use `--es-url` / `--es-index`.

## Execution Summary

At the end of execution, a summary is displayed showing:

- Clusters requested vs created successfully
- Workloads executed successfully vs failed
- Clusters deleted successfully vs failed
- List of any failed clusters with failure reasons
