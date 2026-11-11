# Generates the Quick Support icon: blue rounded square with a monitor and a
# "Q" inside.
#
#   powershell -ExecutionPolicy Bypass -File make-icon.ps1 [-Letter Q]
#
# Output (next to this script): quicksupport.ico (16..256 px), quicksupport-256.png
# The .ico uses 32-bit BMP entries up to 128 px and PNG for 256 px, which the
# .NET Framework csc.exe (/win32icon) accepts.
param([string]$Letter = "Q")

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$out = Split-Path -Parent $MyInvocation.MyCommand.Path

function Make([int]$s) {
    $b = New-Object Drawing.Bitmap $s, $s, ([Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [Drawing.Graphics]::FromImage($b)
    $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'; $g.Clear([Drawing.Color]::Transparent)
    $r = [single]($s * 0.18); $w = [single]($s - 1)
    $path = New-Object Drawing.Drawing2D.GraphicsPath
    $path.AddArc(0, 0, 2*$r, 2*$r, 180, 90); $path.AddArc($w-2*$r, 0, 2*$r, 2*$r, 270, 90)
    $path.AddArc($w-2*$r, $w-2*$r, 2*$r, 2*$r, 0, 90); $path.AddArc(0, $w-2*$r, 2*$r, 2*$r, 90, 90); $path.CloseFigure()
    $br = New-Object Drawing.Drawing2D.LinearGradientBrush ([Drawing.Point]::new(0,0)), ([Drawing.Point]::new($s,$s)), ([Drawing.Color]::FromArgb(255,25,118,210)), ([Drawing.Color]::FromArgb(255,13,71,161))
    $g.FillPath($br, $path)
    # monitor
    $pen = New-Object Drawing.Pen ([Drawing.Color]::White), ([single][Math]::Max(1, $s*0.06))
    $mx = $s*0.18; $my = $s*0.2; $mw = $s*0.64; $mh = $s*0.44
    $g.DrawRectangle($pen, [single]$mx, [single]$my, [single]$mw, [single]$mh)
    $g.FillRectangle([Drawing.Brushes]::White, [single]($s*0.44), [single]($my+$mh), [single]($s*0.12), [single]($s*0.1))
    $g.FillRectangle([Drawing.Brushes]::White, [single]($s*0.32), [single]($my+$mh+$s*0.1), [single]($s*0.36), [single]([Math]::Max(1,$s*0.05)))
    # letter
    $f = New-Object Drawing.Font 'Segoe UI', ([single]($s*0.30)), ([Drawing.FontStyle]::Bold), ([Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object Drawing.StringFormat; $sf.Alignment = 'Center'; $sf.LineAlignment = 'Center'
    $g.DrawString($Letter, $f, [Drawing.Brushes]::White, (New-Object Drawing.RectangleF ([single]$mx), ([single]$my), ([single]$mw), ([single]$mh)), $sf)
    $g.Dispose(); return $b
}

# 32-bit DIB entry of an .ico: BITMAPINFOHEADER (double height) + BGRA
# bottom-up + AND mask (all zero: transparency comes from the alpha channel)
function Dib([Drawing.Bitmap]$b) {
    $s = $b.Width
    $ms = New-Object IO.MemoryStream; $bw = New-Object IO.BinaryWriter $ms
    $bw.Write([uint32]40); $bw.Write([int32]$s); $bw.Write([int32]($s*2)); $bw.Write([uint16]1); $bw.Write([uint16]32)
    $bw.Write([uint32]0); $bw.Write([uint32]0); $bw.Write([int32]0); $bw.Write([int32]0); $bw.Write([uint32]0); $bw.Write([uint32]0)
    for ($y = $s - 1; $y -ge 0; $y--) {
        for ($x = 0; $x -lt $s; $x++) {
            $c = $b.GetPixel($x, $y)
            $bw.Write([byte]$c.B); $bw.Write([byte]$c.G); $bw.Write([byte]$c.R); $bw.Write([byte]$c.A)
        }
    }
    $maskRow = [int]([Math]::Ceiling($s / 32.0) * 4)
    $bw.Write((New-Object byte[] ($maskRow * $s)))
    $bw.Flush(); return ,$ms.ToArray()
}

$sizes = 16, 24, 32, 48, 64, 128, 256
$data = @()
foreach ($s in $sizes) {
    $b = Make $s
    if ($s -ge 256) {
        $ms = New-Object IO.MemoryStream; $b.Save($ms, [Drawing.Imaging.ImageFormat]::Png); $data += ,($ms.ToArray())
        $b.Save((Join-Path $out "quicksupport-256.png"), [Drawing.Imaging.ImageFormat]::Png)
    } else {
        $data += ,(Dib $b)
    }
    $b.Dispose()
}

$fs = [IO.File]::Create((Join-Path $out "quicksupport.ico")); $bw = New-Object IO.BinaryWriter $fs
$bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]$sizes.Count)
$off = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]; $d = $data[$i]; $dim = $(if ($s -ge 256) { 0 } else { $s })
    $bw.Write([byte]$dim); $bw.Write([byte]$dim); $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([uint16]1); $bw.Write([uint16]32); $bw.Write([uint32]$d.Length); $bw.Write([uint32]$off)
    $off += $d.Length
}
foreach ($d in $data) { $bw.Write($d) }
$bw.Close()
Get-ChildItem $out -Filter "quicksupport*" | Select-Object Name, Length
