# ARO-HCP (Azure Red Hat OpenShift - Hosted Control Plane)

This document covers the CLI arguments for deploying and managing ARO-HCP clusters using hcp-burner.

## Prerequisites

- Azure CLI (`az`) installed and configured
- Azure credentials file with the following keys:
  - `tenantId`
  - `subscriptionId`
  - `ClientId`
  - `ClientSecret`
- Bicep CLI for template compilation

## CLI Arguments

### ARO Platform Arguments (Common)

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--azure-credentials-file` | `HCP_BURNER_AZURE_CREDENTIALS_FILE` | *Required* | Path to Azure credentials JSON file |
| `--azure-region` | `HCP_BURNER_AZURE_REGION` | `eastus` | Azure region for cluster deployment |
| `--azure-mc-subscription` | `HCP_BURNER_MC_SUBSCRIPTION` | - | Azure subscription where Management Cluster is installed |
| `--aro-env` | `HCP_BURNER_ARO_ENV` | `production` | ARO environment |
| `--aro-version` | `HCP_BURNER_ARO_VERSION` | `4.20.8` | OpenShift version (major.minor.patch format). Cluster uses major.minor, nodepools use full version |
| `--aro-version-channel` | `HCP_BURNER_ARO_VERSION_CHANNEL` | `stable` | Version channel group. Options: `stable`, `candidate` |

### ARO-HCP Hypershift Arguments

#### Resource Configuration

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--customer-rg-name` | `HCP_BURNER_CUSTOMER_RG_NAME` | `{cluster_name}-rg` | Customer resource group name |
| `--managed-resource-group` | `HCP_BURNER_MANAGED_RESOURCE_GROUP` | `{cluster_name}-managed-rg` | Managed resource group for HCP cluster |
| `--ticket-id` | `HCP_BURNER_TICKET_ID` | `default` | Ticket ID for resource tags |

#### Network Configuration

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--customer-nsg` | `HCP_BURNER_CUSTOMER_NSG` | `{cluster_name}-nsg` | Customer Network Security Group name |
| `--customer-vnet-name` | `HCP_BURNER_CUSTOMER_VNET_NAME` | `{cluster_name}-vnet` | Customer Virtual Network name |
| `--customer-vnet-subnet1` | `HCP_BURNER_CUSTOMER_VNET_SUBNET1` | `{cluster_name}-subnet1` | Customer Virtual Network Subnet name |

#### Node Configuration

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--worker-size` | `HCP_BURNER_WORKER_SIZE` | `Standard_D4s_v3` | Azure VM size for worker nodes |
| `--infra-size` | `HCP_BURNER_INFRA_SIZE` | `Standard_E8s_v3` | Azure VM size for infra nodes |
| `--add-aro-hcp-infra` | `HCP_BURNER_ADD_ARO_HCP_INFRA` | `False` | Create infra nodepool for monitoring/ingress. Accepts: `true/false`, `1/0`, `yes/no` |

#### Autoscaling Configuration

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--autoscale` | - | `False` | Enable autoscaling for worker nodepools |
| `--min-replicas` | `HCP_BURNER_MIN_REPLICAS` | `1` | Minimum worker replicas for autoscaling |
| `--max-replicas` | `HCP_BURNER_MAX_REPLICAS` | *Required if autoscale* | Maximum worker replicas for autoscaling |

#### Authentication & Monitoring

| Argument | Environment Variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--azure-ad-group-name` | `HCP_BURNER_AZURE_AD_GROUP_NAME` | `aro-hcp-perfscale` | Azure AD group name for cluster-admin access |
| `--issuer-url` | `HCP_BURNER_ISSUER_URL` | `https://login.microsoftonline.com/{tenant_id}/v2.0` | OIDC issuer URL for external auth |
| `--azure-prom-token-file` | `HCP_BURNER_AZURE_PROM_TOKEN_FILE` | - | Path to AZURE_PROM_TOKEN file for MC metrics scraping |

## Usage Examples

### Basic Cluster Creation

```bash
python hcp-burner.py \
  --platform aro \
  --subplatform hypershift \
  --azure-credentials-file /path/to/azure-creds.json \
  --azure-region eastus \
  --cluster-name-seed mycluster \
  --cluster-count 1 \
  --workers 3 \
  --install-clusters
```

### With Infra Nodes

```bash
python hcp-burner.py \
  --platform aro \
  --subplatform hypershift \
  --azure-credentials-file /path/to/azure-creds.json \
  --azure-region eastus \
  --cluster-name-seed mycluster \
  --cluster-count 1 \
  --workers 3 \
  --add-aro-hcp-infra true \
  --infra-size Standard_E8s_v3 \
  --install-clusters
```

### With Autoscaling

```bash
python hcp-burner.py \
  --platform aro \
  --subplatform hypershift \
  --azure-credentials-file /path/to/azure-creds.json \
  --azure-region eastus \
  --cluster-name-seed mycluster \
  --cluster-count 1 \
  --workers 3 \
  --autoscale \
  --min-replicas 2 \
  --max-replicas 10 \
  --install-clusters
```

### Specifying OpenShift Version

```bash
python hcp-burner.py \
  --platform aro \
  --subplatform hypershift \
  --azure-credentials-file /path/to/azure-creds.json \
  --aro-version 4.17.12 \
  --aro-version-channel stable \
  --cluster-name-seed mycluster \
  --cluster-count 1 \
  --workers 3 \
  --install-clusters
```

### Cleanup Clusters

```bash
python hcp-burner.py \
  --platform aro \
  --subplatform hypershift \
  --azure-credentials-file /path/to/azure-creds.json \
  --cluster-name-seed mycluster \
  --cluster-count 1 \
  --cleanup-clusters
```

## Azure Credentials File Format

```json
{
  "tenantId": "your-tenant-id",
  "subscriptionId": "your-subscription-id",
  "ClientId": "your-client-id",
  "ClientSecret": "your-client-secret"
}
```

## Infra Nodes

When `--add-aro-hcp-infra true` is set:

1. An infra nodepool (`np-infra`) is created with:
   - Label: `node-role.kubernetes.io/infra=""`
   - Taint: `node-role.kubernetes.io/infra:NoSchedule`

2. After nodes are ready, the following components are moved to infra nodes:
   - **Ingress Controller** - Router pods
   - **Monitoring Stack** - Prometheus, Alertmanager, Grafana, etc.

## Version Handling

The `--aro-version` parameter accepts a full version (e.g., `4.20.8`):
- **Cluster**: Uses `major.minor` (e.g., `4.20`)
- **Nodepools**: Uses full version (e.g., `4.20.8`)

## Execution Summary

At the end of execution, a summary is displayed showing:
- Clusters requested vs created successfully
- Workloads executed successfully vs failed
- Clusters deleted successfully vs failed
- List of any failed clusters with failure reasons

