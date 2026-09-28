"""What she says out loud: words, not symbols."""
from backend.speech_text import for_speech


def test_checklist_reply_is_spoken_as_words():
    reply = ("Done again. All four steps completed:\n\n1. ✅ Created `plan_test` folder\n"
             "2. ✅ Wrote `hello.txt` with \"hi\"\n3. ✅ Read it back\n\n**Clean slate** again! 🎉")
    said = for_speech(reply)
    assert "✅" not in said and "🎉" not in said and "*" not in said and "`" not in said and '"' not in said
    assert "Created plan_test folder" in said and "Wrote hello.txt with hi" in said and "Clean slate again!" in said


def test_links_paths_and_code_are_shortened():
    said = for_speech("Saved to data/sarah_workspace/notes/radahn.md and E:\\AI\\Sarah_V10\\out.txt. "
                      "See https://www.youtube.com/watch?v=abc or [the docs](https://docs.python.org/3/).\n"
                      "```python\nprint('hi')\n```\nThat's it.")
    assert "radahn.md" in said and "sarah_workspace" not in said and "out.txt" in said and "E:" not in said
    assert "youtube.com" in said and "watch?v" not in said and "the docs" in said
    assert "print" not in said and said.endswith("That's it.")


def test_ordinary_slashes_and_numbers_stay():
    said = for_speech("I'm here 24/7, and/or on 9/28/2026 — ready?")
    assert "24/7" in said and "and/or" in said and "9/28/2026" in said


def test_nothing_speakable():
    assert for_speech("✅ 🎉") == ""
