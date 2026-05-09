# Azure DevOps PR AI Review Integration Options

This repository now includes a reusable Azure DevOps step template for PR AI review.

## Option 1: Mirror to GitHub for native Copilot review

- Mirror Azure Repos branches/PRs to GitHub.
- Run GitHub Copilot PR review natively on the mirrored PR.
- Optionally sync a summary back to Azure DevOps PR comments.

## Option 2 (recommended): Azure DevOps-native PR AI review template

Use `templates/pr-ai-review.yml` in PR validation pipelines.

### What it does

1. Checks out the repo with full history.
2. Builds PR diff from source/target branches.
3. Sends PR context + diff to a model endpoint.
4. Posts summary and findings as Azure DevOps PR comment threads.

### Prerequisites

- Enable **Allow scripts to access the OAuth token** so `$(System.AccessToken)` is available.
- Add a secret pipeline variable containing your model API key.
- Provide a model endpoint that accepts chat-style JSON and returns JSON findings.

### Example usage

```yaml
# azure-pipelines.yml
pr:
  branches:
    include: [main]

steps:
  - template: templates/pr-ai-review.yml
    parameters:
      org: your-org
      project: your-project
      repo: your-repo
      modelEndpoint: https://models.example.com/v1/chat/completions
      modelKeySecretName: MODEL_API_KEY
```

## Option 3: `gh copilot` in pipeline (POC)

You can install `gh` + `gh-copilot` in a pipeline and run prompt-based review, but this is generally less reliable for unattended CI due to auth/session constraints.
