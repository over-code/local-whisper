from localwhisper.history import History


def test_add_and_read_back():
    history = History(limit=10)
    history.add("first dictation", audio_seconds=2.0, model="small", method="paste:uinput")
    entry = history.latest()
    assert entry.text == "first dictation" and entry.words == 2
    assert entry.method == "paste:uinput"
    assert "-" in entry.when()


def test_blank_text_is_not_stored():
    history = History()
    assert history.add("   ") == -1
    assert history.recent() == []


def test_trims_to_the_limit():
    history = History(limit=3)
    for index in range(6):
        history.add(f"entry {index}")
    entries = history.recent(50)
    assert len(entries) == 3
    assert entries[0].text == "entry 5"


def test_search_and_delete():
    history = History()
    history.add("buy milk")
    history.add("write the report")
    assert [e.text for e in history.recent(search="report")] == ["write the report"]
    history.delete(history.latest().id)
    assert [e.text for e in history.recent()] == ["buy milk"]


def test_stats_and_clear():
    history = History()
    history.add("one two three", audio_seconds=30.0)
    stats = history.stats()
    assert stats == {"entries": 1.0, "audio_seconds": 30.0, "words": 3.0}
    history.clear()
    assert history.stats()["entries"] == 0.0
