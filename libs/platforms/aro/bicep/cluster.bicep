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
              vaultName: keyVaultName
              name: etcdEncryptionKeyName
              version: last(split(etcdEncryptionKey.properties.keyUriWithVersion, '/'))
             }
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
      operatorsAuthentication: {
        userAssignedIdentities: {
          controlPlaneOperators: {
            'cluster-api-azure': clusterApiAzureMi.id
            'control-plane': controlPlaneMi.id
            'cloud-controller-manager': cloudControllerManagerMi.id
            #disable-next-line prefer-unquoted-property-names
            'ingress': ingressMi.id
            'disk-csi-driver': diskCsiDriverMi.id
            'file-csi-driver': fileCsiDriverMi.id
            'image-registry': imageRegistryMi.id
            'cloud-network-config': cloudNetworkConfigMi.id
            'kms': kmsMi.id
          }
          dataPlaneOperators: {
            'disk-csi-driver': dpDiskCsiDriverMi.id
            'file-csi-driver': dpFileCsiDriverMi.id
            'image-registry': dpImageRegistryMi.id
          }
          serviceManagedIdentity: serviceManagedIdentity.id
        }
      }
    }
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${serviceManagedIdentity.id}': {}
      '${clusterApiAzureMi.id}': {}
      '${controlPlaneMi.id}': {}
      '${cloudControllerManagerMi.id}': {}
      '${ingressMi.id}': {}
      '${diskCsiDriverMi.id}': {}
      '${fileCsiDriverMi.id}': {}
      '${imageRegistryMi.id}': {}
      '${cloudNetworkConfigMi.id}': {}
      '${kmsMi.id}': {}
    }
  }
  dependsOn: [
    hcpClusterApiProviderRoleSubnetAssignment
    keyVaultCryptoUserToKeyVaultRoleAssignment
    hcpControlPlaneOperatorVnetRoleAssignment
    hcpControlPlaneOperatorNsgRoleAssignment
    cloudControllerManagerRoleSubnetAssignment
    cloudControllerManagerRoleNsgAssignment
    ingressOperatorRoleSubnetAssignment
    fileStorageOperatorRoleSubnetAssignment
    fileStorageOperatorRoleNsgAssignment
    networkOperatorRoleSubnetAssignment
    networkOperatorRoleVnetAssignment
    dpDiskCsiDriverMiFederatedCredentialsRoleAssignment
    dpFileCsiDriverMiFederatedCredentialsRoleAssignment
    dpImageRegistryMiFederatedCredentialsRoleAssignment
    serviceManagedIdentityRoleAssignmentVnet
    serviceManagedIdentityRoleAssignmentSubnet
    serviceManagedIdentityRoleAssignmentNSG
    dpFileCsiDriverFileStorageOperatorRoleSubnetAssignment
    dpFileCsiDriverFileStorageOperatorRoleNsgAssignment
    serviceManagedIdentityReaderOnControlPlaneMi
    serviceManagedIdentityReaderOnCloudControllerManagerMi
    serviceManagedIdentityReaderOnIngressMi
    serviceManagedIdentityReaderOnDiskCsiDriverMi
    serviceManagedIdentityReaderOnFileCsiDriverMi
    serviceManagedIdentityReaderOnImageRegistryMi
    serviceManagedIdentityReaderOnCloudNetworkMi
    serviceManagedIdentityReaderOnClusterApiAzureMi
    serviceManagedIdentityReaderOnKmsMi
    rbacPropagationDelay
  ]
}

// Delay to allow RBAC role assignments to propagate before HCP creation
// Azure RBAC can take 1-5 minutes to fully propagate
resource rbacPropagationDelay 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: '${clusterName}-rbac-delay'
  location: resourceGroup().location
  kind: 'AzurePowerShell'
  properties: {
    azPowerShellVersion: '9.7'
    retentionInterval: 'PT1H'
    scriptContent: 'Start-Sleep -Seconds 60'
    timeout: 'PT5M'
  }
  dependsOn: [
    dpDiskCsiDriverMiFederatedCredentialsRoleAssignment
    dpFileCsiDriverMiFederatedCredentialsRoleAssignment
    dpImageRegistryMiFederatedCredentialsRoleAssignment
    serviceManagedIdentityRoleAssignmentVnet
    serviceManagedIdentityRoleAssignmentSubnet
    serviceManagedIdentityRoleAssignmentNSG
  ]
}
