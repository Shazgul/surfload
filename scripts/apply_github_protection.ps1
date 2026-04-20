param(
    [string]$Repo = "Shazgul/surfload"
)

$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (!(Test-Path $gh)) {
    throw "GitHub CLI not found at '$gh'. Install GitHub CLI first."
}

$auth = & $gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

$payload = @'
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "tests (3.10)"},
      {"context": "tests (3.11)"},
      {"context": "tests (3.12)"}
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
'@

$branches = @("main", "develop")
foreach ($branch in $branches) {
    Write-Host "Applying branch protection for $Repo:$branch ..."
    $payload | & $gh api -X PUT "repos/$Repo/branches/$branch/protection" --input -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply protection to branch '$branch'."
    }
}

Write-Host "Branch protection configured for $Repo (main, develop)."
