# Built-in Windows WinRT audio-only capture. No UI, PortAudio or DirectShow.
param([Parameter(Mandatory=$true)][string]$OutputWav)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Capture.MediaCapture,Windows.Media.Capture,ContentType=WindowsRuntime]
$null=[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime]
$null=[Windows.Devices.Enumeration.DeviceAccessInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime]
$null=[Windows.Media.Devices.MediaDevice,Windows.Media.Devices,ContentType=WindowsRuntime]
$null=[Windows.Media.MediaProperties.MediaEncodingProfile,Windows.Media.MediaProperties,ContentType=WindowsRuntime]
$null=[Windows.Storage.StorageFolder,Windows.Storage,ContentType=WindowsRuntime]
Add-Type -TypeDefinition @'
public static class ClearQueryNativeInput {
    public static System.Threading.Tasks.Task<string> Read() {
        return System.Threading.Tasks.Task.Run<string>(() => System.Console.ReadLine());
    }
}
'@
function Send-Event($value) {
    [Console]::WriteLine(($value | ConvertTo-Json -Depth 8 -Compress))
    [Console]::Out.Flush()
}
function Await-Operation($operation,$type) {
    $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    return $method.MakeGenericMethod($type).Invoke($null,@($operation)).GetAwaiter().GetResult()
}
function Await-Action($action) {
    $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and !$_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
    } | Select-Object -First 1
    $null=$method.Invoke($null,@($action)).GetAwaiter().GetResult()
}
$capture=$null
$recording=$false
try {
    $access=[Windows.Devices.Enumeration.DeviceAccessInformation]::CreateFromDeviceClass(
        [Windows.Devices.Enumeration.DeviceClass]::AudioCapture).CurrentStatus.ToString()
    if($access -like 'Denied*') {throw [UnauthorizedAccessException]::new('Windows microphone access is denied.')}
    $id=[Windows.Media.Devices.MediaDevice]::GetDefaultAudioCaptureId([Windows.Media.Devices.AudioDeviceRole]::Default)
    if(!$id) {throw 'No Windows default audio capture device.'}
    $devices=Await-Operation ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync(
        [Windows.Devices.Enumeration.DeviceClass]::AudioCapture)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
    $device=$devices | Where-Object {$_.Id -eq $id} | Select-Object -First 1
    if(!$device -or !$device.IsEnabled) {throw 'The Windows default microphone is unavailable.'}
    $settings=New-Object Windows.Media.Capture.MediaCaptureInitializationSettings
    $settings.StreamingCaptureMode=[Windows.Media.Capture.StreamingCaptureMode]::Audio
    $settings.AudioDeviceId=$id
    $capture=New-Object Windows.Media.Capture.MediaCapture
    Await-Action ($capture.InitializeAsync($settings))
    $native=$capture.AudioDeviceController.GetMediaStreamProperties([Windows.Media.Capture.MediaStreamType]::Audio)
    $format=@{sample_rate=[int]$native.SampleRate;channels=[int]$native.ChannelCount;bits=[int]$native.BitsPerSample;subtype=$native.Subtype}
    if($format.sample_rate -lt 8000 -or $format.sample_rate -gt 192000 -or $format.channels -lt 1 -or $format.channels -gt 8) {throw 'Unsupported native audio format.'}
    Send-Event @{event='READY';backend='windows_mediacapture';device=$device.Name;device_id=$id;access=$access;native_format=$format}
    if([Console]::ReadLine() -ne 'START') {throw 'Recording cancelled before START.'}
    $folder=Await-Operation ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync(
        [IO.Path]::GetDirectoryName($OutputWav))) ([Windows.Storage.StorageFolder])
    $file=Await-Operation ($folder.CreateFileAsync([IO.Path]::GetFileName($OutputWav),
        [Windows.Storage.CreationCollisionOption]::FailIfExists)) ([Windows.Storage.StorageFile])
    $profile=[Windows.Media.MediaProperties.MediaEncodingProfile]::CreateWav([Windows.Media.MediaProperties.AudioEncodingQuality]::Auto)
    $profile.Audio=[Windows.Media.MediaProperties.AudioEncodingProperties]::CreatePcm(
        [uint32]$format.sample_rate,[uint32]$format.channels,16)
    Await-Action ($capture.StartRecordToStorageFileAsync($profile,$file))
    $recording=$true
    $clock=[Diagnostics.Stopwatch]::StartNew()
    Send-Event @{event='RECORDING';maximum_seconds=30}
    # Child-process pipe reader cannot consume the parent terminal's later confirmations.
    $inputTask=[ClearQueryNativeInput]::Read()
    while(!$inputTask.IsCompleted -and $clock.Elapsed.TotalSeconds -lt 30) {Start-Sleep -Milliseconds 20}
    $reason='maximum_duration'
    if($inputTask.IsCompleted) {
        $command=$inputTask.GetAwaiter().GetResult()
        if($command -eq 'CANCEL' -or $null -eq $command) {$reason='cancelled'}
        elseif($command -eq 'STOP') {$reason='enter'}
        else {throw 'Invalid capture control command.'}
    }
    Await-Action ($capture.StopRecordAsync())
    $recording=$false
    Send-Event @{event='DONE';stopped_by=$reason;elapsed_seconds=$clock.Elapsed.TotalSeconds}
} catch {
    Send-Event @{event='ERROR';message=$_.Exception.ToString();hresult=('{0:X8}' -f $_.Exception.HResult)}
    exit 1
} finally {
    if($capture) {
        if($recording) {try {Await-Action ($capture.StopRecordAsync())} catch {}}
        $capture.Dispose()
    }
}
