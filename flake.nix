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
    { agenix, ... }:
    {
      lib.mkRekeyApp = import ./lib { inherit agenix; };
    };
}
