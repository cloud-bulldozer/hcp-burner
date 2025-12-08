@description('The name of the Hypershift cluster to which the node pool will be attached.')
param clusterName string

@description('The name of the node pool')
param nodePoolName string

@description('The VM size of the node pool')
param nodeSize string = 'Standard_E8s_v3'

resource hcp 'Microsoft.RedHatOpenShift/hcpOpenShiftClusters@2024-06-10-preview' existing = {
  name: clusterName
}

resource nodepool 'Microsoft.RedHatOpenShift/hcpOpenShiftClusters/nodePools@2024-06-10-preview' = {
  parent: hcp
  name: nodePoolName
  location: resourceGroup().location
  properties: {
    version: {
      id: '4.19.7'
      channelGroup: 'stable'
    }
    platform: {
      subnetId: hcp.properties.platform.subnetId
      vmSize: nodeSize
      osDisk: {
        sizeGiB: 128
        diskStorageAccountType: 'StandardSSD_LRS'
      }
    }
    replicas: 2
    labels: [
      {
        key: 'node-role.kubernetes.io/infra'
        value: ''
      }
    ]
    taints: [
      {
        key: 'node-role.kubernetes.io/infra'
        value: ''
        effect: 'NoSchedule'
      }
    ]
  }
}
