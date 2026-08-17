import time

from core import POLL_INTERVAL, load_state, log, run_once, save_state


def main() -> None:
    state = load_state()
    while True:
        try:
            state = run_once(state)
            save_state(state)
        except Exception:
            log.exception("Poll cycle failed, will retry next interval")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
