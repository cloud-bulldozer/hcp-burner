@description('The name of the external auth provider configuration')
param externalAuthName string

@description('The issuer url')
param issuerURL string

@description('The client ID')
param clientID string

@description('Name of the hypershift cluster')
param clusterName string

resource hcp 'Microsoft.RedHatOpenShift/hcpOpenShiftClusters@2024-06-10-preview' existing = {
  name: clusterName
}

resource externalauth 'Microsoft.RedHatOpenShift/hcpOpenShiftClusters/externalAuths@2024-06-10-preview' = {
  parent: hcp
  name: externalAuthName
  properties: {
    claim: {
      mappings: {
        username: {
          claim: 'email'
        }
        groups: {
          claim: 'groups'
        }
      }
    }
    clients: [
      {
        clientId: clientID
        component: {
          name: 'console'
          authClientNamespace: 'openshift-console'
        }
        type: 'Confidential'
      }
      {
        clientId: clientID
        component: {
          name: 'cli'
          authClientNamespace: 'openshift-console'
        }
        type: 'Public'
      }
    ]
    issuer: {
      url: issuerURL
      audiences: [
        clientID
      ]
    }
  }
}
