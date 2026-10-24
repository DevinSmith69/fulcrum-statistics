// Re-provisioning must not reset a deployed app to the placeholder image: when the app already exists
// (azd sets SERVICE_<NAME>_RESOURCE_EXISTS=true after the first deploy), read its current image.

param exists bool
param name string

resource existingApp 'Microsoft.App/containerApps@2024-03-01' existing = if (exists) {
  name: name
}

output containers array = exists ? existingApp!.properties.template.containers : []
