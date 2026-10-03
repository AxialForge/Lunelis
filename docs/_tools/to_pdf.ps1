# Open a .docx in Word, update every field (table of contents, page numbers),
# save it, and export a PDF next to it with heading bookmarks.
#
#   powershell -ExecutionPolicy Bypass -File docs\_tools\to_pdf.ps1 <file.docx> [<file.docx> ...]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Files)

foreach ($f in $Files) {
    # One Word per document: after a large export Word can drop the COM
    # connection, so a shared instance would fail on the next file.
    $before = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object Id)
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try {
        $path = (Resolve-Path $f).Path
        $doc = $word.Documents.Open($path, $false, $false)
        $doc.Repaginate()
        foreach ($toc in $doc.TablesOfContents) { $toc.Update() }
        $doc.Fields.Update() | Out-Null
        foreach ($sec in $doc.Sections) {
            foreach ($h in $sec.Headers) { $h.Range.Fields.Update() | Out-Null }
            foreach ($h in $sec.Footers) { $h.Range.Fields.Update() | Out-Null }
        }
        foreach ($toc in $doc.TablesOfContents) { $toc.Update() }   # again: page numbers settle after the first pass
        $doc.Save()
        $pdf = [System.IO.Path]::ChangeExtension($path, ".pdf")
        $pages = $doc.ComputeStatistics(2)
        # 17 = PDF, 0 = optimise for print, 1 = bookmarks from headings
        $doc.ExportAsFixedFormat($pdf, 17, $false, 0, 0, 1, 1, 0, $true, $true, 1, $true, $true, $false)
        try { $doc.Close($false) } catch { }
        Write-Output "$pdf : $pages pages"
    }
    finally {
        try { $word.Quit() } catch { }
        try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null } catch { }
        # A disconnected Word lingers hidden and keeps the .docx locked. Stop only
        # windowless Word processes that this script started.
        Start-Sleep -Seconds 2
        Get-Process WINWORD -ErrorAction SilentlyContinue |
            Where-Object { $before -notcontains $_.Id -and -not $_.MainWindowTitle } |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }
}
