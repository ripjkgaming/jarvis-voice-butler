"""Generate public association metadata from the release certificate (no secrets)."""
import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
keydir = Path.home() / ".jarvis" / "android"
cert = subprocess.check_output([
    "keytool", "-exportcert", "-keystore", str(keydir / "locke-link.keystore"),
    "-alias", "locke-link", "-storepass:file", str(keydir / "keystore.pass"),
])
fingerprint = ":".join(f"{b:02X}" for b in hashlib.sha256(cert).digest())
statement = [{
    "relation": ["delegate_permission/common.handle_all_urls", "delegate_permission/common.get_login_creds"],
    "target": {
        "namespace": "android_app", "package_name": "dev.jarvis.lockelink",
        "sha256_cert_fingerprints": [fingerprint],
    },
}]
path = root / "public" / ".well-known" / "assetlinks.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(statement, indent=2) + "\n")
print(f"Association metadata: {path}")
