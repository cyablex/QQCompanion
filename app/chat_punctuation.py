"""Reduce decorative waves while preserving quoted code, URLs and ranges."""

import re


def without_decorative_waves(text):
    protected = re.compile(r"(```[\s\S]*?```|`[^`\n]*`|https?://[^\s]+|~~[^~\n]+~~)")
    parts = protected.split(text)
    for index in range(0, len(parts), 2):
        part = parts[index]

        def replace(match):
            left, right = part[: match.start()], part[match.end() :]
            before, after = left[-1:] or "", right[:1] or ""
            if before == "\\" or (before.isdigit() and after.isdigit()):
                return match.group()
            if (
                before.isascii()
                and before.isalnum()
                and after.isascii()
                and after.isalnum()
            ):
                return match.group()
            numbers = "零一二三四五六七八九十百千万"
            if before and after and before in numbers and after in numbers:
                return match.group()
            if re.search(r"(?:周|星期)[一二三四五六日天]$", left) and re.match(
                r"(?:周|星期)[一二三四五六日天]", right
            ):
                return match.group()
            # Bare ASCII unary operators, home paths and identifiers are meaningful.
            if match.group().startswith("~") and (
                not before
                or before.isspace()
                or after in {"/", "\\", "="}
                or (after.isascii() and after.isalnum())
                or (
                    before.isascii()
                    and before.isalnum()
                    and after.isascii()
                    and after.isalnum()
                )
            ):
                return match.group()
            if before and after and before.isalnum() and after.isalnum():
                return "，"
            return ""

        parts[index] = re.sub(r"[~～]+", replace, part)
    return "".join(parts)
