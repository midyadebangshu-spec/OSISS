# Evaluation questions

`questions.json` is the original 109-question set (definition-style, partly written from the OCR text, so it is a
ceiling). `questions_v2.json` is the independent set: questions written for **randomly drawn pages**, with the type of
question recorded so weak spots show up.

## Writing the independent set

1. `.venv/bin/python tests/eval/sample_pages.py --count 60 --seed 7 > pages.json` (pages spread over all books).
2. For each page write 1-2 questions *without* copying the page's wording where possible. Mix the types:
   `factoid`, `paraphrase` (different words than the page), `explain` (why/how), `compare`, `multi-para` (answer spans
   paragraphs or a page break), `negative` (a plausible question the corpus does not answer; set `no_answer: true`,
   `expected: []`, no `answer_contains`).
3. Aim for 15+ questions in each language group (`en`, `bn`, `hi`, `roman-bn`, `roman-hi`, `mixed`, `bn->en`,
   `hi->en`, `en->bn`, `en->hi`) and about 20 negatives.
4. Give every question a `split`: `tune` (about 60 %) or `heldout`. Tune settings on `tune`, then report `heldout` once.
5. `.venv/bin/python tests/eval_search.py --questions tests/eval/questions_v2.json --validate`

```json
{
  "id": "v2-001", "lang": "en", "group": "hi->en", "type": "paraphrase", "split": "tune",
  "query": "...",
  "expected": [{"file": "Part of the PDF file name", "pages": [12, 13]}],
  "answer_contains": "short keyword that must be in a correct answer",
  "answer_quote": "optional: the verbatim passage a good quote should cover"
}
```

## Reference run

```bash
.venv/bin/python tests/eval_search.py --questions tests/eval/questions_v2.json --mode full --depth 0 --save tests/eval/baseline_v2.json
.venv/bin/python tests/eval_search.py --questions tests/eval/questions_v2.json --mode full --depth 0 --split heldout --baseline tests/eval/baseline_v2.json
```
