{
  description = "Research-first, guard-railed RXRX/TEM trading harness";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  outputs = { nixpkgs, ... }:
    let
      system = "aarch64-darwin";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.python3 pkgs.ruff ];
        shellHook = ''
          export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
          echo "quanttrader dev shell"
          echo "  tests:    python3 -m unittest discover -s tests"
          echo "  study:    python3 scripts/run_backtest.py"
          echo "  monitor:  python3 scripts/run_monitor.py --interval hourly --days 90"
        '';
      };
    };
}
