import sys


def main() -> int:
    from .ui.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
