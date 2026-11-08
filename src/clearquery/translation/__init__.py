"""Exploratory Qwen3 adapter; never an approved final translation model."""
from dataclasses import dataclass
from clearquery.contracts import TranslationResult, Error

TRANSLATION_PROMPT = (
    "Translate the following Egyptian Arabic question into English. Return only the translation, without explanations. "
    "Preserve Egyptian-Arabic interrogatives in context (أنهي/انهي asks which; كام asks how much/how many), "
    "business metrics, branch identities, dates, scope, ranking and ambiguity; never answer the question or add facts.\n\n{question}"
)


@dataclass(frozen=True)
class QwenConfig:
    model_id: str = "Qwen/Qwen3-4B-Instruct-2507"
    revision: str | None = "cdbee75f17c01a7cc42f958dc650907174af0554"
    device: str = "cpu"
    cache_dir: str | None = None
    local_files_only: bool = True
    max_new_tokens: int = 256
    enable_thinking: bool = False
    quantization: str | None = None
    prompt: str = TRANSLATION_PROMPT


class QwenTranslation:
    def __init__(self, config=QwenConfig()):
        self.config = config
        self.model = self.tokenizer = None
        self.resolved_revision = None

    def unload(self):
        from clearquery.model_runtime import collect, memory
        before = memory()
        self.model = self.tokenizer = None
        return {"component": "qwen_translation", "before": before, "after": collect()}

    def metadata(self):
        from dataclasses import asdict
        return {**asdict(self.config), "status": "EXPLORATORY", "historical_configuration_verified": False,
                "do_sample": False, "resolved_revision": self.resolved_revision}

    def translate(self, question_ar, trace_id):
        c = self.config
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            if self.model is None:
                common = dict(revision=c.revision, cache_dir=c.cache_dir, local_files_only=c.local_files_only)
                self.tokenizer = AutoTokenizer.from_pretrained(c.model_id, **common)
                if c.quantization == "nf4":
                    if not c.device.startswith("cuda"):
                        raise ValueError("NF4 translation requires CUDA; use quantization=null for CPU")
                    from transformers import BitsAndBytesConfig
                    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16)
                    self.model = AutoModelForCausalLM.from_pretrained(c.model_id, **common,
                        quantization_config=quant, device_map={"": c.device}, dtype=torch.float16).eval()
                elif c.quantization == "checkpoint_nf4":
                    if not c.device.startswith("cuda"):
                        raise ValueError("Published NF4 checkpoint requires CUDA")
                    self.model = AutoModelForCausalLM.from_pretrained(c.model_id, **common,
                        device_map={"": c.device}, dtype="auto").eval()
                elif c.quantization is None:
                    self.model = AutoModelForCausalLM.from_pretrained(c.model_id, **common,
                        dtype=torch.float16 if c.device.startswith("cuda") else torch.float32).to(c.device).eval()
                else:
                    raise ValueError("Unknown translation quantization")
                self.resolved_revision = getattr(self.model.config, "_commit_hash", None)
            prompt = c.prompt.format(question=question_ar)
            text = self.tokenizer.apply_chat_template([{"role": "user", "content": prompt}],
                tokenize=False, add_generation_prompt=True, enable_thinking=c.enable_thinking)
            inputs = self.tokenizer(text, return_tensors="pt").to(c.device)
            with torch.inference_mode():
                tokens = self.model.generate(**inputs, do_sample=False, max_new_tokens=c.max_new_tokens)
            result = self.tokenizer.decode(tokens[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
            if not result.strip():
                raise ValueError("Empty model translation")
            return TranslationResult(trace_id=trace_id, text_ar_original=question_ar, text_en=result,
                engine=f"EXPLORATORY:{c.model_id}", error=None)
        except Exception as exc:
            return TranslationResult(trace_id=trace_id, text_ar_original=question_ar, text_en=None,
                engine=f"EXPLORATORY:{c.model_id}", error=Error(code="TRANSLATION_FAILED", message=f"{type(exc).__name__}: {exc}"))
