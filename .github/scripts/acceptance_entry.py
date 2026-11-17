"""Acceptance-only entry point for a scriptable ForgeOS.exe.

The product entry point, forgeos_build/launcher.py, is unchanged. This wrapper
only adds a hidden "--cli" route so an automated test can run the same
forgeos.cli commands (add, plan, run, list, models) inside the frozen build.
Only the Windows real-model acceptance workflow builds it; it is not a release.
"""
import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from forgeos.cli import main as cli_main
        return cli_main(sys.argv[2:])
    from forgeos_build.launcher import main as launcher_main
    return launcher_main()


if __name__ == "__main__":
    sys.exit(main())
