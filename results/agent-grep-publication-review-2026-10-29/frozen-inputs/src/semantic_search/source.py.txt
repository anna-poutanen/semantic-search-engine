"""Physical source lines; Unicode paragraph separators inside strings are data."""
import re


def physical_lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]
