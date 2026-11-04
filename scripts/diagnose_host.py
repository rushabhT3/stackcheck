"""Diagnose fetch failures to one host from inside Modal.

DNS lookup, TCP connect, TLS handshake with the normal CA bundle, then a
second handshake with verification off that only prints the presented
certificate chain. No HTTP request, no app changes, verification stays on
in the app.
"""

import modal

app = modal.App("stackcheck-diagnose")
image = modal.Image.debian_slim().pip_install("cryptography")


@app.function(image=image, timeout=120)
def diagnose(host: str = "rera.bihar.gov.in") -> None:
    import socket
    import ssl
    import time

    from cryptography import x509

    print(f"host: {host}")

    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        print(f"DNS FAILED: {type(exc).__name__}: {exc}")
        return
    addresses = sorted({info[4][0] for info in infos})
    print(f"DNS: {addresses}")

    raw: socket.socket | None = None
    for address in addresses:
        started = time.perf_counter()
        try:
            raw = socket.create_connection((address, 443), timeout=10)
        except OSError as exc:
            print(f"TCP {address} FAILED in {time.perf_counter() - started:.1f}s: {exc}")
            continue
        print(f"TCP {address} OK in {time.perf_counter() - started:.1f}s, peer={raw.getpeername()}")
        break
    if raw is None:
        print("TCP FAILED for every address")
        return

    context = ssl.create_default_context()
    started = time.perf_counter()
    try:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            print(f"TLS VERIFIED OK in {time.perf_counter() - started:.1f}s, {tls.version()}")
            return
    except ssl.SSLCertVerificationError as exc:
        print(f"TLS VERIFY FAILED in {time.perf_counter() - started:.1f}s: {exc}")
    except OSError as exc:
        print(f"TLS HANDSHAKE FAILED in {time.perf_counter() - started:.1f}s: {exc}")
        return
    finally:
        raw.close()

    blind = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    blind.check_hostname = False
    blind.verify_mode = ssl.CERT_NONE
    second = socket.create_connection((addresses[0], 443), timeout=10)
    try:
        with blind.wrap_socket(second, server_hostname=host) as tls:
            chain = tls.get_unverified_chain() if hasattr(tls, "get_unverified_chain") else []
            print(f"presented chain: {len(chain)} certificates")
            for cert_bytes in chain:
                cert = x509.load_der_x509_certificate(cert_bytes)
                print(f"  subject={cert.subject.rfc4514_string()}")
                print(f"  issuer={cert.issuer.rfc4514_string()}")
                print(f"  not_valid_after_utc={cert.not_valid_after_utc.isoformat()}")
    except OSError as exc:
        print(f"CHAIN READ FAILED: {exc}")
    finally:
        second.close()


@app.local_entrypoint()
def main(host: str = "rera.bihar.gov.in") -> None:
    diagnose.remote(host)
