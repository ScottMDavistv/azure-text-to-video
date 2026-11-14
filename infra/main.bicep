
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
  }
}

output containerAppsEnvironmentId string = containerAppsEnvironment.id
