#!/bin/sh

REPORT_JSON="/tmp/phpstan/cache/report.json"
REPORT_HTML="/tmp/phpstan/cache/report.html"
CONTAINER_PATH="/var/www/html"
HOST_PROJECT_PATH="${HOST_PROJECT_PATH:-/var/www/html}"

if [ ! -f "$REPORT_JSON" ]; then
    echo "[error] report.json not found at $REPORT_JSON. Run 'make report' first."
    exit 1
fi

total_errors=$(jq '.totals.file_errors' "$REPORT_JSON")
total_files=$(jq '.files | length' "$REPORT_JSON")
generated_at=$(date '+%Y-%m-%d %H:%M:%S')

cat > "$REPORT_HTML" <<HTMLEOF
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PHPStan Report</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; font-size: 14px; }
        header { background: #1a1a2e; color: #fff; padding: 24px 32px; display: flex; align-items: center; justify-content: space-between; }
        header h1 { font-size: 20px; font-weight: 600; letter-spacing: 0.5px; }
        header .meta { font-size: 12px; color: #aaa; margin-top: 4px; }
        .summary { display: flex; gap: 16px; padding: 20px 32px; background: #fff; border-bottom: 1px solid #e0e0e0; }
        .stat { background: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px 20px; min-width: 140px; text-align: center; }
        .stat .value { font-size: 28px; font-weight: 700; color: #c0392b; }
        .stat .label { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
        .stat.ok .value { color: #27ae60; }
        main { padding: 24px 32px; max-width: 1200px; }
        details { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
        details[open] { box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        summary { padding: 14px 18px; cursor: pointer; display: flex; align-items: center; justify-content: space-between; user-select: none; list-style: none; }
        summary::-webkit-details-marker { display: none; }
        summary:hover { background: #fafafa; }
        .file-path { font-family: 'SFMono-Regular', Consolas, monospace; font-size: 13px; color: #2c3e50; word-break: break-all; }
        .error-count { background: #c0392b; color: #fff; border-radius: 12px; padding: 2px 10px; font-size: 11px; font-weight: 700; white-space: nowrap; margin-left: 12px; }
        .chevron { color: #aaa; font-size: 12px; margin-left: 8px; transition: transform 0.2s; }
        details[open] .chevron { transform: rotate(90deg); }
        table { width: 100%; border-collapse: collapse; }
        tr:not(:last-child) { border-bottom: 1px solid #f0f0f0; }
        tr:hover { background: #fafcff; }
        td { padding: 10px 18px; vertical-align: top; }
        td.line { width: 60px; text-align: right; }
        .line-badge { display: inline-block; background: #eef2ff; color: #3949ab; border-radius: 4px; padding: 1px 7px; font-family: 'SFMono-Regular', Consolas, monospace; font-size: 12px; font-weight: 600; }
        td.message { color: #444; line-height: 1.5; }
        td.action { width: 140px; text-align: right; white-space: nowrap; }
        .open-link { display: inline-block; background: #1a1a2e; color: #fff; border-radius: 4px; padding: 3px 10px; font-size: 11px; text-decoration: none; transition: background 0.15s; }
        .open-link:hover { background: #3949ab; }
        .no-errors { text-align: center; padding: 48px; color: #27ae60; font-size: 18px; font-weight: 600; }
    </style>
</head>
<body>
<header>
    <div>
        <h1>PHPStan Analysis Report</h1>
        <div class="meta">Generated: $generated_at &nbsp;|&nbsp; Host path: $HOST_PROJECT_PATH</div>
    </div>
</header>
<div class="summary">
    <div class="stat$([ "$total_errors" = "0" ] && echo ' ok')">
        <div class="value">$total_errors</div>
        <div class="label">Total Errors</div>
    </div>
    <div class="stat$([ "$total_files" = "0" ] && echo ' ok')">
        <div class="value">$total_files</div>
        <div class="label">Files Affected</div>
    </div>
</div>
<main>
HTMLEOF

if [ "$total_errors" = "0" ]; then
    echo '<div class="no-errors">&#10003; No errors found</div>' >> "$REPORT_HTML"
else
    jq -c '.files | to_entries[]' "$REPORT_JSON" | while IFS= read -r file_entry; do
        file_path=$(echo "$file_entry" | jq -r '.key')
        errors=$(echo "$file_entry" | jq -c '.value.messages[]')
        error_count=$(echo "$file_entry" | jq '.value.messages | length')

        # Replace container path with host path for display and links
        host_file_path=$(echo "$file_path" | sed "s|$CONTAINER_PATH|$HOST_PROJECT_PATH|")
        rel_file_path=$(echo "$file_path" | sed "s|$CONTAINER_PATH/||")

        cat >> "$REPORT_HTML" <<FILEEOF
<details>
    <summary>
        <span class="file-path">$rel_file_path</span>
        <span style="display:flex;align-items:center">
            <span class="error-count">$error_count</span>
            <span class="chevron">&#9654;</span>
        </span>
    </summary>
    <table>
FILEEOF

        echo "$errors" | while IFS= read -r error; do
            line=$(echo "$error" | jq -r '.line')
            message=$(echo "$error" | jq -r '.message' | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
            phpstorm_url="phpstorm://open?file=$(echo "$host_file_path" | sed 's/ /%20/g')&line=$line"

            cat >> "$REPORT_HTML" <<ROWEOF
        <tr>
            <td class="line"><span class="line-badge">$line</span></td>
            <td class="message">$message</td>
            <td class="action"><a class="open-link" href="$phpstorm_url">Open in PHPStorm</a></td>
        </tr>
ROWEOF
        done

        cat >> "$REPORT_HTML" <<CLOSEEOF
    </table>
</details>
CLOSEEOF
    done
fi

cat >> "$REPORT_HTML" <<FOOTEREOF
</main>
</body>
</html>
FOOTEREOF

echo "[done] Report generated: $REPORT_HTML"
