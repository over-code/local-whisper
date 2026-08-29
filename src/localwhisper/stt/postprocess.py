"""Turn a raw Whisper transcript into text you would have typed.

Whisper already punctuates and capitalises, so this stage is deliberately
small: strip the noises a microphone picks up, honour a few spoken commands,
apply the user's vocabulary, and never mangle the sentence in the process.
Every step is individually switchable in the settings.
"""

from __future__ import annotations

import re

from ..config import TextConfig

#: Sounds that carry no meaning in any language we care about. Matched as whole
#: words only, so "umbrella" survives.
FILLERS = (
    "uh", "uhh", "uhm", "umm", "erm", "hmm", "mhm", "mm-hmm",
    "ähm", "äh", "öhm", "ähh",
)

#: Real words in some languages ("um 5 Uhr", "er sagte", "hm ja"), so these are
#: only dropped when Whisper marked them as a hesitation with a comma, or when
#: they open the utterance. That keeps German dictation intact.
AMBIGUOUS_FILLERS = ("um", "er", "hm", "eh", "ah")

#: Whisper's favourite inventions when handed silence or noise. We only drop
#: the transcript when the *whole* thing matches one of these — a sentence that
#: merely contains "thank you" is real speech.
HALLUCINATIONS = (
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.",
    "you", "bye.", "bye", ".", "!", "?", "...",
    "please subscribe", "subscribe to my channel",
    "untertitelung des zdf, 2020", "untertitel im auftrag des zdf, 2021",
    "untertitel von stephanie geiges", "vielen dank.", "danke.",
    "amara.org community", "subtitles by the amara.org community",
    "sous-titrage société radio-canada", "♪", "[music]", "(music)",
    "[silence]", "(silence)",
)

#: Spoken commands → what they insert. Order matters: longer phrases first so
#: "new paragraph" is not eaten by "new line".
VOICE_COMMANDS: tuple[tuple[str, str], ...] = (
    ("new paragraph", "\n\n"),
    ("neuer absatz", "\n\n"),
    ("new line", "\n"),
    ("newline", "\n"),
    ("neue zeile", "\n"),
    ("zeilenumbruch", "\n"),
    ("tab key", "\t"),
)

_MULTISPACE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%…])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[\{«])\s+")
_SPACE_BEFORE_CLOSE = re.compile(r"\s+([)\]\}»])")
_BLANK_LINES = re.compile(r"\n{3,}")


class PostProcessor:
    """Stateless text cleanup driven by :class:`TextConfig`."""

    def __init__(self, config: TextConfig) -> None:
        self.config = config
        joined = "|".join(re.escape(f) for f in FILLERS)
        self._filler_pattern = re.compile(rf"(?<!\w)(?:{joined})(?!\w)\s*[,]?", re.IGNORECASE)
        ambiguous = "|".join(re.escape(f) for f in AMBIGUOUS_FILLERS)
        # "das ist, äh, ein Test" — drop the filler *and* its pair of commas.
        self._between_commas = re.compile(
            rf",\s*(?:{joined}|{ambiguous})(?!\w)\s*,", re.IGNORECASE
        )
        # Either at the very start of the text, or followed by a comma.
        self._ambiguous_pattern = re.compile(
            rf"(?:^\s*(?:{ambiguous})(?!\w)\s*,?\s*|(?<!\w)(?:{ambiguous})(?!\w)\s*,\s*)",
            re.IGNORECASE,
        )

    def process(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        if self.config.drop_hallucinations and self.is_hallucination(text):
            return ""

        if self.config.voice_commands:
            text = self._apply_voice_commands(text)
        if self.config.remove_fillers:
            text = self._remove_fillers(text)
        if self.config.replacements:
            text = self._apply_replacements(text)
        if self.config.tidy_whitespace:
            text = self._tidy(text)
        if self.config.capitalize_first:
            text = self._capitalize_first(text)
        if not text:
            return ""
        if self.config.trailing_space and not text.endswith(("\n", " ", "\t")):
            text += " "
        return text

    # ----------------------------------------------------------------- steps

    @staticmethod
    def is_hallucination(text: str) -> bool:
        stripped = text.strip().lower()
        if not stripped:
            return True
        # Whisper wraps hallucinated captions in brackets surprisingly often.
        if re.fullmatch(r"[\[\(].{0,40}[\]\)]", stripped):
            return True
        return stripped in HALLUCINATIONS

    def _apply_voice_commands(self, text: str) -> str:
        for phrase, replacement in VOICE_COMMANDS:
            # "new line," / "new line." — Whisper punctuates the command too.
            pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)[,.!]?", re.IGNORECASE)
            text = pattern.sub(replacement, text)
        return text

    def _remove_fillers(self, text: str) -> str:
        cleaned = self._between_commas.sub(",", text)
        cleaned = self._filler_pattern.sub("", cleaned)
        cleaned = self._ambiguous_pattern.sub(" ", cleaned)
        # Removing a leading filler leaves ", and so on" — tidy that up.
        cleaned = re.sub(r"^[\s,]+", "", cleaned)
        return _MULTISPACE.sub(" ", cleaned)

    def _apply_replacements(self, text: str) -> str:
        for source, target in self.config.replacements.items():
            if not source:
                continue
            pattern = re.compile(r"(?<!\w)" + re.escape(source) + r"(?!\w)", re.IGNORECASE)
            text = pattern.sub(target.replace("\\", "\\\\"), text)
        return text

    @staticmethod
    def _tidy(text: str) -> str:
        text = _MULTISPACE.sub(" ", text)
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
        text = _SPACE_AFTER_OPEN.sub(r"\1", text)
        text = _SPACE_BEFORE_CLOSE.sub(r"\1", text)
        text = _BLANK_LINES.sub("\n\n", text)
        # Keep intentional newlines, drop trailing spaces on each line.
        return "\n".join(line.strip() for line in text.split("\n")).strip()

    @staticmethod
    def _capitalize_first(text: str) -> str:
        for index, char in enumerate(text):
            if char.isalpha():
                return text[:index] + char.upper() + text[index + 1:]
            if not char.isspace() and char not in "\"'([«":
                break
        return text
