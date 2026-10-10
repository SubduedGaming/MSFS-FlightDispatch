import sys


def main() -> int:
    from .server_gui.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
