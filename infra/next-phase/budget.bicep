targetScope = 'subscription'

@description('Name of the subscription cost budget.')
param budgetName string

@minValue(1)
@maxValue(100)
@description('Monthly subscription cost ceiling in billing currency units.')
param monthlyAmount int

@description('First day of the current budget month in ISO-8601 format.')
param startDate string

@secure()
@description('Runtime-only budget notification recipient. Never commit this value.')
param notificationEmail string

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: budgetName
  properties: {
    amount: monthlyAmount
    category: 'Cost'
    notifications: {
      actual50: {
        contactEmails: [
          notificationEmail
        ]
        contactGroups: []
        contactRoles: [
          'Owner'
        ]
        enabled: true
        locale: 'en-us'
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
      }
      actual80: {
        contactEmails: [
          notificationEmail
        ]
        contactGroups: []
        contactRoles: [
          'Owner'
        ]
        enabled: true
        locale: 'en-us'
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
      }
      forecast100: {
        contactEmails: [
          notificationEmail
        ]
        contactGroups: []
        contactRoles: [
          'Owner'
        ]
        enabled: true
        locale: 'en-us'
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
      }
    }
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
  }
}

output budgetName string = budget.name
output monthlyAmount int = monthlyAmount