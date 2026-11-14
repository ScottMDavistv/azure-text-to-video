
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
