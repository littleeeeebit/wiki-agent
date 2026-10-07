"""Fresh-PC provisioning, authenticated DNS fallback and executable integrity."""

import hashlib
from http.client import BadStatusLine
from io import BytesIO
import ssl
from unittest.mock import Mock, patch

import pytest

from main import mobile_transport as transport


def test_fresh_pc_downloads_verifies_and_reuses_runtime(tmp_path):
    payload = b"synthetic-executable"
    name = "cloudflared-windows-amd64.exe"
    assets = {("Windows", "amd64"): (name, hashlib.sha256(payload).hexdigest())}
    with patch.dict(transport.ASSETS, assets, clear=True), \
         patch.object(transport.platform, "system", return_value="Windows"), \
         patch.object(transport.platform, "machine", return_value="AMD64"), \
         patch.object(transport.shutil, "which", return_value=None), \
         patch.object(transport, "urlopen", return_value=BytesIO(payload)) as download:
        binary = transport.cloudflared(tmp_path)
        assert binary.read_bytes() == payload
        assert transport.cloudflared(tmp_path) == binary
        assert download.call_count == 1
        binary.chmod(0o600)
        binary.write_bytes(b"corrupted-cache")
        download.return_value = BytesIO(payload)
        assert transport.cloudflared(tmp_path).read_bytes() == payload
        assert download.call_count == 2


def test_unverified_download_is_never_published(tmp_path):
    assets = {("Windows", "amd64"): ("cloudflared.exe", "0" * 64)}
    with patch.dict(transport.ASSETS, assets, clear=True), \
         patch.object(transport.platform, "system", return_value="Windows"), \
         patch.object(transport.platform, "machine", return_value="AMD64"), \
         patch.object(transport.shutil, "which", return_value=None), \
         patch.object(transport, "urlopen", return_value=BytesIO(b"untrusted")):
        with pytest.raises(RuntimeError, match="검증"):
            transport.cloudflared(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_doh_uses_public_addresses_and_rejects_local_targets():
    answer = {"Status": 0, "Answer": [{"type": 1, "data": "104.16.231.132"}]}
    expected = {"local": False, "paired": False}
    with patch.object(transport, "https_json", side_effect=[answer, expected]) as request:
        assert transport.public_status("https://fixture.trycloudflare.com/") == expected
    assert request.call_args_list[0].args[0] == "cloudflare-dns.com"
    assert request.call_args_list[1].args == ("fixture.trycloudflare.com", "/api/mobile/status", "104.16.231.132")
    answer["Answer"][0]["data"] = "127.0.0.1"
    with patch.object(transport, "https_json", return_value=answer) as request:
        with pytest.raises(ValueError, match="non-public"):
            transport.public_status("https://fixture.trycloudflare.com")
    assert request.call_count == 2
    assert all(call.args[0] == "cloudflare-dns.com" for call in request.call_args_list)


def test_explicit_ip_keeps_tls_verification_sni_and_host():
    context = transport.tls_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    connection = Mock()
    connection.getresponse.return_value = Mock(status=200, read=Mock(return_value=b'{"local":false,"paired":false}'))
    raw, encrypted = Mock(), Mock()
    with patch.object(transport, "HTTPSConnection", return_value=connection) as create, \
         patch.object(transport.socket, "create_connection", return_value=raw), \
         patch.object(transport, "tls_context", return_value=Mock(wrap_socket=Mock(return_value=encrypted))) as tls:
        assert transport.https_json("fixture.trycloudflare.com", "/api/mobile/status", "104.16.231.132") == {
            "local": False, "paired": False}
    create.assert_called_once_with("fixture.trycloudflare.com", timeout=3)
    tls.return_value.wrap_socket.assert_called_once_with(raw, server_hostname="fixture.trycloudflare.com")
    assert connection.sock is encrypted
    connection.close.assert_called_once()
    with patch.object(transport, "https_json", side_effect=ssl.SSLCertVerificationError("bad certificate")):
        with pytest.raises(ssl.SSLCertVerificationError):
            transport.public_status("https://fixture.trycloudflare.com")


def test_malformed_https_reply_is_a_recoverable_error_and_closes_the_socket():
    connection = Mock(getresponse=Mock(side_effect=BadStatusLine("invalid proxy reply")))
    with patch.object(transport, "HTTPSConnection", return_value=connection), \
         patch.object(transport.socket, "create_connection"), patch.object(transport, "tls_context"):
        with pytest.raises(OSError, match="Invalid HTTPS response"):
            transport.https_json("fixture.trycloudflare.com", "/api/mobile/status", "104.16.231.132")
    connection.close.assert_called_once()
