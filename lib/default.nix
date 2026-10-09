{ agenix }:
{
  pkgs,
  rules,
  sources,
  hosts,
  name ? "agenix-rekey-helper",
  ageRef ? "nixpkgs#age",
  pythonRef ? "nixpkgs#python3",
}:
let
  system = pkgs.stdenv.hostPlatform.system;
  configFile = pkgs.writeText "${name}-config.json" (
    builtins.toJSON {
      inherit
        rules
        sources
        hosts
        ageRef
        pythonRef
        ;
    }
  );
  runtimePath = pkgs.lib.makeBinPath [
    agenix.packages.${system}.default
    pkgs.gnutar
    pkgs.nix
    pkgs.openssh
  ];
  package = pkgs.writeTextFile {
    inherit name;
    destination = "/bin/${name}";
    executable = true;
    text = /* python */ ''
      #!${pkgs.python3}/bin/python3
      import os
      import sys

      os.environ["PATH"] = "${runtimePath}:" + os.environ.get("PATH", "")
      os.environ["REKEY_CONFIG"] = "${configFile}"
      os.environ["REKEY_WORKER"] = "${../scripts/worker.py}"
      os.execv("${pkgs.python3}/bin/python3", ["python3", "${../scripts/controller.py}", *sys.argv[1:]])
    '';
  };
in
assert builtins.isString rules && rules != "";
assert builtins.isList sources && sources != [ ];
assert builtins.isAttrs hosts;
{
  type = "app";
  program = "${package}/bin/${name}";
  meta.description = "Rekey agenix files on declared SSH hosts, then verify and copy them back";
}
