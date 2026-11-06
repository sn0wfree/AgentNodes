param([string]$Path, [string]$Pattern = '.')
$c = Get-Content -Path $Path -Raw
Write-Output ("LEN=" + $c.Length)
$links = [regex]::Matches($c, 'href="([^"]+)"') | ForEach-Object { $_.Groups[1].Value } | Where-Object { $_ -match $Pattern } | Select-Object -Unique -First 60
$links
