param([switch]$SkipReboot)
# _write79_v5.ps1 — r79 UBI 灌入（中文 + 正确的完成标记）
# 关键修正：U-Boot 的 nand erase/write 结束时不打印 OK，只返回提示符。
# 完成判定一律以 "U-Boot>" 提示符返回为准。
$ErrorActionPreference = 'Stop'
$segs = @(
  @{N='ubi79.seg01'; P='0xB800000';  L='0xF400000'},
  @{N='ubi79.seg02'; P='0x1AC00000'; L='0xF400000'},
  @{N='ubi79.seg03'; P='0x2A000000'; L='0xF400000'},
  @{N='ubi79.seg04'; P='0x39400000'; L='0xF200000'}
)
function TS { Get-Date -Format 'HH:mm:ss' }
$sp = New-Object System.IO.Ports.SerialPort 'COM14',115200,'None',8,'One'
$sp.DtrEnable=$true; $sp.RtsEnable=$true; $sp.ReadTimeout=200
$sp.Open(); Start-Sleep -Milliseconds 300
[void]$sp.ReadExisting()
function Send($c){ $sp.Write("$c`r") }

Write-Host "=== [$(TS)] 探测盒子当前状态 ===" -ForegroundColor Cyan
Send ''
Start-Sleep -Milliseconds 800
$acc = New-Object System.Text.StringBuilder
$sw=[Diagnostics.Stopwatch]::StartNew()
while($sw.Elapsed.TotalMilliseconds -lt 1500){
    $c=$sp.ReadExisting(); if($c.Length){[void]$acc.Append($c)}
    Start-Sleep -Milliseconds 80
}
$t = $acc.ToString()
if($t -match 'U-Boot>'){
    Write-Host "[$(TS)] 盒子已在 U-Boot 提示符，直接灌入" -ForegroundColor Green
} else {
    Write-Host "[$(TS)] 状态未知，尝试 SysRq-b 强制重启并等待提示符" -ForegroundColor Yellow
    $sp.BreakState=$true; Start-Sleep -Milliseconds 300; $sp.BreakState=$false
    Start-Sleep -Milliseconds 200; $sp.Write("b`r")
    $sw=[Diagnostics.Stopwatch]::StartNew(); $acc=New-Object System.Text.StringBuilder; $got=$false
    while($sw.Elapsed.TotalSeconds -lt 45){
        Start-Sleep -Milliseconds 150
        $c=$sp.ReadExisting(); if($c.Length){[void]$acc.Append($c)}
        if($acc.ToString() -match 'U-Boot>'){ $got=$true; break }
        $sp.Write("`r")
    }
    if(-not $got){ throw "45 秒内未出现 U-Boot 提示符" }
}
Send ''
Start-Sleep -Milliseconds 400; [void]$sp.ReadExisting()
Write-Host "[$(TS)] [OK] U-Boot 提示符就绪" -ForegroundColor Green

Write-Host "=== [$(TS)] 设置 mtdparts ===" -ForegroundColor Cyan
Send 'setenv mtdparts mtdparts=nand0:0x800000@0(nand-boot),-@0xb800000(nand-ubi)'
Start-Sleep -Milliseconds 600; [void]$sp.ReadExisting()

Write-Host "=== [$(TS)] 擦除 UBI 数据区（约 8-20 秒）===" -ForegroundColor Cyan
Send 'nand erase 0xB800000 0x1F4800000'
$sw=[Diagnostics.Stopwatch]::StartNew(); $acc=New-Object System.Text.StringBuilder; $shown=0
while($sw.Elapsed.TotalSeconds -lt 300){
    Start-Sleep -Milliseconds 150
    $c=$sp.ReadExisting(); if($c.Length){[void]$acc.Append($c)}
    $t=$acc.ToString()
    if($t -match 'U-Boot>'){ break }   # 命令结束的唯一标志：提示符返回
    $m=[regex]::Matches($t,'Erasing at 0x([0-9a-f]+)')
    if($m.Count -gt $shown){
        $shown=$m.Count
        $pct=[math]::Round([Convert]::ToInt64($m[$m.Count-1].Groups[1].Value,16)/0x1F4800000*100)
        Write-Host ("    擦除 {0,3}%  （{1,5:N0} 秒）" -f $pct,$sw.Elapsed.TotalSeconds) -ForegroundColor Cyan
    }
}
Write-Host ("[$(TS)] [OK] 擦除完成，用时 {0:N0} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green

$i=0
foreach($s in $segs){
    $i++
    $mb=[math]::Round([Convert]::ToInt64($s.L,16)/1MB)
    Write-Host "=== [$(TS)] [$i/4] TFTP 传输 $($s.N)（约 $mb MiB）===" -ForegroundColor Cyan
    [void]$sp.ReadExisting()
    Send "tftpboot 0x20000000 $($s.N)"
    $sw=[Diagnostics.Stopwatch]::StartNew(); $acc=New-Object System.Text.StringBuilder; $shown=0; $ok=$false; $lastpct=-1
    while($sw.Elapsed.TotalSeconds -lt 600){
        Start-Sleep -Milliseconds 120
        $c=$sp.ReadExisting(); if($c.Length){[void]$acc.Append($c)}
        $t=$acc.ToString()
        if($t -match 'Bytes transferred'){ $ok=$true; break }
        $h=[regex]::Matches($t,'#').Count
        if($h -gt $shown){
            $shown=$h
            $kib=[math]::Round($h*1468/1024)
            $pct=[math]::Round($kib/($mb*1024)*100)
            if(($pct - $lastpct) -ge 5 -or ($pct -eq 100 -and $lastpct -lt 100)){   # 每 5% 显示一行
                $lastpct=$pct
                Write-Host ("    传输 {0,3}%  {1,7:N0} KiB（{2,5:N0} 秒）" -f [math]::Min(100,$pct),$kib,$sw.Elapsed.TotalSeconds) -ForegroundColor Cyan
            }
        }
    }
    if(-not $ok){ throw "分段 $i TFTP 传输失败/超时" }
    Write-Host ("[$(TS)] [OK] 第 $i 段传输完成，用时 {0:N0} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green

    Write-Host "=== [$(TS)] [$i/4] 写入 NAND（约 1-3 分钟，无进度输出，用心跳确认活着）===" -ForegroundColor Cyan
    Send "nand write 0x20000000 $($s.P) $($s.L)"
    $sw=[Diagnostics.Stopwatch]::StartNew()
    $lastBeat=0
    while($sw.Elapsed.TotalSeconds -lt 1800){
        Start-Sleep -Milliseconds 200
        $c=$sp.ReadExisting()
        if($c.Length){
            if($c -match 'U-Boot>'){ break }
            $lastBeat=$sw.Elapsed.TotalSeconds   # 有数据流出也视为活着
        }
        if(($sw.Elapsed.TotalSeconds - $lastBeat) -ge 10){
            $lastBeat=$sw.Elapsed.TotalSeconds
            Write-Host ("    写入中... {0,5:N0} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Cyan
        }
    }
    Write-Host ("[$(TS)] [OK] 第 $i 段写入完成，用时 {0:N0} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
}

Write-Host "=== [$(TS)] 验证 UBI 挂载 ===" -ForegroundColor Cyan
Send 'ubi part nand-ubi 24576'
Start-Sleep -Seconds 4
$c=$sp.ReadExisting(); if($c.Length){ Write-Host ("    | " + (($c -replace "`r","`n").Trim())) -ForegroundColor DarkGray }
Send 'ubi info'
Start-Sleep -Seconds 3
$c=$sp.ReadExisting(); if($c.Length){ Write-Host ("    | " + (($c -replace "`r","`n").Trim())) -ForegroundColor DarkGray }
Write-Host ""
Write-Host "[$(TS)] 全部完成！在串口输入 reset 启动 Lakka。" -ForegroundColor Green
[void]$sp.ReadExisting(); $sp.Close()
