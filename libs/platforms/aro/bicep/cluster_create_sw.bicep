@description('Name of the hypershift cluster')
param clusterName string

@description('The Hypershift cluster managed resource group name')
param managedResourceGroupName string

@description('The Network security group name for the hcp cluster resources')
param nsgName string

@description('The virtual network name for the hcp cluster resources')
param vnetName string

@description('The subnet name for deploying hcp cluster resources.')
param subnetName string

@description('The KeyVault name that contains the encryption key')
param keyVaultName string

@description('The OpenShift version for the cluster in major.minor format (e.g., 4.20)')
param clusterVersion string = '4.20'

@description('The version channel group (e.g., stable, candidate)')
param versionChannelGroup string = 'stable'

@description('VNet Integration Subnet Name for SWIFT networking')
param vnetIntegrationSubnetName string

@description('Operator identity map produced by cluster-prereqs_sw.bicep')
param operatorsAuth object

@description('Cluster user-assigned identity map produced by cluster-prereqs_sw.bicep')
param hcpIdentity object

var etcdEncryptionKeyName = 'etcd-data-kms-encryption-key'

resource vnet 'Microsoft.Network/virtualNetworks@2022-07-01' existing = {
  name: vnetName
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2022-07-01' existing = {
  name: subnetName
  parent: vnet
}

resource vnetIntegrationSubnet 'Microsoft.Network/virtualNetworks/subnets@2022-07-01' existing = {
  name: vnetIntegrationSubnetName
  parent: vnet
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2022-07-01' existing = {
  name: nsgName
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-12-01-preview' existing = {
  name: keyVaultName
}

resource etcdEncryptionKey 'Microsoft.KeyVault/vaults/keys@2024-12-01-preview' existing = {
  parent: keyVault
  name: etcdEncryptionKeyName
}

resource hcp 'Microsoft.RedHatOpenShift/hcpOpenShiftClusters@2025-12-23-preview' = {
  name: clusterName
  location: resourceGroup().location
  properties: {
    version: {
      id: clusterVersion
      channelGroup: versionChannelGroup
    }
    dns: {}
    network: {
      networkType: 'OVNKubernetes'
      podCidr: '10.128.0.0/14'
      serviceCidr: '172.30.0.0/16'
      machineCidr: '10.0.0.0/16'
      hostPrefix: 23
    }
    console: {}
    etcd: {
      dataEncryption: {
        keyManagementMode: 'CustomerManaged'
        customerManaged: {
          encryptionType: 'KMS'
          kms: {
            activeKey: {
              name: etcdEncryptionKeyName
              version: last(split(etcdEncryptionKey.properties.keyUriWithVersion, '/'))
            }
            vaultName: keyVaultName
            visibility: 'Public'
          }
        }
      }
    }
    api: {
      visibility: 'Public'
    }
    clusterImageRegistry: {
      state: 'Enabled'
    }
    platform: {
      managedResourceGroup: managedResourceGroupName
      subnetId: subnet.id
      outboundType: 'LoadBalancer'
      networkSecurityGroupId: nsg.id
      vnetIntegrationSubnetId: vnetIntegrationSubnet.id
      operatorsAuthentication: operatorsAuth
    }
  }
  identity: hcpIdentity
}
