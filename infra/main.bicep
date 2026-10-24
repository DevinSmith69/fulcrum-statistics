// Fulcrum on Azure (azd, resource-group scope). See docs/DEPLOY.md.
//
//   Log Analytics + App Insights, Container Registry, user-assigned identity, Key Vault,
//   Event Hubs Standard (adopts the existing namespace), Container Apps environment with
//   api (external, WebSockets) / sim (internal) / engine / agents, Static Web App for web/.
//   The Foundry project already exists and is only referenced (role assignment for the identity).

targetScope = 'resourceGroup'

@minLength(1)
@maxLength(64)
@description('azd environment name (AZURE_ENV_NAME).')
param environmentName string

@description('Region for everything except the Static Web App.')
param location string = resourceGroup().location

@description('Naming suffix shared with the hand-made resources (AZURE_SETUP.md).')
param suffix string = 'ds534'

// ---- existing resources -------------------------------------------------------------------------------------
@description('Event Hubs namespace to adopt (or create, if it does not exist).')
param eventHubsNamespaceName string = 'evh-fulcrum-${suffix}'
param eventHubsZoneRedundant bool = true

@description('Existing Foundry (AIServices) account and project. Not created or modified here.')
param foundryAccountName string = 'fulcrum-${suffix}'
param foundryProjectName string = 'fulcrum'
param foundryResourceGroup string = resourceGroup().name
param foundryModelDeployment string = 'fast'
param foundryRecapDeployment string = 'strong'

// ---- app settings -------------------------------------------------------------------------------------------
@allowed(['foundry', 'fake'])
@description('LLM client for the agents. `fake` = deterministic template copy (no Foundry calls).')
param llm string = 'foundry'

@description('Cut-list fallback: run sim + engine + agents inside the api container on the memory bus.')
param inProcess bool = false

@description('Start one match when the sim app boots (a fresh deployment then has a live match).')
param simAutostart bool = true
param simSpeed string = '10'

@description('Static Web Apps is not offered in Sweden Central, and West Europe refused new SWAs for this subscription (what-if, 24 Oct). It only hosts static files; the API stays in Sweden Central.')
@allowed(['eastus2', 'centralus', 'westus2', 'westeurope', 'eastasia'])
param staticWebAppLocation string = 'eastus2'

@description('Extra CORS origins for the API, comma-separated (e.g. http://localhost:5173 for local web dev).')
param extraAllowedOrigins string = ''

// Set by azd after the first deploy so re-provisioning keeps the deployed images.
param apiExists bool = false
param simExists bool = false
param engineExists bool = false
param agentsExists bool = false

var tags = { 'azd-env-name': environmentName, app: 'fulcrum' }
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var names = {
  logs: 'log-fulcrum-${suffix}'
  appInsights: 'appi-fulcrum-${suffix}'
  registry: 'crfulcrum${suffix}'
  identity: 'id-fulcrum-${suffix}'
  keyVault: 'kv-fulcrum-${suffix}'
  environment: 'cae-fulcrum-${suffix}'
  staticWebApp: 'swa-fulcrum-${suffix}'
  api: 'ca-fulcrum-api'
  sim: 'ca-fulcrum-sim'
  engine: 'ca-fulcrum-engine'
  agents: 'ca-fulcrum-agents'
}

// ---- observability ------------------------------------------------------------------------------------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.logs
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 } // hard stop on runaway logging (budget)
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: names.appInsights
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---- identity, registry, secrets ----------------------------------------------------------------------------
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.identity
  location: location
  tags: tags
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: names.registry
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: names.keyVault
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, roles.acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, identity.id, roles.keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---- Event Hubs (adopted) + connection string into Key Vault ------------------------------------------------
module eventHubs 'modules/eventhubs.bicep' = {
  name: 'eventhubs'
  params: {
    name: eventHubsNamespaceName
    location: location
    tags: tags
    zoneRedundant: eventHubsZoneRedundant
    appPrincipalId: identity.properties.principalId
    keyVaultName: keyVault.name
  }
}

// ---- Foundry (existing): role for the identity --------------------------------------------------------------
module foundry 'modules/foundry-role.bicep' = {
  name: 'foundry-role'
  scope: resourceGroup(foundryResourceGroup)
  params: {
    accountName: foundryAccountName
    projectName: foundryProjectName
    principalId: identity.properties.principalId
  }
}

// ---- Static Web App (web/) ----------------------------------------------------------------------------------
resource web 'Microsoft.Web/staticSites@2023-12-01' = {
  name: names.staticWebApp
  location: staticWebAppLocation
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

// ---- Container Apps -----------------------------------------------------------------------------------------
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: names.environment
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var webOrigin = 'https://${web.properties.defaultHostname}'
var allowedOrigins = empty(extraAllowedOrigins) ? webOrigin : '${webOrigin},${extraAllowedOrigins}'

var commonEnv = [
  { name: 'FULCRUM_BUS', value: 'eventhubs' }
  { name: 'EVENTHUBS_NAMESPACE', value: eventHubs.outputs.name }
  { name: 'EVENTHUBS_CONNECTION_STRING', secretRef: 'eventhubs-connection-string' }
  { name: 'FULCRUM_LLM', value: llm }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundry.outputs.projectEndpoint }
  { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: foundryModelDeployment }
  { name: 'FOUNDRY_MODEL_DEPLOYMENT_RECAP', value: foundryRecapDeployment }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } // DefaultAzureCredential -> this identity
  { name: 'FULCRUM_TEAMS_PATH', value: '/app/data/teams.json' }
]
var kvSecrets = [
  { name: 'eventhubs-connection-string', keyVaultUrl: eventHubs.outputs.connectionStringSecretUri }
]

// In the in-process fallback the api runs everything on the memory bus; the other three apps scale to zero.
var apiEnv = concat(
  inProcess
    ? concat(filter(commonEnv, e => e.name != 'FULCRUM_BUS'), [
        { name: 'FULCRUM_BUS', value: 'memory' }
        { name: 'FULCRUM_INPROCESS', value: '1' }
      ])
    : concat(commonEnv, [{ name: 'FULCRUM_SIM_URL', value: 'http://${names.sim}' }]),
  [
    { name: 'FULCRUM_SERVICE', value: 'api' }
    { name: 'FULCRUM_ALLOWED_ORIGINS', value: allowedOrigins }
  ]
)
var backendReplicas = inProcess ? 0 : 1

module apiImage 'modules/fetch-image.bicep' = {
  name: 'api-image'
  params: { exists: apiExists, name: names.api }
}
module simImage 'modules/fetch-image.bicep' = {
  name: 'sim-image'
  params: { exists: simExists, name: names.sim }
}
module engineImage 'modules/fetch-image.bicep' = {
  name: 'engine-image'
  params: { exists: engineExists, name: names.engine }
}
module agentsImage 'modules/fetch-image.bicep' = {
  name: 'agents-image'
  params: { exists: agentsExists, name: names.agents }
}

module api 'modules/containerapp.bicep' = {
  name: 'app-api'
  params: {
    name: names.api
    location: location
    tags: tags
    serviceName: 'api'
    environmentId: environment.id
    identityId: identity.id
    registryServer: registry.properties.loginServer
    image: apiExists ? apiImage.outputs.containers[0].image : placeholderImage
    env: apiEnv
    keyVaultSecrets: kvSecrets
    ingress: 'external'
    targetPort: 8000
    cpu: '0.5'
    memory: '1Gi'
    // One replica: the API keeps per-match read models and WebSocket fan-out in memory.
    minReplicas: 1
    maxReplicas: 1
  }
  dependsOn: [acrPull, kvSecretsUser]
}

module sim 'modules/containerapp.bicep' = {
  name: 'app-sim'
  params: {
    name: names.sim
    location: location
    tags: tags
    serviceName: 'sim'
    environmentId: environment.id
    identityId: identity.id
    registryServer: registry.properties.loginServer
    image: simExists ? simImage.outputs.containers[0].image : placeholderImage
    env: concat(commonEnv, [
      { name: 'FULCRUM_SERVICE', value: 'sim' }
      { name: 'FULCRUM_SIM_AUTOSTART', value: simAutostart && !inProcess ? '1' : '0' }
      { name: 'FULCRUM_SIM_SPEED', value: simSpeed }
    ])
    keyVaultSecrets: kvSecrets
    ingress: 'internal' // POST /matches from the api; no public endpoint
    targetPort: 8001
    cpu: '0.25'
    memory: '0.5Gi'
    minReplicas: backendReplicas // a running match lives in this process: never scale it to zero mid-match
    maxReplicas: 1
  }
  dependsOn: [acrPull, kvSecretsUser]
}

module engine 'modules/containerapp.bicep' = {
  name: 'app-engine'
  params: {
    name: names.engine
    location: location
    tags: tags
    serviceName: 'engine'
    environmentId: environment.id
    identityId: identity.id
    registryServer: registry.properties.loginServer
    image: engineExists ? engineImage.outputs.containers[0].image : placeholderImage
    env: concat(commonEnv, [{ name: 'FULCRUM_SERVICE', value: 'engine' }])
    keyVaultSecrets: kvSecrets
    cpu: '0.5'
    memory: '1Gi'
    minReplicas: backendReplicas
    maxReplicas: 1 // per-match state in memory; partitions are keyed by match_id if this ever scales out
  }
  dependsOn: [acrPull, kvSecretsUser]
}

module agents 'modules/containerapp.bicep' = {
  name: 'app-agents'
  params: {
    name: names.agents
    location: location
    tags: tags
    serviceName: 'agents'
    environmentId: environment.id
    identityId: identity.id
    registryServer: registry.properties.loginServer
    image: agentsExists ? agentsImage.outputs.containers[0].image : placeholderImage
    env: concat(commonEnv, [{ name: 'FULCRUM_SERVICE', value: 'agents' }])
    keyVaultSecrets: kvSecrets
    cpu: '0.5'
    memory: '1Gi'
    minReplicas: backendReplicas
    maxReplicas: 1
  }
  dependsOn: [acrPull, kvSecretsUser]
}

// ---- outputs (azd stores these in .azure/<env>/.env) --------------------------------------------------------
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = subscription().tenantId
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output AZURE_CONTAINER_ENVIRONMENT_NAME string = environment.name
output AZURE_KEY_VAULT_NAME string = keyVault.name
output AZURE_MANAGED_IDENTITY_CLIENT_ID string = identity.properties.clientId
output API_URL string = 'https://${api.outputs.fqdn}'
output WEB_URL string = webOrigin
output EVENTHUBS_NAMESPACE string = eventHubs.outputs.name
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output APPLICATIONINSIGHTS_NAME string = appInsights.name
