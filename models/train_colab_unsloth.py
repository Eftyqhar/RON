# ==============================================================================
# RON AI - Google Colab Fine-Tuning & GGUF Exporter (Unsloth + Qwen 2.5 0.5B)
# ==============================================================================
# Instructions:
# 1. Open Google Colab (https://colab.research.google.com).
# 2. Set Hardware Accelerator to T4 GPU (Runtime -> Change runtime type -> T4 GPU).
# 3. Upload 'ron_train_dataset.jsonl' to the Colab files panel.
# 4. Copy-paste and run this script or run Cell by Cell.
# 5. It will train in ~10 minutes and automatically download 'ron_qwen_0.5b-unsloth.Q4_K_M.gguf'.
# ==============================================================================

import os
import sys

print(">>> [1/6] Installing Unsloth and optimized dependencies...")
os.system('pip install --quiet "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"')
os.system('pip install --quiet --no-deps "xformers<0.0.27" trl peft accelerate bitsandbytes datasets')

import torch
from unsloth import FastLanguageModel, is_bfloat16_supported
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

# Configuration
MODEL_NAME = "Qwen/Qwen2.5-0.5B-Instruct"  # or "Qwen/Qwen2.5-1.5B-Instruct"
MAX_SEQ_LENGTH = 1024
DATASET_PATH = "ron_train_dataset.jsonl"
OUTPUT_DIR = "ron_checkpoint"
GGUF_NAME = "ron_qwen_0.5b"

print(f">>> [2/6] Loading Base Model: {MODEL_NAME} in 4-bit...")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=MODEL_NAME,
    max_seq_length=MAX_SEQ_LENGTH,
    dtype=None,             # Auto detection (float16 / bfloat16)
    load_in_4bit=True,      # Fast 4-bit quantization (~2.5GB VRAM)
)

print(">>> [3/6] Setting up Parameter-Efficient LoRA Adapters...")
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0,         # Optimized by Unsloth
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

print(f">>> [4/6] Loading & Formatting Dataset from '{DATASET_PATH}'...")
if not os.path.exists(DATASET_PATH):
    raise FileNotFoundError(f"Please upload '{DATASET_PATH}' to Colab before running!")

raw_dataset = load_dataset("json", data_files=DATASET_PATH, split="train")

def format_chatml(batch):
    formatted = []
    for conv in batch["messages"]:
        # Apply Qwen ChatML template
        text = tokenizer.apply_chat_template(conv, tokenize=False, add_generation_prompt=False)
        formatted.append(text)
    return {"text": formatted}

dataset = raw_dataset.map(format_chatml, batched=True)

print(">>> [5/6] Training with SFTTrainer (Unsloth Accelerated)...")
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LENGTH,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        warmup_steps=15,
        max_steps=350,            # Optimal for ~3500 examples (~1.6 epochs, ~8-12 mins on T4 GPU)
        learning_rate=2e-4,
        fp16=not is_bfloat16_supported(),
        bf16=is_bfloat16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=3407,
        output_dir=OUTPUT_DIR,
    ),
)

trainer_stats = trainer.train()
print(">>> Training Completed Successfully!")

print(f">>> [6/6] Converting and Quantizing to GGUF (q4_k_m)...")
# Saves merged weights and directly compiles to GGUF via llama.cpp
model.save_pretrained_gguf(GGUF_NAME, tokenizer, quantization_method="q4_k_m")

gguf_file = f"{GGUF_NAME}-unsloth.Q4_K_M.gguf"
if os.path.exists(gguf_file):
    print(f"\n[SUCCESS] GGUF Model Created: {gguf_file}")
    print(f"File size: {os.path.getsize(gguf_file) / (1024*1024):.2f} MB")
    try:
        from google.colab import files
        print("Initiating automatic download to your PC...")
        files.download(gguf_file)
    except Exception:
        print(f"Please manually download '{gguf_file}' from the Colab file explorer.")
else:
    # Alternative path check
    alt_file = os.path.join(GGUF_NAME, "unsloth.Q4_K_M.gguf")
    if os.path.exists(alt_file):
        print(f"\n[SUCCESS] GGUF Model Created: {alt_file}")
        try:
            from google.colab import files
            files.download(alt_file)
        except Exception:
            pass
