"""Materialize the VM runtime env from its scoped Secret Manager identity."""

import base64
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def metadata(path):
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/" + path,
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the designated VM")
    project = metadata("project/project-id")
    token = json.loads(metadata("instance/service-accounts/default/token"))["access_token"]
    request = urllib.request.Request(
        f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/memory-harness-runtime/versions/latest:access",
        headers={"Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    payload = base64.b64decode(result["payload"]["data"], validate=True)
    # Validate without exposing any value in exceptions or output.
    entries = {}
    for line in payload.decode().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in entries:
            raise SystemExit("Secret env payload has an invalid or duplicate entry")
        entries[key] = value
    if len(entries.get("ADMIN_TOKEN", "")) < 24 or len(entries.get("STAFF_PASSWORD", "")) < 16:
        raise SystemExit("Secret env payload lacks strong app credentials")
    target = Path("/etc/memory-harness/runtime.env")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".runtime-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("Runtime env installed from Secret Manager with root-only permissions")


if __name__ == "__main__":
    main()
