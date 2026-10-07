"""App-owned tunnel runtime and HTTPS probes independent of system DNS."""

import hashlib
from http.client import HTTPException, HTTPSConnection
import ipaddress
import json
from pathlib import Path
import platform
import shutil
import socket
import ssl
import tarfile
import tempfile
import threading
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

import certifi


VERSION = "2026.10.0"
# Official release asset digests, pinned with the executable version.
ASSETS = {
    ("Windows", "amd64"): ("cloudflared-windows-amd64.exe", "86aee4017b26625cee8484c113558f48effa4cd47f7aa05fcf425604e5d2b23c"),
    ("Windows", "386"): ("cloudflared-windows-386.exe", "0630a8779e9823a1a3b091698b8e71874e0f7b205559219f52fdd301466b5546"),
    ("Darwin", "amd64"): ("cloudflared-darwin-amd64.tgz", "903845b81828c8cb3c5d13d816a2de71c06a3da5785469df8eb0e1b736d92f9f"),
    ("Darwin", "arm64"): ("cloudflared-darwin-arm64.tgz", "a2f79ff7b9420aa537d74af239f376da170bbabeb529aec416002adac6a72e70"),
    ("Linux", "amd64"): ("cloudflared-linux-amd64", "d33ff2d14475178d2012c2c56beba87389ac5ded27649519f198a7d3134a99db"),
    ("Linux", "arm64"): ("cloudflared-linux-arm64", "e6422b9d4f72d3194bc5a38676f13667c06666523217b842a877d72a80b5ac08"),
}
MAX_DOWNLOAD = 128 * 1024 * 1024
_download_lock = threading.Lock()


def tls_context():
    context = ssl.create_default_context()
    # Keep OS/company roots and supplement incomplete Python certificate stores.
    context.load_verify_locations(cafile=certifi.where())
    return context


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cloudflared(directory: Path) -> Path:
    """Reuse a verified copy or download one without PATH changes or elevation."""
    system, machine = platform.system(), platform.machine().lower()
    arch = "arm64" if machine in ("aarch64", "arm64") else "amd64" if machine in ("amd64", "x86_64") else "386"
    # Windows on ARM supports the official x64 executable.
    if system == "Windows" and arch == "arm64":
        arch = "amd64"
    if (system, arch) not in ASSETS:
        raise RuntimeError("이 PC에서는 휴대폰 연결 도구를 실행할 수 없습니다")
    asset, expected = ASSETS[system, arch]
    with _download_lock:
        installed = shutil.which("cloudflared")
        if installed and not asset.endswith(".tgz") and digest(installed) == expected:
            return Path(installed)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / asset
        if not target.is_file() or digest(target) != expected:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
                    temporary = Path(out.name)
                    checksum, total = hashlib.sha256(), 0
                    url = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{asset}"
                    with urlopen(url, timeout=30, context=tls_context()) as response:
                        while chunk := response.read(1024 * 1024):
                            total += len(chunk)
                            if total > MAX_DOWNLOAD:
                                raise RuntimeError("연결 도구 다운로드 크기가 올바르지 않습니다")
                            checksum.update(chunk)
                            out.write(chunk)
                    if checksum.hexdigest() != expected:
                        raise RuntimeError("연결 도구 검증에 실패했습니다. 다시 연결을 눌러 주세요")
                temporary.replace(target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if asset.endswith(".tgz"):
            # Read one regular member; never extract archive-selected paths.
            with tarfile.open(target, "r:gz") as archive:
                member = next((m for m in archive if m.name.lstrip("./") == "cloudflared" and m.isfile()), None)
                if member is None or member.size > MAX_DOWNLOAD:
                    raise RuntimeError("연결 도구 압축 파일이 올바르지 않습니다")
                with archive.extractfile(member) as stream:
                    payload = stream.read(MAX_DOWNLOAD + 1)
            target = directory / "cloudflared"
            if not target.is_file() or digest(target) != hashlib.sha256(payload).hexdigest():
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as out:
                        temporary = Path(out.name)
                        out.write(payload)
                    temporary.replace(target)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        target.chmod(0o700)
        return target


def https_json(host, path, address):
    """Connect to an explicit address while checking the original TLS hostname."""
    connection = HTTPSConnection(host, timeout=3)
    try:
        raw = socket.create_connection((address, 443), timeout=3)
        try:
            connection.sock = tls_context().wrap_socket(raw, server_hostname=host)
        except BaseException:
            raw.close()
            raise
        connection.request("GET", path, headers={"Accept": "application/dns-json"})
        response = connection.getresponse()
        if response.status != 200:
            raise OSError(f"HTTPS probe returned {response.status}")
        return json.loads(response.read(16384))
    except HTTPException as exc:
        raise OSError("Invalid HTTPS response") from exc
    finally:
        connection.close()


def public_status(origin):
    """Resolve only the tunnel hostname via authenticated DoH, then probe it."""
    host = urlsplit(origin).hostname
    if not host or not host.endswith(".trycloudflare.com"):
        raise ValueError("Expected a temporary tunnel hostname")
    problem = OSError("The public address is not ready")
    for resolver in ("1.1.1.1", "1.0.0.1"):
        try:
            answer = https_json("cloudflare-dns.com", "/dns-query?" + urlencode({"name": host, "type": "A"}), resolver)
            if not isinstance(answer, dict):
                raise ValueError("DNS returned an invalid response")
            if answer.get("Status") != 0:
                raise OSError(f"DNS response status {answer.get('Status')}")
            for record in answer.get("Answer", []):
                if record.get("type") != 1:
                    continue
                address = ipaddress.ip_address(record["data"])
                if not address.is_global:
                    raise ValueError("DNS returned a non-public address")
                try:
                    return https_json(host, "/api/mobile/status", str(address))
                except OSError as exc:
                    problem = exc
        except (OSError, ValueError, KeyError) as exc:
            problem = exc
    raise problem
