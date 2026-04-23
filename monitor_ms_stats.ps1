$ErrorActionPreference = "Stop"

$projectRoot = "C:\Users\Administrator\PycharmProjects\PythonProject"
$logDir = Join-Path $projectRoot "logs"
$logPath = Join-Path $logDir "ms_download_status.log"
$sharedRoot = "\\192.168.1.18\跨部门共享\设计图片下载\官网下图\男装\M&S"

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

function Get-FileCount {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return 0
    }

    return (Get-ChildItem $Path -File -ErrorAction SilentlyContinue | Measure-Object).Count
}

function Write-StatusSnapshot {
    $script = @'
from pathlib import Path
import json
import ms_downloader as m

ctx = m.RunContext(
    request_manager=m.RequestManager(),
    cache_root=Path(".ms_cache"),
    progress_root=Path("M&S_temp") / "progress",
)

output_root = Path(r"\\192.168.1.18\跨部门共享\设计图片下载\官网下图\男装\M&S")

rows = []
for category_name, category_url in m.CATEGORIES.items():
    current_url = category_url
    current_page = 1
    seen = set()
    total = 0
    while current_url:
        cache_path = m.url_cache_path(ctx.cache_root, "html", current_url, ".html")
        html = ctx.request_manager.fetch_text(
            current_url,
            cache_path=cache_path,
            ttl_seconds=m.HTML_CACHE_TTL_SECONDS,
        )
        for product_url in m.extract_product_urls(html, current_url):
            normalized = m.normalize_product_url(product_url)
            if normalized not in seen:
                seen.add(normalized)
                total += 1
        next_url = m.find_next_page_url(html, current_url, current_page)
        if not next_url:
            break
        current_url = next_url
        current_page += 1

    progress_path = ctx.progress_root / f"{category_name}.json"
    completed = set(m.load_progress(progress_path).get("completed", []))
    local_count = len(list((output_root / category_name).glob("*.png"))) if (output_root / category_name).exists() else 0
    rows.append(
        {
            "category": category_name,
            "total": total,
            "completed": len(completed),
            "remaining": max(total - len(completed), 0),
            "local_files": local_count,
        }
    )

download_running = False
try:
    import subprocess
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('py.exe','python.exe') -and $_.CommandLine -like '*ms_downloader.py*' } | Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    download_running = bool(completed.stdout.strip())
except Exception:
    download_running = False

payload = {
    "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "download_running": download_running,
    "rows": rows,
}
print(json.dumps(payload, ensure_ascii=False))
'@

    $json = $script | py -3 -
    $payload = $json | ConvertFrom-Json

    Add-Content -Path $logPath -Value ("[{0}] download_running={1}" -f $payload.timestamp, $payload.download_running)
    foreach ($row in $payload.rows) {
        $sharedCount = Get-FileCount -Path (Join-Path $sharedRoot $row.category)
        Add-Content -Path $logPath -Value (
            "  {0}: total={1}, completed={2}, remaining={3}, local_files={4}, shared_files={5}" -f
            $row.category, $row.total, $row.completed, $row.remaining, $row.local_files, $sharedCount
        )
    }
    Add-Content -Path $logPath -Value ""
}

Write-StatusSnapshot
while ($true) {
    Start-Sleep -Seconds 1800
    try {
        Write-StatusSnapshot
    } catch {
        Add-Content -Path $logPath -Value ("[{0}] snapshot_failed: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message)
        Add-Content -Path $logPath -Value ""
    }
}
