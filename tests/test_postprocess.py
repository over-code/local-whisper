import pytest

from localwhisper.config import TextConfig
from localwhisper.stt.postprocess import PostProcessor


@pytest.fixture
def processor():
    return PostProcessor(TextConfig())


def test_fillers_are_removed(processor):
    assert processor.process("um, so I was thinking, uh, we ship today.") == \
        "So I was thinking, we ship today. "


def test_filler_removal_does_not_break_german(processor):
    # "um" and "er" are ordinary German words; only comma-marked hesitations go.
    assert processor.process("Ich komme um 5 Uhr, er ist schon da.") == \
        "Ich komme um 5 Uhr, er ist schon da. "


def test_words_containing_fillers_survive(processor):
    assert processor.process("umbrella and hummus").startswith("Umbrella and hummus")


def test_voice_commands(processor):
    assert processor.process("one new line two new paragraph three") == "One\ntwo\n\nthree "


def test_hallucinations_are_dropped(processor):
    for noise in ("Thank you.", "[Music]", "Subtitles by the Amara.org community", "  "):
        assert processor.process(noise) == ""


def test_real_sentence_containing_thank_you_survives(processor):
    assert "Thank you for the review" in processor.process("thank you for the review, Anna.")


def test_replacements_are_whole_word_and_case_insensitive():
    processor = PostProcessor(TextConfig(replacements={"claude code": "Claude Code"}))
    assert "Claude Code" in processor.process("I use CLAUDE code daily.")


def test_whitespace_is_tidied(processor):
    assert processor.process("hello   world , here ( yes ) ") == "Hello world, here (yes) "


def test_switches_are_respected():
    processor = PostProcessor(TextConfig(
        remove_fillers=False, capitalize_first=False, trailing_space=False,
        voice_commands=False, tidy_whitespace=False, drop_hallucinations=False,
    ))
    assert processor.process("um, thank you.") == "um, thank you."


def test_transcript_of_only_punctuation_is_dropped(processor):
    assert processor.process("...") == ""
