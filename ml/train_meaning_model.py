import json
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split
from datasets import Dataset
from transformers import (
    T5Tokenizer,
    T5ForConditionalGeneration,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
    DataCollatorForSeq2Seq,
)

# =========================
# Paths
# =========================
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "nlidb_advanced_training_dataset_10000.csv"
MODEL_OUT = BASE_DIR / "models" / "meaning_t5"

# =========================
# Config
# =========================
MODEL_NAME = "t5-small"
MAX_INPUT_LEN = 128
MAX_TARGET_LEN = 128
TEST_SIZE = 0.1
RANDOM_STATE = 42

# =========================
# Helpers
# =========================
def safe_str(v):
    if pd.isna(v):
        return ""
    return str(v).strip()

def row_to_target_json(row):
    target = {
        "intent": safe_str(row["intent"]),
        "target_table": safe_str(row["target_table"]),
        "target_columns": safe_str(row["target_columns"]),
        "filters": safe_str(row["filters"]),
        "joins": safe_str(row["joins"]),
        "group_by": safe_str(row["group_by"]),
        "sort_column": safe_str(row["sort_column"]),
        "sort_order": safe_str(row["sort_order"]),
        "limit_value": safe_str(row["limit_value"]),
        "aggregate": safe_str(row["aggregate"]),
    }
    return json.dumps(target, ensure_ascii=False, separators=(",", ":"))

def build_input_text(row):
    # Keep prompt simple and stable
    return f"extract meaning: {safe_str(row['user_input'])}"

def preprocess_examples(examples, tokenizer):
    model_inputs = tokenizer(
        examples["input_text"],
        max_length=MAX_INPUT_LEN,
        truncation=True,
        padding="max_length",
    )

    labels = tokenizer(
        text_target=examples["target_text"],
        max_length=MAX_TARGET_LEN,
        truncation=True,
        padding="max_length",
    )

    model_inputs["labels"] = labels["input_ids"]
    return model_inputs

# =========================
# Load CSV
# =========================
df = pd.read_csv(DATA_PATH)

# Keep only needed columns
required_cols = [
    "user_input",
    "intent",
    "target_table",
    "target_columns",
    "filters",
    "joins",
    "group_by",
    "sort_column",
    "sort_order",
    "limit_value",
    "aggregate",
]
df = df[required_cols].copy()

# Build training pairs
df["input_text"] = df.apply(build_input_text, axis=1)
df["target_text"] = df.apply(row_to_target_json, axis=1)

train_df, val_df = train_test_split(
    df[["input_text", "target_text"]],
    test_size=TEST_SIZE,
    random_state=RANDOM_STATE,
    shuffle=True,
)

train_ds = Dataset.from_pandas(train_df.reset_index(drop=True))
val_ds = Dataset.from_pandas(val_df.reset_index(drop=True))

# =========================
# Tokenizer + Model
# =========================
tokenizer = T5Tokenizer.from_pretrained(MODEL_NAME)
model = T5ForConditionalGeneration.from_pretrained(MODEL_NAME)

tokenized_train = train_ds.map(lambda x: preprocess_examples(x, tokenizer), batched=True)
tokenized_val = val_ds.map(lambda x: preprocess_examples(x, tokenizer), batched=True)

data_collator = DataCollatorForSeq2Seq(tokenizer=tokenizer, model=model)

# =========================
# Training args
# =========================
training_args = Seq2SeqTrainingArguments(
    output_dir=str(MODEL_OUT),
    evaluation_strategy="epoch",
    save_strategy="epoch",
    logging_strategy="steps",
    logging_steps=100,
    learning_rate=3e-4,
    per_device_train_batch_size=8,
    per_device_eval_batch_size=8,
    weight_decay=0.01,
    save_total_limit=2,
    num_train_epochs=5,
    predict_with_generate=True,
    fp16=False,   # keep False for Mac CPU
    bf16=False,
    load_best_model_at_end=True,
    report_to="none",
)

trainer = Seq2SeqTrainer(
    model=model,
    args=training_args,
    train_dataset=tokenized_train,
    eval_dataset=tokenized_val,
    tokenizer=tokenizer,
    data_collator=data_collator,
)

# =========================
# Train
# =========================
trainer.train()

# =========================
# Save final model
# =========================
trainer.save_model(str(MODEL_OUT))
tokenizer.save_pretrained(str(MODEL_OUT))

print(f"Model saved to: {MODEL_OUT}")