$envFile = Get-Content .env
foreach ($line in $envFile) {
    if ($line -match '^([A-Z_]+)=(.+)$' -and $line -notmatch '^\s*#') {
        $key = $matches[1]
        $val = $matches[2]
        Set-Item -Path "Env:$key" -Value $val
    }
}
$env:GAMEFORGE_ALLOW_INSECURE_LOCALHOST = "true"
$env:GAMEFORGE_API_KEYS = "dev-key-12345"
.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
