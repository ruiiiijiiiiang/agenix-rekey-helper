{
  description = "On-demand, shell-independent rekeying for agenix secrets across SSH hosts";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    agenix = {
      url = "github:ryantm/agenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { agenix, nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
    in
    {
      lib.mkRekeyApp = import ./lib { inherit agenix; };

      devShells = nixpkgs.lib.genAttrs systems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.ruff
              pkgs.nixfmt
              pkgs.age
              agenix.packages.${system}.default
              pkgs.nix
              pkgs.openssh
              pkgs.gnutar
            ];
          };
        }
      );
    };
}
