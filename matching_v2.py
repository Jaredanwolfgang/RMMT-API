import ast
import json
import math
import os
from dataclasses import dataclass
from typing import Any

import numpy as np


DEFAULT_ALPHA = 0.7
DEFAULT_BETA = 0.3
NEUTRAL_SCORE = 0.5

NUMERIC_FIELD_KEYWORDS = {
    "sleep_time": ("睡觉", "sleep", "turn off lights"),
    "wake_time": ("起床", "wake", "get up"),
    "routine": ("作息规律", "regular", "schedule"),
    "cleanliness": ("整洁", "cleanliness"),
    "cleaning_frequency": ("打扫", "clean", "shared"),
    "smell_sensitivity": ("气味", "垃圾", "外卖", "allergies"),
    "quietness": ("安静", "quiet"),
    "noise_sensitivity": ("噪声", "声音", "noise"),
    "night_computer": ("电脑", "游戏", "语音", "video", "gaming"),
    "roommate_relation": ("舍友关系", "communicate"),
    "social_energy": ("社交能量", "social"),
    "visitor_acceptance": ("访客", "朋友来宿舍", "visitor"),
    "dorm_study": ("学习", "工作", "study"),
    "dorm_state": ("宿舍状态", "environment", "weekend"),
}

TEXT_FIELD_KEYWORDS = {
    "self_intro": ("介绍", "认识你", "self", "intro"),
    "interests": ("兴趣", "爱好", "hobbies"),
    "dorm_atmosphere": ("宿舍氛围", "room atmosphere", "environment"),
    "representative_thing": ("代表你", "电影", "书", "游戏", "地点", "thing"),
    "roommate_note": ("提前知道", "特殊", "notes", "allergies"),
}

FIELD_LABELS = {
    "sleep_time": "睡觉时间",
    "wake_time": "起床时间",
    "routine": "作息规律",
    "cleanliness": "整洁程度",
    "cleaning_frequency": "打扫频率",
    "smell_sensitivity": "气味/垃圾敏感度",
    "quietness": "自身安静程度",
    "noise_sensitivity": "噪声敏感度",
    "night_computer": "夜间电脑/游戏/语音频率",
    "roommate_relation": "理想舍友关系",
    "social_energy": "社交能量",
    "visitor_acceptance": "访客接受程度",
    "dorm_study": "宿舍学习/工作频率",
    "dorm_state": "宿舍状态偏好",
    "self_intro": "自我介绍",
    "interests": "兴趣爱好",
    "dorm_atmosphere": "宿舍氛围",
    "representative_thing": "代表自己的事物",
    "roommate_note": "希望舍友知道的事",
}


@dataclass
class MatchV2Config:
    alpha: float = DEFAULT_ALPHA
    beta: float = DEFAULT_BETA

    @classmethod
    def from_env(cls):
        return cls(
            alpha=_read_float_env("MATCHING_V2_ALPHA", DEFAULT_ALPHA),
            beta=_read_float_env("MATCHING_V2_BETA", DEFAULT_BETA),
        ).normalized()

    def normalized(self):
        alpha = max(0.0, float(self.alpha))
        beta = max(0.0, float(self.beta))
        total = alpha + beta
        if total <= 0:
            return MatchV2Config()
        return MatchV2Config(alpha=alpha / total, beta=beta / total)


def calculate_match_v2(user_a, user_b, config=None, embedding_model=None, commit_vectors=None):
    """Calculate MVP roommate match score for two Student-like objects.

    The function accepts real SQLAlchemy Student models or small fake objects
    with a ``questionnaire_answers`` attribute, which keeps tests lightweight.
    """
    cfg = (config or MatchV2Config.from_env()).normalized()
    numeric_result = calculate_numeric_score(user_a, user_b)
    text_score = calculate_text_score(
        user_a,
        user_b,
        config=cfg,
        embedding_model=embedding_model,
        commit_vectors=commit_vectors,
    )
    final_score = _clamp(numeric_result["numeric_score"]) * cfg.alpha + _clamp(text_score) * cfg.beta
    match_score = round(_clamp(final_score) * 100, 1)

    similar = [item["label"] for item in numeric_result["fields"] if item["similarity"] >= 0.75][:3]
    different = [item["label"] for item in reversed(numeric_result["fields"]) if item["similarity"] < 0.5][:3]

    return {
        "algorithm": "matching_v2",
        "match_score": match_score,
        "score": match_score,
        "numeric_score": round(_clamp(numeric_result["numeric_score"]), 4),
        "text_score": round(_clamp(text_score), 4),
        "top_similar_fields": similar,
        "top_different_fields": different,
        "explanation_features": {
            "similar": similar,
            "different": different,
        },
    }


def calculate_numeric_score(user_a, user_b):
    answers_a = _answers_by_item(user_a)
    answers_b = _answers_by_item(user_b)
    weighted_sum = 0.0
    weight_sum = 0.0
    field_scores = []

    for item_id in sorted(set(answers_a.keys()) & set(answers_b.keys())):
        answer_a = answers_a[item_id]
        answer_b = answers_b[item_id]
        if not _is_numeric_match_field(answer_a):
            continue

        similarity = _question_similarity(answer_a, answer_b)
        if similarity is None:
            continue

        weight = _combined_weight(answer_a, answer_b)
        weighted_sum += similarity * weight
        weight_sum += weight
        field_scores.append({
            "item_id": item_id,
            "label": _field_label(answer_a),
            "similarity": similarity,
            "weight": weight,
        })

    field_scores.sort(key=lambda item: item["similarity"], reverse=True)
    if weight_sum <= 0:
        return {"numeric_score": NEUTRAL_SCORE, "fields": field_scores}
    return {"numeric_score": weighted_sum / weight_sum, "fields": field_scores}


def calculate_text_score(user_a, user_b, config=None, embedding_model=None, commit_vectors=None):
    cfg = (config or MatchV2Config.from_env()).normalized()
    text_answers_a = _text_answers_by_item(user_a)
    text_answers_b = _text_answers_by_item(user_b)
    shared_item_ids = sorted(set(text_answers_a.keys()) & set(text_answers_b.keys()))
    if not shared_item_ids:
        return NEUTRAL_SCORE

    field_scores = []
    try:
        model = embedding_model or _get_embedding_model()
        for item_id in shared_item_ids:
            vector_a = _answer_vector(text_answers_a[item_id], model)
            vector_b = _answer_vector(text_answers_b[item_id], model)
            cosine = _cosine_similarity(vector_a, vector_b)
            if cosine is None:
                continue
            field_scores.append(_calibrate_text_cosine(cosine, cfg))
    except Exception:
        return NEUTRAL_SCORE

    if not field_scores:
        return NEUTRAL_SCORE

    if commit_vectors:
        commit_vectors()

    return sum(field_scores) / len(field_scores)


def build_text_profile(user):
    parts = []
    text_answers = []
    for answer in _answers_by_item(user).values():
        if not _is_text_match_field(answer):
            continue
        raw = _answer_value(answer)
        if raw is None or str(raw).strip() == "":
            continue
        label = _field_label(answer)
        text = _stringify_answer(raw)
        parts.append(f"- {label}: {text}")
        text_answers.append(answer)
    if not parts:
        return "", []
    return "用户画像：\n" + "\n".join(parts), text_answers


def _answer_vector(answer, embedding_model):
    vector = _read_vector(answer)
    if vector is not None:
        return vector
    text = _stringify_answer(_answer_value(answer))
    if not text or text.strip() == "":
        return None
    vector = _as_vector(embedding_model.encode(text))
    if vector is None:
        return None
    if hasattr(answer, "vector"):
        answer.vector = json.dumps(vector.tolist())
    return vector


def _calibrate_text_cosine(cosine, config):
    return _clamp((float(cosine) + 1.0) / 2.0)


_EMBEDDING_MODEL = None


def _get_embedding_model():
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        force_cpu = os.getenv("MATCHING_V2_FORCE_CPU", "1") != "0"
        if force_cpu:
            import torch
            torch.cuda.is_available = lambda: False
        from text2vec import SentenceModel
        _EMBEDDING_MODEL = SentenceModel(device="cpu" if force_cpu else None)
    return _EMBEDDING_MODEL


def _question_similarity(answer_a, answer_b):
    values_a = _normalized_answer(answer_a)
    values_b = _normalized_answer(answer_b)
    if values_a is None or values_b is None:
        return None

    if isinstance(values_a, list) or isinstance(values_b, list):
        set_a = set(values_a if isinstance(values_a, list) else [values_a])
        set_b = set(values_b if isinstance(values_b, list) else [values_b])
        union = set_a | set_b
        if not union:
            return None
        return len(set_a & set_b) / len(union)

    try:
        value_a = float(values_a)
        value_b = float(values_b)
    except (TypeError, ValueError):
        return 1.0 if str(values_a) == str(values_b) else 0.0

    max_range = _max_range(answer_a, answer_b)
    return _clamp(1.0 - abs(value_a - value_b) / max_range)


def _normalized_answer(answer):
    raw = _answer_value(answer)
    if raw is None or str(raw).strip() == "":
        return None

    parsed = _parse_answer(raw)
    if isinstance(parsed, list):
        return [_option_value(answer, item) for item in parsed if str(item).strip()]
    return _option_value(answer, parsed)


def _option_value(answer, raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw

    text = str(raw).strip().strip('"').strip("'")
    try:
        return float(text)
    except ValueError:
        pass

    options = _item_options(answer)
    for index, option in enumerate(options, start=1):
        if text == option:
            return index
    return text


def _max_range(answer_a, answer_b):
    options_count = max(len(_item_options(answer_a)), len(_item_options(answer_b)))
    if options_count > 1:
        return float(options_count - 1)
    return 4.0


def _combined_weight(answer_a, answer_b):
    weight_a = _safe_weight(getattr(answer_a, "weight", None))
    weight_b = _safe_weight(getattr(answer_b, "weight", None))
    combined = (weight_a + weight_b) / 2.0
    return combined if combined > 0 else 1.0


def _safe_weight(value):
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 1.0
    return weight if weight > 0 else 1.0


def _is_numeric_match_field(answer):
    item = getattr(answer, "item", None)
    item_type = str(getattr(item, "type", "") or "").lower()
    data_type = str(getattr(item, "data_type", "") or "").lower()
    title = str(getattr(item, "title", "") or "").lower()

    if item_type in {"text", "textarea", "input"} and _field_key(title, TEXT_FIELD_KEYWORDS):
        return False
    if item_type in {"select", "radio", "checkbox", "number", "integer"}:
        return True
    if data_type in {"integer", "number", "float", "double"}:
        return True
    return _field_key(title, NUMERIC_FIELD_KEYWORDS) is not None


def _is_text_match_field(answer):
    item = getattr(answer, "item", None)
    item_type = str(getattr(item, "type", "") or "").lower()
    title = str(getattr(item, "title", "") or "").lower()
    if _field_key(title, TEXT_FIELD_KEYWORDS):
        return True
    return item_type in {"text", "textarea", "input"} and not _field_key(title, NUMERIC_FIELD_KEYWORDS)


def _field_label(answer):
    item = getattr(answer, "item", None)
    title = str(getattr(item, "title", "") or "")
    key = _field_key(title.lower(), {**NUMERIC_FIELD_KEYWORDS, **TEXT_FIELD_KEYWORDS})
    if key:
        return FIELD_LABELS.get(key, title)
    return title or str(getattr(answer, "item_id", "未知题目"))


def _field_key(title, mapping):
    for key, keywords in mapping.items():
        if any(keyword.lower() in title for keyword in keywords):
            return key
    return None


def _answers_by_item(user):
    answers = getattr(user, "questionnaire_answers", None) or []
    return {getattr(answer, "item_id", None): answer for answer in answers if getattr(answer, "item_id", None)}


def _text_answers_by_item(user):
    answers = _answers_by_item(user)
    return {
        item_id: answer
        for item_id, answer in answers.items()
        if _is_text_match_field(answer) and str(_answer_value(answer) or "").strip()
    }


def _answer_value(answer):
    return getattr(answer, "answer", None)


def _parse_answer(raw):
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw

    text = str(raw).strip()
    if not text:
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if isinstance(parsed, (list, tuple)):
                return list(parsed)
            if isinstance(parsed, dict):
                return list(parsed.values())
            return parsed
        except Exception:
            pass
    if "," in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


def _item_options(answer):
    item = getattr(answer, "item", None)
    params = getattr(item, "params", None)
    if params is None:
        return []
    parsed = params if isinstance(params, dict) else None
    if parsed is None:
        text = str(params).strip()
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
                break
            except Exception:
                pass
    if not isinstance(parsed, dict):
        return []
    options = parsed.get("options") or parsed.get("data") or []
    if not isinstance(options, list):
        return []
    return [str(option) for option in options]


def _stringify_answer(raw):
    parsed = _parse_answer(raw)
    if isinstance(parsed, list):
        return "、".join(str(item) for item in parsed)
    return str(parsed)


def _read_vector(answer):
    raw = getattr(answer, "vector", None)
    if not raw:
        return None
    try:
        return _as_vector(json.loads(raw))
    except Exception:
        return None


def _as_vector(value):
    try:
        arr = np.array(value, dtype=float)
    except Exception:
        return None
    if arr.ndim > 1:
        arr = arr.reshape(-1)
    if arr.size == 0:
        return None
    return arr


def _cosine_similarity(vector_a, vector_b):
    a = _as_vector(vector_a)
    b = _as_vector(vector_b)
    if a is None or b is None or a.shape != b.shape:
        return None
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0 or math.isnan(denom):
        return None
    return float(np.dot(a, b) / denom)


def _clamp(value, low=0.0, high=1.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NEUTRAL_SCORE
    if math.isnan(number):
        return NEUTRAL_SCORE
    return max(low, min(high, number))


def _read_float_env(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
