; Lunelis installer (Inno Setup 6). Built by CI on a version tag from the
; PyInstaller folder dist\Lunelis:
;
;   iscc /DAppVersion=0.34.0 packaging\installer\lunelis.iss
;
; One per-user install - no administrator rights, no UAC prompt - into
; %LOCALAPPDATA%\Programs\Lunelis, with a Start menu entry and an entry in
; Settings > Apps like any other program.
;
; First install: a few setup pages (photo folders, where the catalog lives,
; tray and start-up, optional AI models). The answers go to
; %APPDATA%\Lunelis\setup.json, which Lunelis applies once at its next start
; (src/lunelis/firstrun.py) - the installer itself never touches the catalog.
;
; Updates come from inside Lunelis (Settings > Updates), not from this
; installer. Running it again over an existing install only replaces the
; program files: the setup pages are skipped and nothing is asked twice.
;
; The uninstaller lives in {app}\uninstall; the in-app updater carries that
; folder over when it swaps versions (updater.py) and updates the version
; shown in Settings > Apps.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Lunelis"
#endif
#define AppId "{{6E0C9C1B-2F4A-4C7E-9D52-1A7B3E5F8C21}"
#define Repo "https://github.com/AxialForge/Lunelis"

[Setup]
AppId={#AppId}
AppName=Lunelis
AppVersion={#AppVersion}
AppVerName=Lunelis {#AppVersion}
AppPublisher=AxialForge
AppPublisherURL={#Repo}
AppSupportURL={#Repo}/issues
AppUpdatesURL={#Repo}/releases
AppComments=A photo library for Windows that never changes your originals.
DefaultDirName={localappdata}\Programs\Lunelis
DefaultGroupName=Lunelis
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
WizardSizePercent=110
SetupIconFile=..\..\assets\icons\lunelis.ico
UninstallDisplayIcon={app}\Lunelis.exe
UninstallDisplayName=Lunelis
UninstallFilesDir={app}\uninstall
LicenseFile=LICENSE.txt
OutputDir=..\..\dist
OutputBaseFilename=Lunelis-v{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Lunelis
VersionInfoDescription=Lunelis setup

[Messages]
WelcomeLabel2=This will install Lunelis [ver] on your computer.%n%nLunelis catalogues the photos and videos you already have - on this PC, USB drives or a network drive - without ever changing, renaming or deleting them.%n%nNo administrator rights are needed. Later versions update from inside Lunelis.

[Tasks]
Name: desktopicon; Description: "Put a Lunelis shortcut on the desktop"; Flags: unchecked

[InstallDelete]
; The program folder holds only Lunelis: clear the old version's files so
; nothing stale is left behind (the data folder is never in here).
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; Updates (from inside Lunelis) add files this installer never listed; the
; program folder holds only Lunelis, so it goes as a whole.
Type: filesandordirs; Name: "{app}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Lunelis"; Filename: "{app}\Lunelis.exe"; Comment: "Your photo library"
Name: "{autodesktop}\Lunelis"; Filename: "{app}\Lunelis.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Lunelis.exe"; Description: "Start Lunelis now"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

var
  Existing: Boolean;            // Lunelis has been set up on this PC before
  PhotosPage: TWizardPage;
  FolderList: TNewCheckListBox;
  DataPage: TInputDirWizardPage;
  StartPage: TInputOptionWizardPage;
  ModelsPage: TInputOptionWizardPage;

{ --- helpers ------------------------------------------------------------------ }

function JsonStr(const S: String): String;
var
  R: String;
begin
  R := S;
  StringChangeEx(R, '\', '\\', True);
  StringChangeEx(R, '"', '\"', True);
  Result := '"' + R + '"';
end;

function JsonBool(B: Boolean): String;
begin
  if B then Result := 'true' else Result := 'false';
end;

function DefaultDataDir: String;
begin
  Result := ExpandConstant('{localappdata}\Lunelis');
end;

function SameFolder(const A, B: String): Boolean;
begin
  Result := CompareText(RemoveBackslashUnlessRoot(A), RemoveBackslashUnlessRoot(B)) = 0;
end;

function Inside(const Child, Parent: String): Boolean;
var
  P: String;
begin
  P := AddBackslash(Parent);
  Result := SameFolder(Child, Parent) or (CompareText(Copy(AddBackslash(Child), 1, Length(P)), P) = 0);
end;

{ Lunelis was set up here before: an install record, a library, or answers already applied. }
function FoundExisting: Boolean;
begin
  Result := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppId}_is1')
    or FileExists(DefaultDataDir + '\catalog.db')
    or FileExists(ExpandConstant('{userappdata}\Lunelis\location.json'))
    or FileExists(ExpandConstant('{userappdata}\Lunelis\setup.applied.json'));
end;

procedure AddFolder(const Path, Note: String; Checked: Boolean);
var
  I: Integer;
begin
  for I := 0 to FolderList.Items.Count - 1 do
    if SameFolder(FolderList.ItemSubItem[I], Path) then Exit;
  FolderList.AddCheckBox(Path, Note, 0, Checked, True, False, False, nil);
  FolderList.ItemSubItem[FolderList.Items.Count - 1] := Path;
end;

{ A folder probably holds photos when a quick look finds a picture or video in it, or one level down. }
function HasPhotos(const Folder: String; Depth: Integer): Boolean;
var
  F: TFindRec;
  Ext: String;
  Seen: Integer;
begin
  Result := False;
  Seen := 0;
  if FindFirst(AddBackslash(Folder) + '*', F) then
  try
    repeat
      Seen := Seen + 1;
      if (F.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
      begin
        Ext := Lowercase(ExtractFileExt(F.Name));
        if (Ext = '.jpg') or (Ext = '.jpeg') or (Ext = '.heic') or (Ext = '.png') or (Ext = '.arw')
          or (Ext = '.cr2') or (Ext = '.cr3') or (Ext = '.nef') or (Ext = '.raf') or (Ext = '.dng')
          or (Ext = '.mp4') or (Ext = '.mov') or (Ext = '.tif') then
        begin
          Result := True;
          Exit;
        end;
      end
      else if (Depth > 0) and (F.Name <> '.') and (F.Name <> '..') and (Copy(F.Name, 1, 1) <> '.') then
        if HasPhotos(AddBackslash(Folder) + F.Name, Depth - 1) then
        begin
          Result := True;
          Exit;
        end;
    until (not FindNext(F)) or (Seen > 300);
  finally
    FindClose(F);
  end;
end;

{ The user's Pictures folder, wherever it was moved to (Inno Setup has no constant for it). }
function PicturesFolder: String;
begin
  if not RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders',
    'My Pictures', Result) then
    Result := GetEnv('USERPROFILE') + '\Pictures';
end;

procedure SuggestFolders;
var
  F: TFindRec;
  Downloads, Pics: String;
begin
  Pics := PicturesFolder;
  if (Pics <> '') and DirExists(Pics) and HasPhotos(Pics, 2) then
    AddFolder(Pics, 'Pictures', True);
  if (GetEnv('OneDrive') <> '') and DirExists(GetEnv('OneDrive') + '\Pictures')
     and HasPhotos(GetEnv('OneDrive') + '\Pictures', 2) then
    AddFolder(GetEnv('OneDrive') + '\Pictures', 'OneDrive', True);
  Downloads := GetEnv('USERPROFILE') + '\Downloads';
  if FindFirst(Downloads + '\Takeout*', F) then
  try
    repeat
      if (F.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        AddFolder(Downloads + '\' + F.Name, 'Google Takeout', True);
    until not FindNext(F);
  finally
    FindClose(F);
  end;
end;

procedure AddClick(Sender: TObject);
var
  Dir: String;
begin
  Dir := '';
  if BrowseForFolder('Choose a folder of photos (a drive, a folder, or a network folder):', Dir, False) then
    AddFolder(Dir, '', True);
end;

procedure RemoveClick(Sender: TObject);
begin
  if FolderList.ItemIndex >= 0 then
    FolderList.Items.Delete(FolderList.ItemIndex);
end;

{ --- pages -------------------------------------------------------------------- }

procedure InitializeWizard;
var
  AddBtn, RemoveBtn: TNewButton;
  Note: TNewStaticText;
begin
  Existing := FoundExisting;

  PhotosPage := CreateCustomPage(wpSelectDir, 'Your photos',
    'Which folders hold your photos? Lunelis only reads them.');
  FolderList := TNewCheckListBox.Create(PhotosPage);
  FolderList.Parent := PhotosPage.Surface;
  FolderList.SetBounds(0, 0, PhotosPage.SurfaceWidth, ScaleY(150));
  AddBtn := TNewButton.Create(PhotosPage);
  AddBtn.Parent := PhotosPage.Surface;
  AddBtn.Caption := 'Add a folder...';
  AddBtn.SetBounds(0, FolderList.Top + FolderList.Height + ScaleY(8), ScaleX(110), ScaleY(24));
  AddBtn.OnClick := @AddClick;
  RemoveBtn := TNewButton.Create(PhotosPage);
  RemoveBtn.Parent := PhotosPage.Surface;
  RemoveBtn.Caption := 'Remove';
  RemoveBtn.SetBounds(AddBtn.Left + AddBtn.Width + ScaleX(8), AddBtn.Top, ScaleX(80), ScaleY(24));
  RemoveBtn.OnClick := @RemoveClick;
  Note := TNewStaticText.Create(PhotosPage);
  Note.Parent := PhotosPage.Surface;
  Note.WordWrap := True;
  Note.SetBounds(0, AddBtn.Top + AddBtn.Height + ScaleY(10), PhotosPage.SurfaceWidth, ScaleY(48));
  Note.Caption := 'Folders on this PC, USB drives and network folders (such as \\nas\photos) all work. '
    + 'Lunelis reads them in the background after it starts; you can add more any time with Library > Add folder.';
  SuggestFolders;

  DataPage := CreateInputDirPage(PhotosPage.ID, 'Where Lunelis keeps its catalog',
    'Ratings, edits, albums, thumbnails and catalog backups live in one data folder.',
    'Lunelis never writes into your photo folders. The data folder needs about 25 KB per photo for '
    + 'thumbnails (4 GB for 160,000 photos) and must be on a drive in this PC, not a network folder.'#13#10#13#10
    + 'Choose a folder that holds a Lunelis catalog from another PC to keep using that library.',
    False, 'Lunelis');
  DataPage.Add('');
  DataPage.Values[0] := DefaultDataDir;

  StartPage := CreateInputOptionPage(DataPage.ID, 'Start-up and the tray',
    'How Lunelis runs in the background.',
    'With the tray on, closing the window keeps Lunelis running quietly, so it can offer to import '
    + 'when you plug in a memory card, phone or USB stick.', False, False);
  StartPage.Add('Keep Lunelis in the tray when its window is closed, and watch for memory cards');
  StartPage.Add('Start Lunelis with Windows (it opens in the tray)');
  StartPage.Values[0] := True;
  StartPage.Values[1] := False;

  ModelsPage := CreateInputOptionPage(StartPage.ID, 'Optional downloads',
    'Small AI models that run only on this PC.',
    'Lunelis downloads the ones you tick in the background after it starts, and checks each against its '
    + 'known fingerprint before using it. Nothing about your photos is sent anywhere. Everything else '
    + 'works without them, and Settings can download them later.', False, False);
  ModelsPage.Add('Scene tags: suggests what is in each photo, and powers Find similar (155 MB)');
  ModelsPage.Add('Faces: finds the people in your photos so you can name them once (39 MB)');
  ModelsPage.Add('Subject masks: select the person or thing in one click when editing (44 MB)');
  ModelsPage.Add('Sky masks: select the sky in one click when editing (176 MB)');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Set up before: only the program files are replaced. }
  Result := Existing and ((PageID = PhotosPage.ID) or (PageID = DataPage.ID)
    or (PageID = StartPage.ID) or (PageID = ModelsPage.ID));
end;

{ The program folder must hold Lunelis and nothing else: updates replace it and the uninstaller removes it. }
function FolderIsFree(const Dir: String): Boolean;
var
  F: TFindRec;
begin
  Result := True;
  if not DirExists(Dir) or FileExists(AddBackslash(Dir) + 'Lunelis.exe') then Exit;
  if FindFirst(AddBackslash(Dir) + '*', F) then
  try
    repeat
      if (F.Name <> '.') and (F.Name <> '..') then
      begin
        Result := False;
        Exit;
      end;
    until not FindNext(F);
  finally
    FindClose(F);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  D: String;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and not FolderIsFree(WizardDirValue) then
  begin
    MsgBox('Choose an empty folder (or the one Lunelis is already in). Updates replace the whole program '
      + 'folder and uninstalling removes it, so nothing else may live there.' + #13#10#13#10
      + WizardDirValue + ' already holds other files.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  if CurPageID = DataPage.ID then
  begin
    D := DataPage.Values[0];
    if Copy(D, 1, 2) = '\\' then
    begin
      MsgBox('The data folder has to be on a drive in this PC - the catalog can''t live on a network '
        + 'folder. Your photos can be anywhere.', mbError, MB_OK);
      Result := False;
    end
    else if Inside(D, WizardDirValue) or Inside(WizardDirValue, D) then
    begin
      MsgBox('Choose a data folder outside the program folder (' + WizardDirValue + '): updates replace '
        + 'the program folder.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
var
  I, N: Integer;
  S: String;
begin
  S := MemoDirInfo + NewLine + NewLine;
  if Existing then
    S := S + 'Lunelis is already set up on this PC: only the program files are replaced. '
      + 'Your library, settings and photos stay as they are.'
  else
  begin
    N := 0;
    for I := 0 to FolderList.Items.Count - 1 do
      if FolderList.Checked[I] then N := N + 1;
    S := S + 'Photo folders:' + NewLine;
    if N = 0 then S := S + Space + '(none yet - add them in Lunelis)' + NewLine;
    for I := 0 to FolderList.Items.Count - 1 do
      if FolderList.Checked[I] then S := S + Space + FolderList.ItemSubItem[I] + NewLine;
    S := S + NewLine + 'Data folder:' + NewLine + Space + DataPage.Values[0] + NewLine;
    if StartPage.Values[0] then S := S + NewLine + 'Tray: on' else S := S + NewLine + 'Tray: off';
    if StartPage.Values[1] then S := S + NewLine + 'Start with Windows: on';
    if ModelsPage.Values[0] or ModelsPage.Values[1] or ModelsPage.Values[2] or ModelsPage.Values[3] then
      S := S + NewLine + 'Optional downloads: chosen models download after Lunelis starts';
  end;
  if MemoTasksInfo <> '' then S := S + NewLine + NewLine + MemoTasksInfo;
  Result := S;
end;

{ --- the answers -------------------------------------------------------------- }

procedure WriteSetup;
var
  I: Integer;
  Folders, Models, Data, Json: String;
begin
  Folders := '';
  for I := 0 to FolderList.Items.Count - 1 do
    if FolderList.Checked[I] then
    begin
      if Folders <> '' then Folders := Folders + ', ';
      Folders := Folders + JsonStr(FolderList.ItemSubItem[I]);
    end;
  Models := '';
  if ModelsPage.Values[0] then Models := Models + '"scene"';
  if ModelsPage.Values[1] then begin if Models <> '' then Models := Models + ', '; Models := Models + '"subject"'; end;
  if ModelsPage.Values[2] then begin if Models <> '' then Models := Models + ', '; Models := Models + '"sky"'; end;
  if ModelsPage.Values[3] then begin if Models <> '' then Models := Models + ', '; Models := Models + '"faces"'; end;
  Data := DataPage.Values[0];
  if SameFolder(Data, DefaultDataDir) then Data := '';
  Json := '{"version": 1, "data_dir": ' + JsonStr(Data)
    + ', "folders": [' + Folders + ']'
    + ', "tray": ' + JsonBool(StartPage.Values[0])
    + ', "start_with_windows": ' + JsonBool(StartPage.Values[0] and StartPage.Values[1])
    + ', "models": [' + Models + ']'
    + ', "from": "installer {#AppVersion}"}';
  ForceDirectories(ExpandConstant('{userappdata}\Lunelis'));
  SaveStringsToUTF8File(ExpandConstant('{userappdata}\Lunelis\setup.json'), [Json], False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not Existing then
    WriteSetup;
end;

{ --- uninstall ---------------------------------------------------------------- }

function InitializeUninstall: Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data, Loc, Script: String;
  Lines: TArrayOfString;
  P, Q: Integer;
  Code: Integer;
begin
  if CurUninstallStep <> usUninstall then Exit;
  { The Start-with-Windows entry points at a program that's going away. }
  RegDeleteValue(HKCU, RunKey, 'Lunelis');

  Data := DefaultDataDir;
  Loc := ExpandConstant('{userappdata}\Lunelis\location.json');
  if LoadStringsFromFile(Loc, Lines) and (GetArrayLength(Lines) > 0) then
  begin
    { "data_dir": "D:\\Lunelis" - a small read, no JSON parser in here. }
    for Q := 0 to GetArrayLength(Lines) - 1 do
    begin
      P := Pos('"data_dir": "', Lines[Q]);
      if P > 0 then
      begin
        Data := Copy(Lines[Q], P + 13, Length(Lines[Q]));
        Data := Copy(Data, 1, Pos('"', Data) - 1);
        StringChangeEx(Data, '\\', '\', True);
      end;
    end;
  end;

  if UninstallSilent or not DirExists(Data) then Exit;
  if MsgBox('Also remove your Lunelis library data?' + #13#10#13#10 + Data + #13#10#13#10
      + 'That is the catalog (ratings, edits, albums, tags), thumbnails and catalog backups. It goes to the '
      + 'Recycle Bin. Your photos are never touched either way.' + #13#10#13#10
      + 'Choose No to keep it, so a later install picks up where you left off.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    Script := 'Add-Type -AssemblyName Microsoft.VisualBasic; '
      + '[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(''' + Data + ''', '
      + '''OnlyErrorDialogs'', ''SendToRecycleBin'')';
    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' + Script + '"', '',
      SW_HIDE, ewWaitUntilTerminated, Code);
    DelTree(ExpandConstant('{userappdata}\Lunelis'), True, True, True);
  end;
end;
