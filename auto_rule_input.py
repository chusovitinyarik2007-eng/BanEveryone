"""Collect Telegram message fragments for AUTO routing rules.

A Telegram client can split a long paste into multiple messages. Never infer
completion from the first valid message: validation and saving happen only after
an explicit confirmation in the service bot.
"""

import re

RULE_PREFIXES = ("domain:", "full:", "regexp:", "geosite:")
MAX_RULES = 10_000
MAX_RULE_LENGTH = 512
MAX_DRAFT_CHARS = 6_000_000


class RuleInputError(ValueError):
    """The complete draft is invalid; the caller must keep it for correction."""


def add_part(parts, message_id, text):
    """Return a new list of pieces, deduplicated by Telegram message ID.

    Sorting by ID means concurrently delivered updates are reassembled in their
    original chat order, even if handlers receive them out of order.
    """
    if not isinstance(message_id, int) or not isinstance(text, str) or not text.strip():
        raise RuleInputError("Отправьте непустое текстовое сообщение с правилами.")
    if any(part["id"] == message_id for part in parts):
        return parts
    if sum(len(part["text"]) for part in parts) + len(text) > MAX_DRAFT_CHARS:
        raise RuleInputError("Превышен лимит черновика. Ничего не сохранено. Используйте /reset или /cancel.")
    return sorted([*parts, {"id": message_id, "text": text}], key=lambda part: part["id"])


def _likely_complete_rule(last_line):
    """Conservative cue for a rule's end, never based on a server lookup.

    Short pasted messages normally contain whole rules. Only join a fragment
    when the previous message was near Telegram's text cap or its last rule
    is visibly incomplete (e.g. 'dom' or 'domain:site.e').
    """
    value = last_line.strip()
    if value.startswith("domain:"):
        name = value[7:]
        return (bool(re.fullmatch(r"[a-zA-Z0-9*_.-]+", name))
                and not re.search(r"\.[a-zA-Z]$", name)
                and not re.search(r"\.[a-zA-Z0-9]*-$", name))
    if value.startswith(("full:", "regexp:", "geosite:")):
        return len(value.split(":", 1)[1]) > 0
    return False


def assembled_text(parts):
    """Preserve chat order and avoid silently corrupting separate messages.

    A new message starting with a rule prefix is a new line. Pieces split at
    the Telegram 4096-character limit or visibly incomplete rule fragments are
    joined; independent short invalid lines stay separate and fail validation
    instead of becoming part of the previous domain silently.
    """
    result = ""
    previous_part_length = 0
    for part in sorted(parts, key=lambda item: item["id"]):
        text = part["text"]
        if result and not result.endswith(("\n", "\r")):
            last_line = result.rsplit("\n", 1)[-1]
            if text.lstrip().startswith(RULE_PREFIXES):
                result += "\n"
            elif previous_part_length < 3800 and _likely_complete_rule(last_line):
                result += "\n"
        result += text
        previous_part_length = len(text)
    return result


def draft_statistics(parts):
    """Count nonempty lines as a progress indicator, not as saved rules."""
    text = assembled_text(parts)
    return len(parts), len([line for line in text.splitlines() if line.strip()])


def parse_rules(parts):
    """Validate the entire buffered draft; never silently drop invalid rules."""
    if not parts:
        raise RuleInputError("Вы ещё не отправили правила. Отправьте их или используйте /cancel.")
    text = assembled_text(parts)
    if text.strip() == "-":
        return []
    rules = [line.strip() for line in text.splitlines() if line.strip()]
    if not rules:
        raise RuleInputError("Черновик пуст. Отправьте правила или используйте /cancel.")
    if len(rules) > MAX_RULES:
        raise RuleInputError(f"Получено {len(rules)} правил (максимум {MAX_RULES}). Ничего не сохранено.")
    invalid = [(index, line) for index, line in enumerate(rules, 1)
               if len(line) > MAX_RULE_LENGTH or not line.startswith(RULE_PREFIXES)
               or not line[len(line.split(':', 1)[0])+1:].strip()]
    if invalid:
        positions = ", ".join(str(index) for index, _ in invalid[:12])
        raise RuleInputError(
            f"Некорректные строки: {positions}. Разрешены domain:, full:, regexp:, geosite: "
            f"(до {MAX_RULE_LENGTH} символов на правило). Ничего не сохранено. "
            "Исправьте ввод через /reset или отмените через /cancel."
        )
    return rules
