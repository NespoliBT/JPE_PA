{
  description = "jpe — a personal assistant with explicit authority boundaries";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      # Load the uv workspace (pyproject.toml + uv.lock).
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      # Build from pre-built wheels where possible.
      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      # Editable overlay for development: the project itself is linked from $REPO_ROOT.
      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
      };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;
        in
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.wheel
              overlay
            ]
          )
      );
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "jpe-dev-env" workspace.deps.all;
        in
        {
          default = pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
              pkgs.git
            ];

            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };

            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
            '';
          };
        }
      );

      packages = forAllSystems (system: {
        default = pythonSets.${system}.mkVirtualEnv "jpe-env" workspace.deps.default;
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/jpe";
        };
      });

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          env = pythonSet.mkVirtualEnv "jpe-check-env" workspace.deps.all;
        in
        {
          default = pkgs.runCommand "jpe-tests" { nativeBuildInputs = [ env ]; } ''
            export REPO_ROOT=$PWD
            cp -r ${./.}/tests ./tests
            cp -r ${./.}/src ./src
            cp ${./.}/pyproject.toml ./pyproject.toml
            pytest -q tests
            touch $out
          '';

          lint = pkgs.runCommand "jpe-lint" { nativeBuildInputs = [ env ]; } ''
            cp -r ${./.}/src ./src
            cp -r ${./.}/tests ./tests
            cp ${./.}/pyproject.toml ./pyproject.toml
            ruff check src tests
            ruff format --check src tests
            touch $out
          '';

          types = pkgs.runCommand "jpe-types" { nativeBuildInputs = [ env ]; } ''
            cp -r ${./.}/src ./src
            cp ${./.}/pyproject.toml ./pyproject.toml
            mypy src
            touch $out
          '';
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
