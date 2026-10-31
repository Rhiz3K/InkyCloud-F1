"""Content Security Policy builders for site and locally hosted API documentation."""

DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})
REDOC_STYLE_HASHES = (
    "'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=' "
    "'sha256-QMIg+bpjm3JdElJ388KYke01izlUW0UoNOeKjpMxdgc='"
)


def build_csp(nonce: str, *, redoc: bool = False) -> str:
    """Build a nonce-based policy, allowing ReDoc's pinned styles and search worker."""
    styles = f"'self' 'nonce-{nonce}'"
    if redoc:
        styles += f" {REDOC_STYLE_HASHES}"
    policy = (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        f"style-src {styles}; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    if redoc:
        policy += "; worker-src 'self' blob:"
    return policy
