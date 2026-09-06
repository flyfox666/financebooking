; ============================================================
; 有数 LedgerAI · Windows 安装程序脚本（Inno Setup 7）
; ------------------------------------------------------------
; 输入：dist\YouShuLedgerAI\（PyInstaller onedir 产物）
; 输出：dist\installer\有数LedgerAI-setup-v<版本>.exe
; 编译：build\tools\inno\ISCC.exe installer\YouShuLedgerAI.iss
;
; 交付约定（已与用户确认）：
;   1A 空模板分发 —— 安装包不含任何账套数据，用户首次启动自建库 + 默认 admin
;   2A 卸载保留数据 —— 卸载只删程序目录，账套数据留在用户 LOCALAPPDATA
; ============================================================

#define MyAppName "有数 LedgerAI"
#define MyAppNameEn "YouShuLedgerAI"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "有数 LedgerAI"
#define MyAppExeName "YouShuLedgerAI.exe"
#define MyAppURL "http://127.0.0.1:8000/app"

[Setup]
; AppId 决定"同一个软件"的身份：升级安装靠它识别旧版本，切勿随意改动
AppId={{7C3F1A52-9E48-4B6D-A1C0-5E8D2B7F4A31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL={#MyAppURL}

; 装到 Program Files（传统财务软件形态，需管理员权限）
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
PrivilegesRequired=admin
DisableProgramGroupPage=yes
DisableWelcomePage=no

; 输出（一律用 {#SourcePath} 绝对定位，避免相对路径解析歧义）
SourceDir={#SourcePath}..
OutputDir={#SourcePath}..\dist\installer
OutputBaseFilename={#MyAppNameEn}-setup-v{#MyAppVersion}

; 压缩：283MB 源文件，lzma2 固态压缩后约 120MB
Compression=lzma2/max
SolidCompression=yes

; 仅 64 位（Python 3.12 + OpenCV 均为 x64），最低 Windows 10
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; 安装/卸载时若程序在运行，用 Restart Manager 提示关闭（不静默杀进程）
CloseApplications=yes
RestartApplications=no

WizardStyle=modern
WizardSizePercent=110
ShowLanguageDialog=no
UsePreviousAppDir=yes
UsePreviousGroup=yes

; 安装向导中展示"使用前必读"（含数据目录、默认账号、常见问题）
; 源文件放 installer\ 而非 dist\：dist 被 gitignore，clone 后需能完整重建交付包
; 注：{#SourcePath} 已指向本 .iss 所在的 installer\ 目录，勿再拼一层 installer\
InfoBeforeFile={#SourcePath}使用前必读.txt

; 控制面板「程序和功能」里的显示名
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
; compiler: 是 Inno 内置前缀（指向编译器自身目录），脚本因此不依赖 Inno 的安装位置
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; PyInstaller 产物整目录（含 _internal 下的解释器、依赖、静态前端、迁移脚本）
Source: "dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; 使用说明随程序一起装，便于用户从开始菜单直接打开
Source: "installer\使用前必读.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\使用前必读"; Filename: "{app}\使用前必读.txt"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; 注：不建「打开数据文件夹」快捷方式。数据目录属于「运行程序的那个 Windows 用户」，
; 而本脚本以 admin 模式安装，{localappdata} 会解析成安装者（可能是管理员）的目录，
; 与日常使用者的数据目录不一致 —— 快捷方式反而误导。数据位置见「使用前必读」。

[Run]
; 安装完成页勾选"立即启动"：shellexec + nowait 让程序脱离安装器进程树独立运行
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; 只清程序目录自身的空壳；账套数据不在这里，天然不会被删（2A）
Type: filesandordirs; Name: "{app}"

[Code]
// 卸载完成后明确告知数据仍在，避免用户误以为账套被清空
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox(
      '程序已卸载。' + #13#10 + #13#10 +
      '你的账套数据【仍完整保留】在：' + #13#10 +
      'C:\Users\<你的用户名>\AppData\Local\有数LedgerAI\data' + #13#10 + #13#10 +
      '内含全部凭证、发票与附件。重新安装本软件会自动沿用这些数据。' + #13#10 +
      '如确认要彻底清除，请手动删除上述文件夹。',
      mbInformation, MB_OK);
end;

// 安装完成页补充数据落盘位置说明（首次安装用户最常问的问题）
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      WizardForm.FinishedLabel.Caption + #13#10 + #13#10 +
      '首次启动会自动建库，默认账号 admin / admin123456，登录后请立即修改密码。' + #13#10 +
      '账套数据保存在你的用户目录（AppData\Local\有数LedgerAI\data），升级或重装不会丢失。';
end;
