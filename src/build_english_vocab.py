"""Build the English word list used to tell English queries from romanized Bengali/Hindi ones.

Words come from the English chunks already ingested into PostgreSQL, plus the system word list
(/usr/share/dict/words) when present. Re-run after ingesting new English books.
"""

from __future__ import annotations

import os
import sys

from clients import get_postgres_connection
from config import settings
from translit import english_vocabulary_words

SYSTEM_WORD_LIST = "/usr/share/dict/words"


def main() -> int:
    """Write the vocabulary file and return a process exit code."""
    try:
        connection = get_postgres_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT chunk_text FROM chunks WHERE COALESCE(language_code, 'en') = 'en'")
            texts = [row[0] for row in cursor.fetchall()]
        connection.close()
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Could not read chunks: {exc}", file=sys.stderr)
        return 1

    words = set(english_vocabulary_words(texts))
    corpus_count = len(words)
    if os.path.isfile(SYSTEM_WORD_LIST):
        with open(SYSTEM_WORD_LIST, encoding="utf-8", errors="ignore") as handle:
            words.update(w for w in (line.strip().lower() for line in handle) if w.isalpha() and len(w) >= 2)

    os.makedirs(os.path.dirname(os.path.abspath(settings.english_vocab_path)), exist_ok=True)
    with open(settings.english_vocab_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sorted(words)) + "\n")
    print(f"[OSISS] Wrote {len(words)} words ({corpus_count} from {len(texts)} chunks) to {settings.english_vocab_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
