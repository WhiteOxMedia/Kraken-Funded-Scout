$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
"installed voices:" | Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\voice_check.txt" -Encoding utf8
foreach ($v in $s.GetInstalledVoices()) {
  $info = $v.VoiceInfo
  "  $($info.Name) | $($info.Gender) | $($info.Culture)" | Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\voice_check.txt" -Append -Encoding utf8
}
try {
  $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female)
  "female voice selected: $($s.Voice.Name)" | Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\voice_check.txt" -Append -Encoding utf8
} catch {
  "female voice NOT available: $($_.Exception.Message)" | Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\voice_check.txt" -Append -Encoding utf8
}
$s.Volume = 100
$s.Rate = 1
$s.Speak("Testing, testing. Green light on X R P! Buy fifty percent! I am watching it for you. Get out now!")
"spoke test line" | Out-File -FilePath "C:\Users\birds\Workspace\kraken_scout\voice_check.txt" -Append -Encoding utf8
