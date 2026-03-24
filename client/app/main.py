from .agent import Agent
from .config import load_settings
from .logger import configure_logging

def main() -> None:
    configure_logging()
    settings = load_settings()
    Agent(settings).run_forever()

if __name__ == "__main__":
    main()
