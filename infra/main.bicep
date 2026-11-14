
targetScope = 'resourceGroup'

@description('Azure deployment location')
param location string = resourceGroup().location

@description('Existing Azure Storage account name')
param storageAccountName string = 'sttextvideo1794671698'

// Reference the existing Storage account.
// Do not create a duplicate.
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

output storageAccountId string = storageAccount.id
output deploymentLocation string = location


@description('Log Analytics workspace name')
param logAnalyticsName string = 'log-textvideo-dev'

// Create a Log Analytics workspace for application monitoring.
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: {
    project: 'azure-text-to-video'
    environment: 'development'
  }
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id

@description('Container Apps environment name')
param containerAppsEnvironmentName string = 'cae-textvideo-dev'

// Create the Container Apps environment.
resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: {
    project: 'azure-text-to-video'
    environment: 'development'
  }
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  zoneRedundant: false
      peerAuthentication: {
        mtls: {
          enabled: false
        }
      }
      peerTrafficConfiguration: {
        encryption: {
          enabled: false
        }
      }
      workloadProfiles: [
        {
          name: 'Consumption'
          workloadProfileType: 'Consumption'
        }
      ]
  }
}

output containerAppsEnvironmentId string = containerAppsEnvironment.id

@description('Private Azure Container Registry for backend images')
param containerRegistryName string = 'acrtextvideo1794671698'

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  sku: {
    name: 'Basic'
  }
  tags: {
    project: 'azure-text-to-video'
    environment: 'development'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

output containerRegistryLoginServer string = containerRegistry.properties.loginServer

@description('Managed identity used by FastAPI to pull container images')
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
