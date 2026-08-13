from rapidfuzz import fuzz

def pick_relevant_tables(question: str, kb_tables: dict, topk=2):
    q = (question or "").lower()
    scored = []
    for t, meta in kb_tables.items():
        if meta.get("is_system"):
            continue
        text = (meta.get("description","") + " " + " ".join(meta.get("synonyms", [])) + " " + t).lower()
        scored.append((fuzz.token_set_ratio(q, text), t))
    scored.sort(reverse=True)
    return [t for _, t in scored[:topk]]

def pick_examples(question: str, kb_examples: list, topk=3):
    q = (question or "").lower()
    scored = []
    for ex in kb_examples:
        score = fuzz.token_set_ratio(q, (ex.get("nl_question") or "").lower())
        scored.append((score, ex))
    scored.sort(reverse=True)
    return [ex for _, ex in scored[:topk]]