#!/usr/bin/env python3
"""Reencrypt staged secrets that this host's SSH identity can read."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


def main() -> int:
    helper = Path(__file__).resolve().parent
    stage = helper.parent
    config = json.loads((helper / "config.json").read_text())
    identity = Path(config["identity"]).expanduser()
    if not identity.is_absolute():
        identity = Path.home() / identity
    if not identity.is_file() or not os.access(identity, os.R_OK):
        raise RuntimeError(f"Unreadable identity: {identity}")

    rekeyed = skipped = 0
    recipients_dir = helper / "recipients"
    for recipients_file in sorted(recipients_dir.rglob("*.age")):
        relative = recipients_file.relative_to(recipients_dir)
        encrypted = stage / config["rules_dir"] / relative
        if not encrypted.is_file():
            raise RuntimeError(f"Missing encrypted file: {relative}")

        decrypt = ["age", "--decrypt", "--identity", str(identity)]
        probe = subprocess.run(
            [*decrypt, "--output", os.devnull, str(encrypted)],
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode != 0:
            skipped += 1
            continue

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{encrypted.name}.rekey.", dir=encrypted.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            encrypt = ["age", "--encrypt", "--recipients-file", str(recipients_file)]
            if (helper / "armor" / relative).exists():
                encrypt.append("--armor")
            encrypt.extend(["--output", str(temporary)])

            with subprocess.Popen([*decrypt, str(encrypted)], stdout=subprocess.PIPE) as source:
                with subprocess.Popen(encrypt, stdin=source.stdout) as destination:
                    assert source.stdout is not None
                    source.stdout.close()
                    encrypt_status = destination.wait()
                decrypt_status = source.wait()
            if decrypt_status or encrypt_status:
                raise RuntimeError(f"Failed to rekey: {relative}")

            os.chmod(temporary, stat.S_IMODE(encrypted.stat().st_mode))
            os.replace(temporary, encrypted)
            rekeyed += 1
        finally:
            temporary.unlink(missing_ok=True)

    print(f"Rekeyed: {rekeyed}; unreadable: {skipped}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
