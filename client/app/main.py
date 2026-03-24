import logging
import sys

from .agent import Agent
from .config import load_settings


class PrettyFormatter(logging.Formatter):
    COLOR = sys.stdout.isatty()
    RESET = "\033[0m" if COLOR else ""
    DIM = "\033[2m" if COLOR else ""
    GREEN = "\033[32m" if COLOR else ""
    YELLOW = "\033[33m" if COLOR else ""
    RED = "\033[31m" if COLOR else ""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        level = record.levelname
        if level == "INFO":
            prefix = f"{self.GREEN}[+]{self.RESET}"
        elif level in ("WARNING", "WARN"):
            prefix = f"{self.YELLOW}[!]{self.RESET}"
        elif level == "ERROR":
            prefix = f"{self.RED}[-]{self.RESET}"
        else:
            prefix = f"{self.DIM}[*]{self.RESET}"
        msg = record.getMessage()
        return f"{self.DIM}{ts}{self.RESET} {prefix} {msg}"


def configure_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(PrettyFormatter())
    root.addHandler(handler)

    logging.getLogger("websocket").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    settings = load_settings()
    Agent(settings).run_forever()


if __name__ == "__main__":
    main()
