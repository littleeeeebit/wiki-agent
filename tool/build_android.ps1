$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } elseif ($env:ANDROID_SDK_ROOT) {
    $env:ANDROID_SDK_ROOT
} else { Join-Path $repo 'raw/android-sdk' }
$jdk = if ($env:JAVA_HOME) { $env:JAVA_HOME } else {
    $local = Get-ChildItem (Join-Path $repo 'raw/jdk-17') -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($local) { $local.FullName } else { '' }
}

if (-not (Test-Path (Join-Path $sdk 'platforms/android-36'))) {
    throw 'Android SDK 36가 없습니다. docs/mobile.md의 APK 빌드 준비를 먼저 따르세요.'
}
if (-not (Test-Path (Join-Path $sdk 'build-tools/36.0.0/apksigner.bat'))) {
    throw 'Android build-tools 36.0.0이 필요합니다.'
}
if (-not $jdk -or -not (Test-Path (Join-Path $jdk 'bin/java.exe'))) {
    throw 'JDK 17이 없습니다. docs/mobile.md의 APK 빌드 준비를 먼저 따르세요.'
}
$version = (& (Join-Path $jdk 'bin/java.exe') --version | Select-Object -First 1) -join ''
if ($version -notmatch '^(?:openjdk|java) (?<major>\d+)' -or [int]$Matches.major -lt 17) {
    throw "JDK 17 이상이 필요합니다. 현재: $version"
}

$env:ANDROID_HOME = $sdk
$env:JAVA_HOME = $jdk
$previousKeystore = $env:WIKI_ANDROID_KEYSTORE
$previousPassword = $env:WIKI_ANDROID_KEY_PASSWORD
try {
    if (-not $env:WIKI_ANDROID_KEYSTORE) {
        $signing = Join-Path $repo 'raw/android-signing'
        $keystore = Join-Path $signing 'release.p12'
        $passwordFile = Join-Path $signing 'password.dpapi'
        New-Item $signing -ItemType Directory -Force | Out-Null
        if (Test-Path $passwordFile) {
            $protected = Get-Content $passwordFile -Raw | ConvertTo-SecureString
            $credential = [PSCredential]::new('wiki-agent', $protected)
            $env:WIKI_ANDROID_KEY_PASSWORD = $credential.GetNetworkCredential().Password
        } elseif (Test-Path $keystore) {
            throw '서명 키의 암호 파일이 없습니다. 키를 덮어쓰지 않습니다. 서명 백업을 복구하세요.'
        } else {
            $entropy = New-Object byte[] 32
            $random = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $random.GetBytes($entropy) } finally { $random.Dispose() }
            $env:WIKI_ANDROID_KEY_PASSWORD = [Convert]::ToBase64String($entropy)
            $protected = ConvertTo-SecureString $env:WIKI_ANDROID_KEY_PASSWORD -AsPlainText -Force
            [IO.File]::WriteAllText($passwordFile, ($protected | ConvertFrom-SecureString), [Text.UTF8Encoding]::new($false))
        }
        $env:WIKI_ANDROID_KEYSTORE = $keystore
        if (-not (Test-Path $keystore)) {
            & (Join-Path $jdk 'bin/keytool.exe') -genkeypair -noprompt -storetype PKCS12 -keystore $keystore `
                -alias wiki-agent -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=wiki-agent Mobile' `
                -storepass:env WIKI_ANDROID_KEY_PASSWORD -keypass:env WIKI_ANDROID_KEY_PASSWORD
            if ($LASTEXITCODE -ne 0) { throw '릴리스 서명 키를 생성하지 못했습니다.' }
        }
    } elseif (-not $env:WIKI_ANDROID_KEY_PASSWORD) {
        throw '외부 서명 키에는 WIKI_ANDROID_KEY_PASSWORD가 필요합니다.'
    }

    & (Join-Path $repo 'android/gradlew.bat') -p (Join-Path $repo 'android') clean testReleaseUnitTest lintRelease assembleRelease bundleRelease
    if ($LASTEXITCODE -ne 0) { throw 'Android 릴리스 빌드가 실패했습니다.' }

    $built = Join-Path $repo 'android/app/build/outputs/apk/release/app-release.apk'
    $buildTools = Join-Path $sdk 'build-tools/36.0.0'
    & (Join-Path $buildTools 'apksigner.bat') verify $built
    if ($LASTEXITCODE -ne 0) { throw 'APK 서명 검증이 실패했습니다.' }
    & (Join-Path $buildTools 'zipalign.exe') -c -P 16 4 $built
    if ($LASTEXITCODE -ne 0) { throw 'APK 정렬 검증이 실패했습니다.' }
    $badging = (& (Join-Path $buildTools 'aapt.exe') dump badging $built) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $badging -match 'application-debuggable' -or $badging -notmatch "targetSdkVersion:'36'") {
        throw '배포 APK는 디버그가 꺼져 있고 API 36을 대상으로 해야 합니다.'
    }
    $permissions = (& (Join-Path $buildTools 'aapt.exe') dump permissions $built) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'APK 권한을 확인하지 못했습니다.' }
    foreach ($match in [regex]::Matches($permissions, "uses-permission: name='([^']+)'")) {
        if ($match.Groups[1].Value -notin @('android.permission.INTERNET', 'android.permission.ACCESS_NETWORK_STATE')) {
            throw "APK에 예상하지 않은 권한이 있습니다: $($match.Groups[1].Value)"
        }
    }

    # Runtime data, not scratch: after-merge clears artifacts/ and the server serves this file.
    $artifact = Join-Path $repo 'raw/android/wiki-agent.apk'
    $bundle = Join-Path $repo 'raw/android/wiki-agent.aab'
    New-Item (Split-Path -Parent $artifact) -ItemType Directory -Force | Out-Null
    Copy-Item $built $artifact -Force
    Copy-Item (Join-Path $repo 'android/app/build/outputs/bundle/release/app-release.aab') $bundle -Force
    Write-Host "APK: $artifact"
    Write-Host "AAB: $bundle"
} finally {
    $env:WIKI_ANDROID_KEYSTORE = $previousKeystore
    $env:WIKI_ANDROID_KEY_PASSWORD = $previousPassword
}
