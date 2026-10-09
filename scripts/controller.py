#!/usr/bin/env python3
"""Rekey agenix secrets on selected SSH hosts and update the local ciphertext."""

import argparse
import filecmp
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


REMOTE_DIRECTORY = re.compile(r"/tmp/agenix-rekey-helper\.[A-Za-z0-9]{8}\Z")


def relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\n" in value or "\x00" in value:
        raise ValueError(f"Invalid relative path: {value!r}")
    if value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"Invalid relative path: {value!r}")
    return Path(value)


def copy_source(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


def same_source(left: Path, right: Path) -> bool:
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and os.readlink(left) == os.readlink(right)
    if left.is_file() or right.is_file():
        return left.is_file() and right.is_file() and filecmp.cmp(left, right, shallow=False)
    if not left.is_dir() or not right.is_dir():
        return False
    left_names = {entry.name for entry in left.iterdir()}
    if left_names != {entry.name for entry in right.iterdir()}:
        return False
    return all(same_source(left / name, right / name) for name in left_names)


def check_rules(work: Path, rules: Path, show_output: bool = False) -> bool:
    result = subprocess.run(
        ["agenix", "--check"],
        env={**os.environ, "AGENIX_RULES": str(work / rules)},
        stdout=None if show_output else subprocess.DEVNULL,
        stderr=None if show_output else subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def transfer_to_host(work: Path, target: str, remote: str) -> None:
    command = f"tar -C {shlex.quote(remote)} -xf -"
    with subprocess.Popen(
        ["tar", "-C", str(work), "-cf", "-", "."], stdout=subprocess.PIPE
    ) as archive:
        assert archive.stdout is not None
        with subprocess.Popen(["ssh", target, command], stdin=archive.stdout) as transfer:
            archive.stdout.close()
            transfer_status = transfer.wait()
        archive_status = archive.wait()
    if archive_status or transfer_status:
        raise RuntimeError(f"Failed to send staged files to {target}")


def transfer_from_host(destination: Path, target: str, remote: str) -> None:
    command = f"tar -C {shlex.quote(remote)} -cf - ."
    with subprocess.Popen(["ssh", target, command], stdout=subprocess.PIPE) as archive:
        assert archive.stdout is not None
        with subprocess.Popen(
            ["tar", "-C", str(destination), "-xf", "-"], stdin=archive.stdout
        ) as transfer:
            archive.stdout.close()
            transfer_status = transfer.wait()
        archive_status = archive.wait()
    if archive_status or transfer_status:
        raise RuntimeError(f"Failed to receive staged files from {target}")


def run_host(
    work: Path,
    helper: Path,
    target: str,
    identity: str,
    rules_dir: Path,
    python_ref: str,
    age_ref: str,
) -> Path:
    (helper / "config.json").write_text(
        json.dumps({"identity": identity, "rules_dir": str(rules_dir)})
    )
    remote = subprocess.check_output(
        ["ssh", target, "mktemp -d /tmp/agenix-rekey-helper.XXXXXXXX"], text=True
    ).strip()
    if not REMOTE_DIRECTORY.fullmatch(remote):
        raise RuntimeError(f"Unexpected remote staging path: {remote!r}")
    try:
        transfer_to_host(work, target, remote)
        worker = f"{remote}/.agenix-rekey-helper/worker.py"
        command = " ".join(
            map(
                shlex.quote,
                [
                    "nix",
                    "--extra-experimental-features",
                    "nix-command flakes",
                    "shell",
                    python_ref,
                    age_ref,
                    "--command",
                    "python3",
                    worker,
                ],
            )
        )
        subprocess.run(["ssh", target, command], check=True)
        returned = work.parent / "returned"
        returned.mkdir()
        transfer_from_host(returned, target, remote)
        return returned
    finally:
        subprocess.run(
            ["ssh", target, f"rm -rf -- {shlex.quote(remote)}"],
            check=False,
            stdout=subprocess.DEVNULL,
        )


def write_changed_file(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{destination.name}.rekey.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--host", action="append", dest="hosts", metavar="NAME")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = json.loads(Path(os.environ["REKEY_CONFIG"]).read_text())
    worker = Path(os.environ["REKEY_WORKER"])
    repo = args.repo.resolve(strict=True)
    rules = relative_path(config["rules"])
    sources = [relative_path(value) for value in config["sources"]]
    if not sources:
        raise ValueError("At least one source path is required")
    hosts = config["hosts"]
    selected = sorted(hosts) if args.all else list(dict.fromkeys(args.hosts))
    if not selected:
        raise ValueError("No hosts selected")
    for name in selected:
        if name not in hosts or not isinstance(hosts[name], dict) or not all(
            isinstance(hosts[name].get(key), str) and hosts[name][key]
            for key in ("target", "identity")
        ):
            raise ValueError(f"Unknown or incomplete host: {name}")
    for source in sources:
        if not (repo / source).exists():
            raise ValueError(f"Missing source: {source}")
    if not (repo / rules).is_file():
        raise ValueError(f"Missing rules: {rules}")

    with tempfile.TemporaryDirectory(prefix="agenix-rekey-helper.") as scratch_name:
        scratch = Path(scratch_name)
        original = scratch / "original"
        work = scratch / "work"
        original.mkdir()
        work.mkdir()
        for source in sources:
            copy_source(repo / source, original / source)
        for source in sources:
            copy_source(original / source, work / source)

        result = subprocess.run(
            [
                "nix-instantiate", "--eval", "--strict", "--json", "--expr",
                "{ rules }: import (builtins.toPath rules)", "--argstr", "rules", str(work / rules),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        manifest = json.loads(result.stdout)
        if not isinstance(manifest, dict):
            raise ValueError("Agenix rules must evaluate to an attribute set")
        for name, rule in manifest.items():
            relative_path(name)
            if not name.endswith(".age") or not isinstance(rule, dict):
                raise ValueError(f"Invalid secret rule: {name}")
            keys = rule.get("publicKeys")
            if not isinstance(keys, list) or not keys or any(
                not isinstance(key, str) or not key or "\n" in key for key in keys
            ):
                raise ValueError(f"Invalid recipient list: {name}")
            if not (work / rules.parent / name).is_file():
                raise ValueError(f"Missing encrypted file: {name}")

        print(f"Rules: {rules}; secrets: {len(manifest)}; hosts: {' '.join(selected)}", flush=True)
        if args.dry_run:
            return 0

        helper = work / ".agenix-rekey-helper"
        if helper.exists():
            raise ValueError("Reserved staging path exists in sources")
        (helper / "recipients").mkdir(parents=True)
        (helper / "armor").mkdir()
        shutil.copy2(worker, helper / "worker.py")
        for name, rule in manifest.items():
            recipients = helper / "recipients" / name
            recipients.parent.mkdir(parents=True, exist_ok=True)
            recipients.write_text("\n".join(rule["publicKeys"]) + "\n")
            if rule.get("armor") is True:
                armored = helper / "armor" / name
                armored.parent.mkdir(parents=True, exist_ok=True)
                armored.touch()

        if check_rules(work, rules):
            print("All recipients already match the rules; no files were changed.")
            return 0

        verified = False
        for name in selected:
            host = hosts[name]
            target, identity = host["target"], host["identity"]
            if "\n" in identity:
                raise ValueError(f"Invalid identity path for {name}")
            print(f"Processing {name} ({target})", flush=True)
            returned = run_host(
                work, helper, target, identity,
                rules.parent,
                config.get("pythonRef", "nixpkgs#python3"),
                config.get("ageRef", "nixpkgs#age"),
            )
            for secret in manifest:
                relative = rules.parent / secret
                shutil.copy2(returned / relative, work / relative)
            shutil.rmtree(returned)
            if check_rules(work, rules):
                verified = True
                print(f"All recipients match the rules after {name}")
                break

        if not verified:
            print(
                "Some secrets still have mismatched recipients; no local files were changed.",
                file=sys.stderr,
            )
            check_rules(work, rules, show_output=True)
            return 1
        for source in sources:
            if not same_source(repo / source, original / source):
                raise RuntimeError(
                    f"Source changed while rekeying: {source}; no local files were changed"
                )

        updated = 0
        for secret in manifest:
            relative = rules.parent / secret
            if not filecmp.cmp(original / relative, work / relative, shallow=False):
                write_changed_file(work / relative, repo / relative)
                updated += 1
        print(f"Updated {updated} encrypted files in {repo}")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
