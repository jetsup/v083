import sys

USAGE = "usage: python3 -m v380cam [gui|web] [options]"


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "gui"
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if mode == "gui":
        from .gui.app import main as run
    elif mode == "web":
        from .web.server import main as run
    else:
        raise SystemExit(USAGE)
    run()


main()
