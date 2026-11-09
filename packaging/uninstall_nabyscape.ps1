# Удаление NabyScape вместе с SysMon Widget: остановить, убрать автозапуск, данные и сохранённые ключи.
param([string]$AppDir)
$ErrorActionPreference = 'SilentlyContinue'
$local = [Environment]::GetFolderPath('LocalApplicationData')
$dirs = @(
    (Join-Path $AppDir 'third_party\nabyscape'),
    (Join-Path $local 'sysmon-widget\nabyscape'),
    (Join-Path $local 'NabyScape'),
    (Join-Path $local 'LumaIsland'),
    (Join-Path $local 'NabyScape-Updates')
)

# 1. Мягко попросить закрыться, затем добить процессы из наших папок
try { $ev = [Threading.EventWaitHandle]::OpenExisting('Local\LumaIsland.Exit'); $null = $ev.Set(); $ev.Dispose() } catch {}
Start-Sleep -Milliseconds 1500
foreach ($p in Get-Process) {
    $path = $null; try { $path = $p.Path } catch {}
    if (-not $path) { continue }
    foreach ($d in $dirs) {
        if ($path.StartsWith($d.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { Stop-Process -Id $p.Id -Force; break }
    }
}
Start-Sleep -Milliseconds 500

# 2. Автозапуск
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
foreach ($name in 'NabyScape','LumaIsland','BillyNabilaIsland') {
    $v = (Get-ItemProperty -LiteralPath $run -Name $name).$name
    if ($v -and ($dirs | Where-Object { $v.Contains($_) })) { Remove-ItemProperty -LiteralPath $run -Name $name }
}

# 3. Сохранённые ключи ИИ (диспетчер учётных данных Windows)
try {
    Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;using System.Collections.Generic;
public static class NabyCreds {
  [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct C{public uint F,T;public string N,Cm;public System.Runtime.InteropServices.ComTypes.FILETIME W;public uint S;public IntPtr B;public uint P,A;public IntPtr At;public string Al,U;}
  [DllImport("advapi32",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CredEnumerateW(string f,uint fl,out uint n,out IntPtr l);
  [DllImport("advapi32",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CredDeleteW(string t,uint ty,uint fl);
  [DllImport("advapi32")] static extern void CredFree(IntPtr p);
  public static int Remove(){uint n;IntPtr l;if(!CredEnumerateW("NabyScape/*",0,out n,out l))return 0;var names=new List<string>();
    try{for(int i=0;i<n;i++){var c=(C)Marshal.PtrToStructure(Marshal.ReadIntPtr(l,i*IntPtr.Size),typeof(C));if(c.T==1&&c.N!=null&&c.N.StartsWith("NabyScape/"))names.Add(c.N);}}finally{CredFree(l);}
    foreach(var x in names)CredDeleteW(x,1,0);return names.Count;}
}
'@
    $null = [NabyCreds]::Remove()
} catch {}

# 4. Файлы и настройки (без перехода по ссылкам/junction)
foreach ($d in $dirs) {
    if (Test-Path -LiteralPath $d) {
        $item = Get-Item -LiteralPath $d -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        Remove-Item -LiteralPath $d -Recurse -Force
    }
}
