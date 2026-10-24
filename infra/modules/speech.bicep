// Optional Azure AI Speech resource for the overlay's neural voice commentary (POST /api/speech).
// Keyless: local (key) auth is disabled; the apps' user-assigned identity gets "Cognitive Services Speech User"
// and calls the custom-domain endpoint with an Entra token (docs/DEPLOY.md, "Voice commentary").

param name string
param location string
param tags object
param principalId string

@allowed(['F0', 'S0'])
@description('F0 = free tier (one per subscription; 0.5M neural characters/month).')
param sku string = 'F0'

// "Cognitive Services Speech User"
var speechUser = 'f2dc8367-1007-4938-bd23-fe263f013447'

resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: { name: sku }
  properties: {
    customSubDomainName: name // required for Microsoft Entra auth; cannot be changed later
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource speechRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: speech
  name: guid(speech.id, principalId, speechUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', speechUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output id string = speech.id
output endpoint string = 'https://${name}.cognitiveservices.azure.com'
output region string = location
