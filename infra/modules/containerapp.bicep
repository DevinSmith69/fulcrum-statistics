// One Fulcrum Container App. All four apps run the same image; FULCRUM_SERVICE picks the process.

param name string
param location string
param tags object = {}

@description('azd service name (api|sim|engine|agents); azd finds the app by this tag on deploy.')
param serviceName string

param environmentId string
param identityId string
param registryServer string

@description('Image to run. On first provision azd has not built anything yet, so main.bicep passes a placeholder.')
param image string

@description('Environment variables: [{ name, value } | { name, secretRef }].')
param env array = []

@description('Key Vault-backed secrets: [{ name, keyVaultUrl }]. Resolved with the user-assigned identity.')
param keyVaultSecrets array = []

@allowed(['none', 'internal', 'external'])
param ingress string = 'none'
param targetPort int = 8000

param cpu string = '0.5'
param memory string = '1Gi'
param minReplicas int = 1
param maxReplicas int = 1

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: ingress == 'none'
        ? null
        : {
            external: ingress == 'external'
            targetPort: targetPort
            transport: 'auto' // HTTP/1.1 + WebSocket upgrade (the API's /ws)
            allowInsecure: ingress == 'internal' // in-environment calls to the sim use plain http
          }
      registries: [{ server: registryServer, identity: identityId }]
      secrets: [for s in keyVaultSecrets: { name: s.name, keyVaultUrl: s.keyVaultUrl, identity: identityId }]
    }
    template: {
      containers: [
        {
          name: serviceName
          image: image
          env: env
          resources: { cpu: json(cpu), memory: memory }
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
}

output name string = app.name
output fqdn string = ingress == 'none' ? '' : app.properties.configuration.ingress.fqdn
