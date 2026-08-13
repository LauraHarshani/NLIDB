import re
from dataclasses import dataclass
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

@dataclass
class GenResult:
    ok: bool
    sql: str | None = None
    explanation: str = ""
    error: str | None = None

def clean_sql_only_first_select(text: str) -> str:
    """
    Many HF text-to-sql models output junk around SQL.
    This keeps ONLY the first SELECT ... statement.
    """
    if not text:
        return ""

    t = text.strip()

    # Remove code fences
    t = re.sub(r"```.*?```", "", t, flags=re.S).strip()

    # Find first SELECT
    m = re.search(r"(?is)\bselect\b", t)
    if not m:
        return ""

    t = t[m.start():]

    # Cut at first semicolon (if present)
    semi = t.find(";")
    if semi != -1:
        t = t[:semi]

    # Remove weird control chars
    t = re.sub(r"[\x00-\x1f\x7f]", " ", t).strip()

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t

class T5Text2SQL:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        self.model.to("cpu")
        self.model.eval()

    def _schema_text(self, schema: dict) -> str:
        # avoid word "tables:" to reduce "FROM tables"
        lines = []
        for t, meta in schema.items():
            cols = meta.get("columns", [])
            if cols:
                lines.append(f"{t}({', '.join(cols)})")
        return "\n".join(lines)

    def _prompt(self, question: str, schema: dict) -> str:
        return (
            "Convert the question into ONE MySQL SELECT query.\n"
            "Use ONLY the schema below.\n"
            "Do NOT invent table or column names.\n"
            "Return ONLY SQL.\n\n"
            f"Schema:\n{self._schema_text(schema)}\n\n"
            f"Question: {question}\n"
            "SQL:"
        )

    def generate_sql(self, question: str, schema: dict) -> GenResult:
        if not schema:
            return GenResult(ok=False, error="empty_schema")

        prompt = self._prompt(question, schema)
        inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True)

        try:
            out = self.model.generate(
                **inputs,
                max_new_tokens=128,
                num_beams=4,
                early_stopping=True
            )
            raw = self.tokenizer.decode(out[0], skip_special_tokens=True)
            sql = clean_sql_only_first_select(raw)

            if not sql.lower().startswith("select"):
                return GenResult(ok=False, error="no_select_generated")

            return GenResult(ok=True, sql=sql, explanation="t5 generated")

        except Exception as e:
            return GenResult(ok=False, error=str(e))