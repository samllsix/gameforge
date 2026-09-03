# Build and optionally push the sandbox-runner Docker image.
#
# Usage:
#   .\scripts\build-sandbox-runner.ps1 [-Push] [-Tag <tag>]
#
# Environment:
#   SANDBOX_RUNNER_IMAGE  Default image name (default: gameforge/sandbox-runner:latest)

param(
    [switch]$Push,
    [string]$Tag = "latest"
)

$ErrorActionPreference = "Stop"

$image = $env:SANDBOX_RUNNER_IMAGE -or "gameforge/sandbox-runner:latest"
$imageWithTag = $image -replace ":.*", ":$Tag"

Write-Host "Building $imageWithTag ..."
docker build -t $imageWithTag -f docker/sandbox-runner/Dockerfile .

if ($Push) {
    Write-Host "Pushing $imageWithTag ..."
    docker push $imageWithTag
}

Write-Host "Done."
