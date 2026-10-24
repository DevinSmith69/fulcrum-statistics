// Grants the apps' identity "Foundry User" on the EXISTING Foundry project (and, by default, its account).
// Nothing here creates or changes the Foundry resource itself.

param accountName string
param projectName string
param principalId string

@description('Also assign at account scope, in case the LLM client calls the account (OpenAI-style) endpoint.')
param includeAccountScope bool = true

// "Foundry User" (formerly "Azure AI User"; the GUID did not change).
var foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: account
  name: projectName
}

resource projectRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: guid(project.id, principalId, foundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource accountRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (includeAccountScope) {
  scope: account
  name: guid(account.id, principalId, foundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
