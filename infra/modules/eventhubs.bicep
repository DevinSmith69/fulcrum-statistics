// Event Hubs Standard namespace (Kafka endpoint on 9093), the five topics and every consumer group from
// docs/API_CONTRACT.md section 1. Declaring the existing namespace by name adopts it: ARM PUTs are idempotent,
// and the properties below mirror what AZURE_SETUP.md created, so what-if shows no destructive change.

@description('Namespace name. Default adopts the namespace created by hand in AZURE_SETUP.md.')
param name string
param location string
param tags object = {}

@description('Throughput units (1 TU = 1 MB/s in, 2 MB/s out; plenty for one match at 10x).')
param capacity int = 1
param zoneRedundant bool = true
param partitionCount int = 2
param retentionDays int = 7

@description('Principal (the apps\' user-assigned identity) that gets Data Sender + Data Receiver for the managed-identity path.')
param appPrincipalId string

@description('Key Vault that receives the app rule connection string as secret `eventhubs-connection-string`.')
param keyVaultName string

// Topic -> consumer groups (API_CONTRACT section 1). `agents` on match-events is the WS3 addition.
var topics = [
  { name: 'match-events', groups: ['engine', 'agents', 'api-feed'] }
  { name: 'tracking-frames', groups: ['engine'] }
  { name: 'match-state', groups: ['api'] }
  { name: 'moments', groups: ['agents', 'api'] }
  { name: 'insights', groups: ['api'] }
]
var consumerGroups = flatten(map(topics, t => map(t.groups, g => { topic: t.name, group: g })))

resource ns 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: capacity }
  properties: {
    kafkaEnabled: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false // the apps use a SAS connection string from Key Vault (MI/OAUTHBEARER also granted)
    zoneRedundant: zoneRedundant
    isAutoInflateEnabled: false
  }
}

resource hubs 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = [
  for t in topics: {
    parent: ns
    name: t.name
    properties: {
      partitionCount: partitionCount
      retentionDescription: { cleanupPolicy: 'Delete', retentionTimeInHours: retentionDays * 24 }
    }
  }
]

resource groups 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = [
  for cg in consumerGroups: {
    name: '${ns.name}/${cg.topic}/${cg.group}'
    properties: {}
    dependsOn: [hubs]
  }
]

// Least-privilege SAS rule for the apps (Send + Listen, no Manage). Its connection string goes to Key Vault.
resource appRule 'Microsoft.EventHub/namespaces/authorizationRules@2024-01-01' = {
  parent: ns
  name: 'fulcrum-apps'
  properties: { rights: ['Send', 'Listen'] }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource connSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'eventhubs-connection-string'
  properties: {
    value: appRule.listKeys().primaryConnectionString
    contentType: 'Event Hubs SAS connection string (rule fulcrum-apps: Send, Listen)'
  }
}

var dataSender = '2b629674-e913-4c01-ae53-ef4638d8f975' // Azure Event Hubs Data Sender
var dataReceiver = 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde' // Azure Event Hubs Data Receiver

resource senderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ns
  name: guid(ns.id, appPrincipalId, dataSender)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', dataSender)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource receiverRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ns
  name: guid(ns.id, appPrincipalId, dataReceiver)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', dataReceiver)
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output name string = ns.name
output fqdn string = '${ns.name}.servicebus.windows.net'
output connectionStringSecretUri string = connSecret.properties.secretUri
output topics array = [for t in topics: t.name]
