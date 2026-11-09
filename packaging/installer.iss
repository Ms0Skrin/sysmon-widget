; Установщик SysMon Widget (Inno Setup 6). Собирается из build.ps1.
#define AppVer GetEnv("SYSMON_VERSION")

[Setup]
AppId={{8C5E2A41-6B1F-4D7A-9C3E-5F2B7A9D1E44}
AppName=SysMon Widget
AppVersion={#AppVer}
AppVerName=SysMon Widget {#AppVer}
AppPublisher=Ms0Skrin
AppPublisherURL=https://github.com/Ms0Skrin/sysmon-widget
DefaultDirName={autopf}\SysMon Widget
DefaultGroupName=SysMon Widget
DisableProgramGroupPage=yes
#ifdef PREVIEW
PrivilegesRequired=lowest
#else
PrivilegesRequired=admin
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=SysMonWidget-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\SysMonWidget.exe
UninstallDisplayName=SysMon Widget
Compression=lzma2/ultra64
SolidCompression=yes
; оформление в стиле приложения: светлая/тёмная тема по системе, фирменные картинки и цвета
WizardStyle=modern dynamic hidebevels
WizardBackColor=#f3f4f7
WizardBackColorDynamicDark=#16181d
WizardImageFile=..\build\art\wizard_dark_100.bmp,..\build\art\wizard_dark_125.bmp,..\build\art\wizard_dark_150.bmp,..\build\art\wizard_dark_200.bmp
WizardSmallImageFile=..\build\art\small_dark_100.png,..\build\art\small_dark_125.png,..\build\art\small_dark_150.png,..\build\art\small_dark_200.png
WizardImageBackColor=#16181d
WizardImageBackColorDynamicDark=#16181d
CloseApplications=force
RestartApplications=no
UsedUserAreasWarning=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
ru.WelcomeLabel1=Добро пожаловать в SysMon Widget
ru.WelcomeLabel2=Будут установлены панели-виджеты для рабочего стола: мониторинг процессора, видеокарты и памяти, датчики температуры и вентиляторов, панель приложений, «остров» NabyScape на русском и прозрачная панель задач.%n%nВсё нужное уже внутри — ничего докачивать не придётся. Права администратора понадобятся один раз: для драйвера датчиков и запуска датчиков без запросов.
ru.FinishedHeadingLabel=SysMon Widget установлен
ru.FinishedLabel=Панели появятся на рабочем столе. Правый клик по панели — её меню, значок в трее — настройки и обновления.

[Tasks]
Name: "autostart"; Description: "Запускать SysMon Widget вместе с Windows"
Name: "sensors"; Description: "Датчики температуры и вентиляторов: драйвер PawnIO и запуск датчиков без запроса прав"

[Files]
Source: "..\dist\SysMonWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall_nabyscape.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\third_party\nabyscape\*"; DestDir: "{app}\third_party\nabyscape"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\third_party\translucenttb\*"; DestDir: "{app}\third_party\translucenttb"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\third_party\pawnio\PawnIO_setup.exe"; DestDir: "{app}\third_party\pawnio"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SysMon Widget"; Filename: "{app}\SysMonWidget.exe"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SysMonWidget"; ValueData: """{app}\SysMonWidget.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\third_party\pawnio\PawnIO_setup.exe"; Parameters: "-install -silent"; StatusMsg: "Устанавливаю драйвер датчиков PawnIO…"; Tasks: sensors; Flags: waituntilterminated runhidden
Filename: "{app}\SysMonSensors.exe"; Parameters: "--install-task"; StatusMsg: "Настраиваю запуск датчиков без запроса прав…"; Tasks: sensors; Flags: waituntilterminated runhidden
Filename: "{app}\SysMonWidget.exe"; Description: "Запустить SysMon Widget"; Flags: postinstall nowait runasoriginaluser

[UninstallRun]
; NabyScape удаляется вместе с виджетом: процесс, автозапуск, ключи ИИ, настройки и скачанные копии
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\uninstall_nabyscape.ps1"" -AppDir ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveNabyScape"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM SysMonWidget.exe"; Flags: runhidden; RunOnceId: "KillWidget"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM SysMonSensors.exe"; Flags: runhidden; RunOnceId: "KillSensors"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM TranslucentTB.exe"; Flags: runhidden; RunOnceId: "KillTTB"
Filename: "{app}\SysMonSensors.exe"; Parameters: "--remove-task"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"
