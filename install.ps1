# 把本仓库的技能安装到本机 DSH。
#
# 用法：
#   .\install.ps1                  # 安装全部技能
#   .\install.ps1 -SkipHeavy       # 跳过 shuitu-writing-skill 的 452 MB 知识库图片
#   .\install.ps1 -Only neat-freak # 只装指定的技能（可多个）
#   .\install.ps1 -List            # 只列出本仓库包含的技能

[CmdletBinding()]
param(
  [string[]]$Only,
  [switch]$SkipHeavy,
  [switch]$List,
  [string]$Destination
)

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
if (-not $Destination) {
  if ($env:DSH_HOME) {
    $Destination = Join-Path $env:DSH_HOME 'skills'
  } else {
    $Destination = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
  }
}

# 含 SKILL.md 的目录才算技能
$skills = Get-ChildItem $repoRoot -Directory -Force |
  Where-Object { Test-Path (Join-Path $_.FullName 'SKILL.md') }

if ($List) {
  '本仓库包含的技能：'
  foreach ($s in $skills) {
    $mb = [math]::Round((Get-ChildItem $s.FullName -Recurse -File -Force |
      Measure-Object Length -Sum).Sum / 1MB, 2)
    '  {0,-26} {1,8:N2} MB' -f $s.Name, $mb
  }
  '目标目录：' + $Destination
  return
}

if ($Only) {
  $skills = $skills | Where-Object { $Only -contains $_.Name }
  if (-not $skills) { throw "没有匹配的技能：$($Only -join ', ')" }
}

"目标目录：$Destination"
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

foreach ($s in $skills) {
  $dest = Join-Path $Destination $s.Name
  $sizeMB = [math]::Round((Get-ChildItem $s.FullName -Recurse -File -Force |
    Measure-Object Length -Sum).Sum / 1MB, 2)

  $exclude = @()
  if ($SkipHeavy -and $s.Name -eq 'shuitu-writing-skill') {
    $exclude = @('.assets', '_meta', '.cache')
  }

  "安装 {0}（{1:N2} MB）..." -f $s.Name, $sizeMB
  if ($exclude.Count -gt 0) { "  排除：$($exclude -join ', ')" }

  $rcArgs = @($s.FullName, $dest, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/R:1', '/W:1')
  if ($exclude.Count -gt 0) { $rcArgs += '/XD'; $rcArgs += $exclude }
  & robocopy @rcArgs | Out-Null

  # robocopy 退出码 < 8 都算成功
  if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（退出码 $LASTEXITCODE）：$($s.Name)" }
  $LASTEXITCODE = 0
}

"完成。重启 DSH 后生效。"
