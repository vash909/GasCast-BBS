"""ANSI helpers for the GasCast BBS"""

from __future__ import annotations

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

FG = {
    "black": "\x1b[30m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "white": "\x1b[37m",
    "bright_black": "\x1b[90m",
    "bright_red": "\x1b[91m",
    "bright_green": "\x1b[92m",
    "bright_yellow": "\x1b[93m",
    "bright_blue": "\x1b[94m",
    "bright_magenta": "\x1b[95m",
    "bright_cyan": "\x1b[96m",
    "bright_white": "\x1b[97m",
}


def c(text: str, *styles: str) -> str:
    return "".join(styles) + text + RESET


def hr(width: int = 72, char: str = "═") -> str:
    return char * width


def box(title: str, lines: list[str], width: int = 72) -> str:
    title_text = f" {title} "
    top = f"╔{title_text:═<{width-1}}╗"
    body = []
    for line in lines:
        body.append(f"║ {line[: width - 4]:<{width-4}} ║")
    bottom = f"╚{hr(width - 2)}╝"
    return "\n".join([top, *body, bottom])


def banner() -> str:
    art = [
        " ██████╗  █████╗ ███████╗ ██████╗ █████╗ ███████╗████████╗",
        "██╔════╝ ██╔══██╗██╔════╝██╔════╝██╔══██╗██╔════╝╚══██╔══╝",
        "██║  ███╗███████║███████╗██║     ███████║███████╗   ██║   ",
        "██║   ██║██╔══██║╚════██║██║     ██╔══██║╚════██║   ██║   ",
        "╚██████╔╝██║  ██║███████║╚██████╗██║  ██║███████║   ██║   ",
        " ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ",
    ]
    return "\n".join(c(line, FG["bright_cyan"], BOLD) for line in art)


def prompt(user: str, channel: str) -> str:
    return (
        c(user, FG["bright_green"], BOLD)
        + c("@", FG["bright_black"])
        + c(channel, FG["bright_yellow"], BOLD)
        + c(" > ", FG["bright_white"])
    )
