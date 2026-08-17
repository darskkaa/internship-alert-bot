from core import load_state, run_once, save_state

if __name__ == "__main__":
    save_state(run_once(load_state()))
