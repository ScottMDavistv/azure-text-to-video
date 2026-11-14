targetScope = 'resourceGroup'

@description('Deployment location')
param location string = resourceGroup().location

@description('Managed identity for FastAPI container image pulls')
param backendIdentityName string = 'id-textvideo-api-dev'

resource backendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: backendIdentityName
  location: location
  tags: {
    project: 'azure-text-to-video'
    environment: 'development'
  }
}

output backendIdentityId string = backendIdentity.id
output backendIdentityPrincipalId string = backendIdentity.properties.principalId
