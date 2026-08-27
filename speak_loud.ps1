param([string]$Text = "", [int]$Rate = 2, [string]$OutFile = "")
# Zira speaks LOUD: render TTS to WAV, amplify to near-full scale (max 3x),
# then play. Volume 100 alone leaves her quiet — this actually boosts her.
# With -OutFile, the amplified WAV is ALSO saved there (for the phone push)
# instead of being deleted.
$ErrorActionPreference = "SilentlyContinue"
if (-not $Text) { exit }

Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female) } catch {}
$s.Volume = 100
$s.Rate = $Rate

$wav = Join-Path $env:TEMP ("zira_" + $PID + ".wav")
$s.SetOutputToWaveFile($wav)
$s.Speak($Text)
$s.Dispose()

if (Test-Path $wav) {
    try {
        $fs = [System.IO.File]::OpenRead($wav)
        $br = New-Object System.IO.BinaryReader($fs)
        $null = $br.ReadBytes(12)  # RIFF + size + WAVE
        $dataOff = -1; $dataLen = 0
        while ($fs.Position + 8 -le $fs.Length) {
            $id = [System.Text.Encoding]::ASCII.GetString($br.ReadBytes(4))
            $sz = $br.ReadUInt32()
            if ($id -eq "data") { $dataOff = $fs.Position; $dataLen = $sz; break }
            if ($sz -gt 0) { $null = $br.ReadBytes($sz) }
        }
        if ($dataOff -ge 0 -and $dataLen -gt 4) {
            $fs.Position = $dataOff
            $n = [int]($dataLen / 2)
            $samples = New-Object 'int[]' $n
            $peak = 1
            for ($i = 0; $i -lt $n; $i++) {
                $v = $br.ReadInt16()
                $samples[$i] = $v
                $a = [Math]::Abs($v); if ($a -gt $peak) { $peak = $a }
            }
            $gain = 0.95 * 32767 / $peak
            if ($gain -gt 3.0) { $gain = 3.0 }
            if ($gain -gt 1.05) {
                for ($i = 0; $i -lt $n; $i++) {
                    $v = [int]($samples[$i] * $gain)
                    if ($v -gt 32767) { $v = 32767 } elseif ($v -lt -32768) { $v = -32768 }
                    $samples[$i] = $v
                }
                $fs.Position = $dataOff
                for ($i = 0; $i -lt $n; $i++) { $br = $null; }  # no-op guard
                $bw = New-Object System.IO.BinaryWriter($fs)
                foreach ($v in $samples) { $bw.Write([int16]$v) }
                $bw.Flush()
            }
        }
        $fs.Close()
    } catch {}

    $player = New-Object System.Media.SoundPlayer($wav)
    $player.PlaySync()
    if ($OutFile) {
        try { Copy-Item $wav $OutFile -Force } catch {}
    }
    Remove-Item $wav
}
