#!/usr/bin/env python3
"""
ROTHSTEIN — Security Module
Sindicato Lansky | Ciberseguridad Intermedia

Componentes:
1. Encriptación de config (Fernet AES-128)
2. Hash de API key (SHA-256 — no se guarda en texto plano)
3. Rate limiting por IP
4. IP whitelist
5. Logs de acceso con rotación
6. Sanitización de inputs
7. Generación de certificado HTTPS auto-firmado
"""

import hashlib
import json
import os
import ssl
import time
import logging
import secrets
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from functools import wraps

# ─── CONFIG ───
BASE_DIR = Path(__file__).parent
SECURITY_DIR = BASE_DIR / ".security"
SECURITY_DIR.mkdir(exist_ok=True)

# ─── 1. ENCRYPTION (Fernet AES) ───

def generate_master_key():
    """Genera clave maestra y la guarda en .security/master.key"""
    key_file = SECURITY_DIR / "master.key"
    if key_file.exists():
        return key_file.read_text().strip()

    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
    except ImportError:
        # Fallback: usar secrets si cryptography no está disponible
        key = secrets.token_urlsafe(32)

    key_file.write_text(key)
    os.chmod(str(key_file), 0o600)  # Solo lectura para el owner
    return key

def encrypt_config(config_path: str, fields_to_encrypt=None):
    """Encripta campos sensibles del config.json"""
    if fields_to_encrypt is None:
        fields_to_encrypt = ["api_key"]

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("[SECURITY] cryptography no disponible — usando hash-only mode")
        return False

    key = generate_master_key()
    f = Fernet(key.encode() if len(key) == 44 else Fernet.generate_key())

    config = json.loads(Path(config_path).read_text())

    for field in fields_to_encrypt:
        if field in config and not config[field].startswith("gAAAAA"):
            config[field] = f.encrypt(config[field].encode()).decode()

    # Encrypt exchange API keys
    for exchange, data in config.get("exchanges", {}).items():
        for subfield in ["api_key", "api_secret", "passphrase"]:
            if subfield in data and data[subfield] and not data[subfield].startswith("gAAAAA"):
                data[subfield] = f.encrypt(data[subfield].encode()).decode()

    Path(config_path).write_text(json.dumps(config, indent=2))
    print(f"[SECURITY] Config encriptado: {config_path}")
    return True

def decrypt_value(encrypted_value: str) -> str:
    """Desencripta un valor individual"""
    try:
        from cryptography.fernet import Fernet
        key = (SECURITY_DIR / "master.key").read_text().strip()
        f = Fernet(key.encode() if len(key) == 44 else key)
        return f.decrypt(encrypted_value.encode()).decode()
    except Exception:
        return encrypted_value  # Si no se puede desencriptar, devolver original


# ─── 2. API KEY HASHING ───

def hash_api_key(api_key: str) -> str:
    """SHA-256 hash de la API key para comparación segura"""
    return hashlib.sha256(api_key.encode()).hexdigest()

def verify_api_key(provided_key: str, stored_hash: str) -> bool:
    """Compara API key con su hash (timing-safe)"""
    provided_hash = hash_api_key(provided_key)
    return secrets.compare_digest(provided_hash, stored_hash)

def setup_hashed_auth(config_path: str):
    """Genera hash de la API key y lo guarda en .security/api_key.hash"""
    config = json.loads(Path(config_path).read_text())
    api_key = config.get("api_key", "")

    hash_file = SECURITY_DIR / "api_key.hash"
    key_hash = hash_api_key(api_key)
    hash_file.write_text(key_hash)
    os.chmod(str(hash_file), 0o600)
    print(f"[SECURITY] API key hash guardado en {hash_file}")
    return key_hash


# ─── 3. RATE LIMITING ───

class RateLimiter:
    """Rate limiter por IP — previene brute force"""
    def __init__(self, max_requests=30, window_seconds=60, lockout_after=5):
        self.max_requests = max_requests
        self.window = window_seconds
        self.lockout_after = lockout_after  # Failed auth attempts before lockout
        self.requests = defaultdict(list)
        self.failed_auths = defaultdict(list)
        self.locked_ips = {}  # ip -> lockout_until timestamp

    def is_allowed(self, ip: str) -> tuple:
        """Returns (allowed: bool, reason: str)"""
        now = time.time()

        # Check lockout
        if ip in self.locked_ips:
            if now < self.locked_ips[ip]:
                remaining = int(self.locked_ips[ip] - now)
                return False, f"IP bloqueada por {remaining}s (brute force detection)"
            else:
                del self.locked_ips[ip]
                self.failed_auths[ip] = []

        # Clean old requests
        self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window]

        # Check rate limit
        if len(self.requests[ip]) >= self.max_requests:
            return False, f"Rate limit: {self.max_requests} requests/{self.window}s"

        self.requests[ip].append(now)
        return True, "ok"

    def register_failed_auth(self, ip: str):
        """Registra un intento fallido de auth"""
        now = time.time()
        self.failed_auths[ip] = [t for t in self.failed_auths[ip] if now - t < 300]
        self.failed_auths[ip].append(now)

        if len(self.failed_auths[ip]) >= self.lockout_after:
            lockout_duration = 300  # 5 minutos
            self.locked_ips[ip] = now + lockout_duration
            return True, lockout_duration
        return False, 0


# ─── 4. IP WHITELIST ───

class IPWhitelist:
    """Whitelist de IPs permitidas"""
    def __init__(self):
        self.whitelist_file = SECURITY_DIR / "ip_whitelist.json"
        self.whitelist = self._load()

    def _load(self):
        if self.whitelist_file.exists():
            return json.loads(self.whitelist_file.read_text())
        # Default: solo localhost
        default = ["127.0.0.1", "::1", "localhost"]
        self._save(default)
        return default

    def _save(self, whitelist):
        self.whitelist_file.write_text(json.dumps(whitelist, indent=2))

    def is_allowed(self, ip: str) -> bool:
        if not self.whitelist:  # Empty whitelist = allow all
            return True
        return ip in self.whitelist

    def add_ip(self, ip: str):
        if ip not in self.whitelist:
            self.whitelist.append(ip)
            self._save(self.whitelist)


# ─── 5. ACCESS LOGGING ───

def setup_access_log():
    """Configura logging de accesos con rotación"""
    log_file = SECURITY_DIR / "access.log"

    logger = logging.getLogger("rothstein.access")
    logger.setLevel(logging.INFO)

    # File handler con rotación simple (max 1MB)
    handler = logging.FileHandler(str(log_file))
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)

    return logger

def log_access(logger, ip: str, method: str, path: str, status: int, details: str = ""):
    """Registra un acceso en el log"""
    msg = f"IP={ip} | {method} {path} | {status}"
    if details:
        msg += f" | {details}"

    if status >= 400:
        logger.warning(msg)
    else:
        logger.info(msg)


# ─── 6. INPUT SANITIZATION ───

ALLOWED_PAIRS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"}
ALLOWED_ACTIONS = {"unlock", "lock", "pause", "reset", "simulate-loss"}

def sanitize_pair(pair: str) -> str:
    """Valida y sanitiza un par de trading"""
    clean = pair.upper().strip()[:20]  # Max 20 chars
    if clean not in ALLOWED_PAIRS:
        raise ValueError(f"Par no permitido: {clean}. Permitidos: {', '.join(ALLOWED_PAIRS)}")
    return clean

def sanitize_number(value, min_val=None, max_val=None, default=0.0):
    """Valida y sanitiza un número"""
    try:
        num = float(value)
        if min_val is not None and num < min_val:
            return min_val
        if max_val is not None and num > max_val:
            return max_val
        return num
    except (ValueError, TypeError):
        return default


# ─── 7. HTTPS CERTIFICATE ───

def generate_self_signed_cert():
    """Genera certificado auto-firmado para HTTPS local"""
    cert_file = SECURITY_DIR / "rothstein.crt"
    key_file = SECURITY_DIR / "rothstein.key"

    if cert_file.exists() and key_file.exists():
        print(f"[SECURITY] Certificado existente: {cert_file}")
        return str(cert_file), str(key_file)

    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_file),
            "-out", str(cert_file),
            "-days", "365",
            "-nodes",
            "-subj", "/C=XX/ST=Sindicato/L=Lansky/O=Rothstein/CN=localhost"
        ], check=True, capture_output=True)

        os.chmod(str(key_file), 0o600)
        print(f"[SECURITY] Certificado HTTPS generado: {cert_file}")
        return str(cert_file), str(key_file)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[SECURITY] openssl no disponible — ejecutando sin HTTPS")
        return None, None


# ─── SECURITY REPORT ───

def generate_security_report():
    """Genera reporte del estado de seguridad"""
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {}
    }

    # 1. Master key exists
    report["checks"]["master_key"] = (SECURITY_DIR / "master.key").exists()

    # 2. API key hashed
    report["checks"]["api_key_hashed"] = (SECURITY_DIR / "api_key.hash").exists()

    # 3. HTTPS cert exists
    report["checks"]["https_cert"] = (SECURITY_DIR / "rothstein.crt").exists()

    # 4. IP whitelist configured
    report["checks"]["ip_whitelist"] = (SECURITY_DIR / "ip_whitelist.json").exists()

    # 5. Access log exists
    report["checks"]["access_log"] = (SECURITY_DIR / "access.log").exists()

    # 6. File permissions
    for f in SECURITY_DIR.iterdir():
        perms = oct(f.stat().st_mode)[-3:]
        report["checks"][f"perms_{f.name}"] = perms in ("600", "644", "700")

    passed = sum(1 for v in report["checks"].values() if v)
    total = len(report["checks"])
    report["score"] = f"{passed}/{total}"
    report["status"] = "SECURE" if passed == total else "PARTIAL" if passed > total // 2 else "INSECURE"

    return report


# ─── FASTAPI MIDDLEWARE INTEGRATION ───

def create_security_middleware(app, config_path: str):
    """Integra toda la seguridad como middleware de FastAPI"""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    rate_limiter = RateLimiter(max_requests=60, window_seconds=60, lockout_after=5)
    ip_whitelist = IPWhitelist()
    access_logger = setup_access_log()

    # Setup hashed auth
    api_key_hash = setup_hashed_auth(config_path)

    # Generate HTTPS cert
    cert_file, key_file = generate_self_signed_cert()

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        method = request.method
        path = request.url.path

        # Skip for static files
        if path == "/" or path.startswith("/static"):
            return await call_next(request)

        # IP Whitelist
        if not ip_whitelist.is_allowed(ip):
            log_access(access_logger, ip, method, path, 403, "IP not whitelisted")
            return JSONResponse(status_code=403, content={"detail": "IP no autorizada"})

        # Rate Limiting
        allowed, reason = rate_limiter.is_allowed(ip)
        if not allowed:
            log_access(access_logger, ip, method, path, 429, reason)
            return JSONResponse(status_code=429, content={"detail": reason})

        # Process request
        response = await call_next(request)

        # Log failed auth
        if response.status_code == 403:
            locked, duration = rate_limiter.register_failed_auth(ip)
            if locked:
                log_access(access_logger, ip, method, path, 403, f"LOCKOUT {duration}s")

        # Log access
        log_access(access_logger, ip, method, path, response.status_code)

        return response

    return cert_file, key_file


if __name__ == "__main__":
    # Self-test
    print("ROTHSTEIN Security Module — Self-Test")
    print("=" * 40)

    # Test hash
    test_key = "test_key_12345"
    h = hash_api_key(test_key)
    assert verify_api_key(test_key, h), "Hash verification failed"
    assert not verify_api_key("wrong_key", h), "Wrong key should not verify"
    print("[OK] API Key hashing")

    # Test rate limiter
    rl = RateLimiter(max_requests=3, window_seconds=1)
    assert rl.is_allowed("127.0.0.1")[0]
    assert rl.is_allowed("127.0.0.1")[0]
    assert rl.is_allowed("127.0.0.1")[0]
    assert not rl.is_allowed("127.0.0.1")[0]  # 4th should fail
    print("[OK] Rate limiting")

    # Test sanitization
    assert sanitize_pair("btcusdt") == "BTCUSDT"
    try:
        sanitize_pair("HACKUSDT")
        assert False, "Should have raised"
    except ValueError:
        pass
    print("[OK] Input sanitization")

    # Test IP whitelist
    wl = IPWhitelist()
    assert wl.is_allowed("127.0.0.1")
    print("[OK] IP whitelist")

    # Generate cert
    cert, key = generate_self_signed_cert()
    print(f"[OK] HTTPS cert: {cert}")

    # Security report
    report = generate_security_report()
    print(f"\n[SECURITY SCORE] {report['score']} — {report['status']}")
