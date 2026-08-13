import json
from pathlib import Path
from transformers import T5Tokenizer, T5ForConditionalGeneration

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = BASE_DIR / "models" / "meaning_t5"

tokenizer = None
model = None

if MODEL_DIR.exists():
    tokenizer = T5Tokenizer.from_pretrained(MODEL_DIR)
    model = T5ForConditionalGeneration.from_pretrained(MODEL_DIR)
else:
    print(f"Warning: Model directory {MODEL_DIR} not found. ML pipeline will be skipped.")
def parse_model_output(text: str):
    text = text.strip()

    # try normal parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # try if model returned escaped JSON string
    try:
        unescaped = bytes(text, "utf-8").decode("unicode_escape")
        return json.loads(unescaped)
    except Exception:
        pass

    # try wrapping braces if missing
    try:
        if not text.startswith("{"):
            text = "{" + text
        if not text.endswith("}"):
            text = text + "}"
        return json.loads(text)
    except Exception:
        pass

    return {
        "raw_output": text,
        "parse_error": True
    }

def predict_meaning(user_input: str):
    if tokenizer is None or model is None:
        raise FileNotFoundError("Meaning model not found. ML pipeline skipped.")
        
    prompt = f"extract meaning: {user_input.strip()}"
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=128)

    outputs = model.generate(
        **inputs,
        max_length=128,
        num_beams=4,
        early_stopping=True
    )

    text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    return parse_model_output(text)

if __name__ == "__main__":
    while True:
        q = input("Enter query: ").strip()
        if not q:
            break
        result = predict_meaning(q)
        print(json.dumps(result, indent=2))