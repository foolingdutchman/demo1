$ErrorActionPreference = "Stop"

$frontendPath = $PSScriptRoot
$port = 5500

Write-Host "Frontend running at http://127.0.0.1:$port"
python -m http.server $port --bind 127.0.0.1 --directory $frontendPath
