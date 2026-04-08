"""Self-signed certificate generation for CONNECT MitM proxy."""
from __future__ import annotations

import logging
import os
import ssl
import subprocess
from pathlib import Path

from ..config.settings import get as cfg

logger = logging.getLogger(__name__)


def get_ssl_context() -> ssl.SSLContext:
    """Get SSL context with self-signed certificate for MitM proxy.

    HyProxy has InsecureSkipVerify=true, so any cert works.
    Auto-generates cert if not found.
    """
    cert_file = Path(cfg("proxy.cert_file", "configs/proxy-cert.pem"))
    key_file = Path(cfg("proxy.key_file", "configs/proxy-key.pem"))

    if not cert_file.exists() or not key_file.exists():
        _generate_self_signed(cert_file, key_file)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file), str(key_file))
    return ctx


def _generate_self_signed(cert_file: Path, key_file: Path) -> None:
    """Generate a self-signed certificate using openssl."""
    cert_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Generating self-signed certificate: %s", cert_file)
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_file),
            "-out", str(cert_file),
            "-days", "3650",
            "-nodes",
            "-subj", "/CN=bookstore-scraper-proxy",
        ], check=True, capture_output=True, timeout=30)
        logger.info("Self-signed certificate generated")
    except FileNotFoundError:
        # openssl not available — generate with Python ssl
        _generate_with_python(cert_file, key_file)
    except subprocess.CalledProcessError as e:
        logger.warning("openssl failed: %s, trying Python fallback", e.stderr)
        _generate_with_python(cert_file, key_file)


def _generate_with_python(cert_file: Path, key_file: Path) -> None:
    """Fallback: generate self-signed cert using Python cryptography or ssl."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "bookstore-scraper-proxy"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )

        key_file.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info("Self-signed certificate generated (Python cryptography)")
    except ImportError:
        raise RuntimeError(
            "Cannot generate self-signed certificate. "
            "Install openssl or 'pip install cryptography'. "
            "Or provide cert/key files manually."
        )
