# agenix-rekey-helper

An on-demand flake app for rekeying agenix secrets through hosts that can read the current ciphertext. It is independent of the user's login shell. Both the local controller and remote worker are written in Python. Neither NixOS builds nor deployments trigger rekeying.

## Add the app to a flake

Add `agenix-rekey-helper` as a flake input, then expose an app. For a local checkout while developing:

```nix
{
  inputs.agenix-rekey-helper.url = "path:/path/to/agenix-rekey-helper";

  outputs = inputs@{ nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      apps.${system}.rekey-secrets = inputs.agenix-rekey-helper.lib.mkRekeyApp {
        inherit pkgs;
        rules = "secrets/secrets.nix";
        sources = [ "secrets" "lib/keys.nix" ];
        hosts = {
          workstation_a = {
            target = "alice@workstation-a";
            identity = ".ssh/id_ed25519";
          };
          workstation_b = {
            target = "alice@workstation-b";
            identity = ".ssh/id_ed25519";
          };
          server_a = {
            target = "alice@server-a";
            identity = ".ssh/id_ed25519";
          };
        };
      };
    };
}
```

`rules` and each `sources` entry are relative to the repo passed at runtime. Include the rules file, every imported Nix file, and the `.age` files. Adjust the example paths to match your repository. `identity` is a path on the **remote host**, absolute or relative to that SSH user's home. A host may be named anything; select it with `--host`.

The remote host needs SSH access, `nix`, `tar`, and `mktemp`. The controller starts the worker with `nix shell nixpkgs#python3 nixpkgs#age --command python3 ...`, so Python and `age` do not need to be installed there beforehand. Set `pythonRef = "<flake-ref>#python3";` or `ageRef = "<flake-ref>#age";` in `mkRekeyApp` to select different remote package references. The controller packages its own local Python, agenix, Nix, SSH, and tar.

## Run

From the consumer repo:

```sh
nix run .#rekey-secrets -- --host server_a --dry-run
nix run .#rekey-secrets -- --host server_a --host workstation_a
nix run .#rekey-secrets -- --all
```

Use `--repo /path/to/repo` if running elsewhere. `--dry-run` checks the local paths, evaluates the rules, and reports the selected hosts and secret count. It does not contact hosts or change files. A normal run sends the staged encrypted inputs and recipient lists to each selected host in order. Each host reencrypts only files its identity can decrypt. The controller checks the resulting recipients with `agenix --check` after each host; it stops as soon as every file matches. It copies changed `.age` files back only after that check succeeds and confirms the local sources have not changed during the run.

The app never sends private keys or plaintext to the controller. Plaintext passes through an `age` pipe on the remote host. Failures before the final copy leave the local repo untouched; a failure during that copy can leave some files updated. Encrypted files may remain in a remote temporary directory if SSH cleanup is unavailable. The app does not stage Git changes or deploy anything. Review the resulting diff, then commit or deploy it manually.

Keep the helper flake lock file updated with `nix flake update` when you want newer agenix and nixpkgs revisions.
