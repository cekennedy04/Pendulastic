# tests/test_phone_server.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cryptography import x509

import pendulastic_phone_server as pps


def test_get_or_create_self_signed_cert_creates_files(tmp_path):
    cert_path, key_path = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    assert os.path.exists(cert_path)
    assert os.path.exists(key_path)


def test_cert_has_matching_san_ip(tmp_path):
    cert_path, _ = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    ips = san.value.get_values_for_type(x509.IPAddress)
    assert str(ips[0]) == "192.168.1.50"


def test_repeated_call_with_same_ip_reuses_cached_cert(tmp_path):
    cert_path, key_path = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    mtime1 = os.path.getmtime(cert_path)
    cert_path2, key_path2 = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    assert cert_path2 == cert_path
    assert os.path.getmtime(cert_path2) == mtime1


def test_call_with_different_ip_regenerates_cert(tmp_path):
    cert_path, _ = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    with open(cert_path, "rb") as f:
        cert1 = x509.load_pem_x509_certificate(f.read())
    pps.get_or_create_self_signed_cert(str(tmp_path), "10.0.0.7")
    with open(cert_path, "rb") as f:
        cert2 = x509.load_pem_x509_certificate(f.read())
    assert cert1.serial_number != cert2.serial_number
