import json
import time
from datetime import datetime, timedelta
from hashlib import sha256

from flask import current_app
from sqlalchemy.orm import joinedload

from ai_client import chat_json
from database import db_session, engine
from matching_v2 import calculate_match_v2
from models import AICacheEntry, AIProfile, MatchingScore, QuestionnaireAnswer, Student


_EXPLANATION_CACHE = {}
EXPLANATION_PROMPT_VERSION = "summary_under_50_v5"
PROFILE_VERSION = "structured_profile_v1"
_AI_CACHE_TABLES_READY = False


def explain_match(current_student, target_student, search_query=None, search_highlights=None, match_config=None):
    search_highlights = _normalize_highlights(search_highlights or [])
    cache_key = _explanation_cache_key(current_student, target_student, search_query, search_highlights)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    match_result = calculate_match_v2(current_student, target_student, config=match_config, commit_vectors=db_session.commit)
    payload = {
        "mode": "search_context" if search_query else "general",
        "search_query": search_query or "",
        "search_highlights": search_highlights,
        "match_score": match_result.get("match_score"),
        "algorithm_features": match_result.get("explanation_features", {}),
        "me": _student_profile(current_student),
        "candidate": _student_profile(target_student),
    }
    raw_result = _call_explanation_ai(payload)
    if not _summary_length_ok(raw_result.get("summary", "")):
        raw_result = _call_explanation_ai(payload, previous_summary=raw_result.get("summary", ""))
    result = _normalize_explanation(raw_result, search_query)
    _cache_set(cache_key, result)
    return result


def explain_team_match(current_students, candidate_students, match_score, search_query=None, search_highlights=None):
    search_highlights = _normalize_highlights(search_highlights or [])
    cache_key = _team_explanation_cache_key(current_students, candidate_students, search_query, search_highlights)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    payload = {
        "mode": "search_context" if search_query else "general",
        "search_query": search_query or "",
        "search_highlights": search_highlights,
        "match_score": match_score,
        "me": _team_profile(current_students),
        "candidate": _team_profile(candidate_students),
    }
    raw_result = _call_team_explanation_ai(payload)
    if not _summary_length_ok(raw_result.get("summary", "")):
        raw_result = _call_team_explanation_ai(payload, previous_summary=raw_result.get("summary", ""))
    result = _normalize_explanation(raw_result, search_query)
    _cache_set(cache_key, result)
    return result


def search_roommates(current_student, query, candidate_ids=None, candidate_limit=None):
    limit = _candidate_limit(candidate_limit)
    candidates = _recommended_students(current_student)
    if candidate_ids is not None:
        allowed = {int(x) for x in candidate_ids[:limit]}
        candidates = [item for item in candidates if item["student"].id in allowed]
    candidates = candidates[:limit]
    if not candidates:
        return {"query": query, "results": []}

    temp_id_to_student_id = {}
    candidate_profiles = []
    for idx, item in enumerate(candidates, start=1):
        temp_id = f"c{idx}"
        student = item["student"]
        temp_id_to_student_id[temp_id] = student.id
        candidate_profiles.append({
            "candidate_id": temp_id,
            "match_score": item["score"],
            "profile": _student_profile(student),
        })

    payload = {
        "query": query,
        "me": _student_profile(current_student),
        "candidates": candidate_profiles,
    }
    ai_result = _call_search_ai(payload)
    results = []
    seen = set()
    for item in ai_result.get("results", []):
        temp_id = str(item.get("candidate_id", "")).strip()
        student_id = temp_id_to_student_id.get(temp_id)
        if student_id is None or student_id in seen:
            continue
        seen.add(student_id)
        results.append({
            "student_id": student_id,
            "ai_score": _bounded_int(item.get("ai_score"), default=60),
            "reason": str(item.get("reason", "")).strip()[:180],
            "highlights": _normalize_highlights(item.get("highlights", [])),
        })

    ranked_ids = {r["student_id"] for r in results}
    for item in candidates:
        student_id = item["student"].id
        if student_id not in ranked_ids:
            results.append({
                "student_id": student_id,
                "ai_score": 0,
                "reason": "AI 未给出明确排序理由，保留原匹配顺序。",
                "highlights": [],
            })
    return {"query": query, "results": results}


def search_dorm_teams(current_students, query, candidate_teams, candidate_keys=None, candidate_limit=None):
    limit = _candidate_limit(candidate_limit)
    candidates = list(candidate_teams or [])
    if candidate_keys is not None:
        allowed = {str(x) for x in candidate_keys[:limit]}
        candidates = [item for item in candidates if str(item.get("candidate_key")) in allowed]
    candidates = candidates[:limit]
    if not candidates:
        return {"query": query, "results": []}

    temp_id_to_key = {}
    candidate_profiles = []
    for idx, item in enumerate(candidates, start=1):
        temp_id = f"t{idx}"
        temp_id_to_key[temp_id] = item["candidate_key"]
        candidate_profiles.append({
            "candidate_id": temp_id,
            "match_score": item["match_score"],
            "profile": _team_profile(item["members"]),
        })

    payload = {
        "query": query,
        "me": _team_profile(current_students),
        "candidates": candidate_profiles,
    }
    ai_result = _call_team_search_ai(payload)
    results = []
    seen = set()
    for item in ai_result.get("results", []):
        temp_id = str(item.get("candidate_id", "")).strip()
        candidate_key = temp_id_to_key.get(temp_id)
        if candidate_key is None or candidate_key in seen:
            continue
        seen.add(candidate_key)
        results.append({
            "candidate_key": candidate_key,
            "ai_score": _bounded_int(item.get("ai_score"), default=60),
            "reason": str(item.get("reason", "")).strip()[:180],
            "highlights": _normalize_highlights(item.get("highlights", [])),
        })

    ranked_keys = {r["candidate_key"] for r in results}
    for item in candidates:
        candidate_key = item["candidate_key"]
        if candidate_key not in ranked_keys:
            results.append({
                "candidate_key": candidate_key,
                "ai_score": 0,
                "reason": "AI 未给出明确排序理由，保留原匹配顺序。",
                "highlights": [],
            })
    return {"query": query, "results": results}


def _candidate_limit(candidate_limit=None):
    default = int(current_app.config.get("AI_SEARCH_CANDIDATE_LIMIT", 30))
    try:
        value = int(candidate_limit) if candidate_limit is not None else default
    except (TypeError, ValueError):
        value = default
    return max(1, min(100, value))


def _call_team_explanation_ai(payload, previous_summary=None):
    system = (
        "你是大学新生宿舍匹配助手。你会把每一边都视为一个小队整体，"
        "只根据给定资料做温和、具体、克制的分析；既说明共同契合点，"
        "也指出需要提前沟通的不契合点。不要编造资料，不要输出隐私信息。必须返回 JSON。"
    )
    user = (
        "请为两个宿舍小队生成匹配解释。若 search_query 非空，请围绕该搜索意图解释，"
        "并优先把 search_highlights 或与搜索意图直接相关的关键词放进 highlight 字段。"
        "summary 不超过 50 个中文字符，必须同时概括最契合方面和最高冲突方面，不要展开细节。"
        "matched_traits 和 mismatched_traits 的 text 必须是完整短句，不能只写题目名或字段名。"
        "返回格式："
        '{"summary":"...",'
        '"matched_traits":[{"text":"...","highlight":"..."}],'
        '"mismatched_traits":[{"text":"...","highlight":"..."}],'
        '"suggested_questions":["..."]}'
        f"\n资料：{json.dumps(payload, ensure_ascii=False)}"
    )
    if previous_summary:
        user += f"\n上一次 summary 超过 50 字：{previous_summary}。请重写为不超过 50 个中文字符。"
    return chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.25, max_tokens=1500)


def _call_explanation_ai(payload, previous_summary=None):
    system = (
        "你是大学新生舍友匹配助手。只根据给定资料做温和、具体、克制的分析；"
        "既说明契合点，也指出需要提前沟通的不契合点。不要编造资料，不要输出隐私信息。"
        "必须返回 JSON。"
    )
    user = (
        "请为两位同学生成舍友匹配解释。若 search_query 非空，请围绕该搜索意图解释，"
        "并优先把 search_highlights 或与搜索意图直接相关的关键词放进 highlight 字段。"
        "summary 不超过 50 个中文字符，必须同时概括最契合方面和最高冲突方面，不要展开细节。"
        "matched_traits 和 mismatched_traits 的 text 必须是完整短句，不能只写题目名或字段名。"
        "返回格式："
        '{"summary":"...",'
        '"matched_traits":[{"text":"...","highlight":"..."}],'
        '"mismatched_traits":[{"text":"...","highlight":"..."}],'
        '"suggested_questions":["..."]}'
        f"\n资料：{json.dumps(payload, ensure_ascii=False)}"
    )
    if previous_summary:
        user += f"\n上一次 summary 超过 50 字：{previous_summary}。请重写为不超过 50 个中文字符。"
    return chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.25, max_tokens=1200)


def _call_team_search_ai(payload):
    system = (
        "你是大学新生宿舍自然语言搜索助手。你会在候选宿舍小队中根据用户 query 和资料相关性排序。"
        "候选队伍已经按基础匹配分筛选过，你只负责重排。不要编造资料，必须返回 JSON。"
    )
    user = (
        "请根据 query 对 candidates 从高到低排序。ai_score 使用 0-100 整数；"
        "highlights 放与 query 直接相关、可在解释中加粗的短关键词。"
        "返回格式："
        '{"results":[{"candidate_id":"t1","ai_score":90,"reason":"...","highlights":["早起"]}]}'
        f"\n资料：{json.dumps(payload, ensure_ascii=False)}"
    )
    return chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.15, max_tokens=2200)


def _call_search_ai(payload):
    system = (
        "你是大学新生舍友自然语言搜索助手。你会在候选人中根据用户 query 和资料相关性排序。"
        "候选人已经按基础匹配分筛选过，你只负责重排。不要编造资料，必须返回 JSON。"
    )
    user = (
        "请根据 query 对 candidates 从高到低排序。ai_score 使用 0-100 整数；"
        "highlights 放与 query 直接相关、可在解释中加粗的短关键词。"
        "返回格式："
        '{"results":[{"candidate_id":"c1","ai_score":90,"reason":"...","highlights":["早起"]}]}'
        f"\n资料：{json.dumps(payload, ensure_ascii=False)}"
    )
    return chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.15, max_tokens=1800)


def _student_profile(student):
    fingerprint = _student_profile_fingerprint(student)
    cached = _profile_cache_get("student", student.id, fingerprint)
    if cached is not None:
        return cached

    answers = []
    for answer in sorted(student.questionnaire_answers or [], key=lambda a: getattr(a.item, "index", 0) or 0):
        if not answer.item:
            continue
        value = _display_answer(answer.answer)
        if value == "":
            continue
        answers.append({
            "question": answer.item.title,
            "answer": value,
            "weight": _safe_float(answer.weight),
        })
    profile = {
        "profile_version": PROFILE_VERSION,
        "student_id": student.id,
        "name": student.name,
        "province": student.province or "",
        "mbti": student.mbti or "",
        "traits": student.contact or "",
        "questionnaire": answers,
    }
    _profile_cache_set("student", student.id, fingerprint, profile)
    return profile


def _team_profile(students):
    students = sorted(students or [], key=lambda student: student.id)
    team_id = _stable_team_id(students)
    fingerprint = _team_profile_fingerprint(students)
    if team_id is not None:
        cached = _profile_cache_get("team", team_id, fingerprint)
        if cached is not None:
            return cached

    members = [_student_profile(student) for student in students]
    profile = {
        "profile_version": PROFILE_VERSION,
        "team_id": team_id,
        "member_count": len(members),
        "members": members,
    }
    if team_id is not None:
        _profile_cache_set("team", team_id, fingerprint, profile)
    return profile


def _recommended_students(current_student):
    score_rows = db_session.query(MatchingScore) \
        .where((MatchingScore.to_student_id == current_student.id) | (MatchingScore.from_student_id == current_student.id)) \
        .all()
    incoming = {}
    outgoing = {}
    for row in score_rows:
        if row.to_student_id == current_student.id and row.from_student_id != current_student.id:
            incoming[row.from_student_id] = row.score
        elif row.from_student_id == current_student.id and row.to_student_id != current_student.id:
            outgoing[row.to_student_id] = row.score

    students = db_session.query(Student) \
        .where(Student.gender == current_student.gender) \
        .where(Student.id != current_student.id) \
        .options(joinedload(Student.questionnaire_answers).joinedload(QuestionnaireAnswer.item)) \
        .all()

    out = []
    for student in students:
        score = incoming.get(student.id)
        if score is None:
            score = outgoing.get(student.id)
        if score is None:
            continue
        out.append({"student": student, "score": _safe_float(score)})
    out.sort(key=lambda item: item["score"], reverse=True)
    return out


def _display_answer(raw):
    if raw is None:
        return ""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        value = raw
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_explanation(data, search_query=None):
    return {
        "mode": "search_context" if search_query else "general",
        "summary": _fit_summary(data),
        "matched_traits": _normalize_trait_items(data.get("matched_traits", [])),
        "mismatched_traits": _normalize_trait_items(data.get("mismatched_traits", [])),
        "suggested_questions": [str(x).strip()[:120] for x in data.get("suggested_questions", []) if str(x).strip()][:3],
    }


def _summary_length_ok(summary):
    length = len(str(summary or "").strip())
    return 0 < length <= 50


def _fit_summary(data):
    summary = str(data.get("summary", "")).strip()
    if len(summary) <= 50:
        return summary
    return summary[:50]


def _first_trait_label(items):
    if not isinstance(items, list):
        return ""
    for item in items:
        if isinstance(item, dict):
            highlight = str(item.get("highlight", "")).strip()
            if highlight:
                return highlight[:18]
            text = str(item.get("text", "")).strip()
            if text:
                return text[:18]
        else:
            text = str(item).strip()
            if text:
                return text[:18]
    return ""


def _normalize_trait_items(items):
    out = []
    for item in items[:4] if isinstance(items, list) else []:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            highlight = str(item.get("highlight", "")).strip()
        else:
            text = str(item).strip()
            highlight = ""
        if text:
            out.append({"text": text[:180], "highlight": highlight[:24]})
    return out


def _normalize_highlights(items):
    if not isinstance(items, list):
        return []
    return [str(x).strip()[:24] for x in items if str(x).strip()][:5]


def _bounded_int(value, default=0):
    try:
        num = int(float(value))
    except Exception:
        return default
    return max(0, min(100, num))


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _explanation_cache_key(current_student, target_student, search_query, search_highlights=None):
    parts = [
        "personal_explanation",
        str(current_student.id),
        str(target_student.id),
        search_query or "",
        ",".join(search_highlights or []),
        EXPLANATION_PROMPT_VERSION,
        _student_profile_fingerprint(current_student),
        _student_profile_fingerprint(target_student),
        str(current_app.config.get("AI_MODEL", "")),
    ]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


def _team_explanation_cache_key(current_students, candidate_students, search_query, search_highlights=None):
    parts = [
        "team",
        ",".join(str(s.id) for s in sorted(current_students, key=lambda item: item.id)),
        ",".join(str(s.id) for s in sorted(candidate_students, key=lambda item: item.id)),
        search_query or "",
        ",".join(search_highlights or []),
        EXPLANATION_PROMPT_VERSION,
        _team_profile_fingerprint(current_students),
        _team_profile_fingerprint(candidate_students),
        str(current_app.config.get("AI_MODEL", "")),
    ]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


def _student_profile_fingerprint(student):
    rows = [
        PROFILE_VERSION,
        str(student.id),
        str(student.name or ""),
        str(student.province or ""),
        str(student.mbti or ""),
        str(student.contact or ""),
        _answers_fingerprint(student),
    ]
    return sha256("|".join(rows).encode("utf-8")).hexdigest()


def _team_profile_fingerprint(students):
    rows = [PROFILE_VERSION]
    for student in sorted(students or [], key=lambda item: item.id):
        rows.append(f"{student.id}:{_student_profile_fingerprint(student)}")
    return sha256("|".join(rows).encode("utf-8")).hexdigest()


def _stable_team_id(students):
    team_ids = {student.team_id for student in students or [] if student.team_id is not None}
    if len(team_ids) != 1:
        return None
    return next(iter(team_ids))


def _answers_fingerprint(student):
    rows = []
    for answer in student.questionnaire_answers or []:
        rows.append(f"{answer.item_id}:{answer.answer}:{answer.weight}:{answer.updated_at}")
    return sha256("|".join(sorted(rows)).encode("utf-8")).hexdigest()


def _ensure_ai_cache_tables():
    global _AI_CACHE_TABLES_READY
    if _AI_CACHE_TABLES_READY:
        return
    AIProfile.__table__.create(bind=engine, checkfirst=True)
    AICacheEntry.__table__.create(bind=engine, checkfirst=True)
    _AI_CACHE_TABLES_READY = True


def _profile_cache_get(subject_type, subject_id, fingerprint):
    try:
        _ensure_ai_cache_tables()
        row = db_session.query(AIProfile) \
            .filter(AIProfile.subject_type == subject_type) \
            .filter(AIProfile.subject_id == subject_id) \
            .first()
        if row is None or row.fingerprint != fingerprint:
            return None
        return json.loads(row.profile_json)
    except Exception:
        db_session.rollback()
        return None


def _profile_cache_set(subject_type, subject_id, fingerprint, profile):
    try:
        _ensure_ai_cache_tables()
        now = datetime.utcnow()
        row = db_session.query(AIProfile) \
            .filter(AIProfile.subject_type == subject_type) \
            .filter(AIProfile.subject_id == subject_id) \
            .first()
        if row is None:
            row = AIProfile(
                subject_type=subject_type,
                subject_id=subject_id,
                fingerprint=fingerprint,
                profile_json=json.dumps(profile, ensure_ascii=False),
            )
            db_session.add(row)
        else:
            row.fingerprint = fingerprint
            row.profile_json = json.dumps(profile, ensure_ascii=False)
            row.updated_at = now
        db_session.commit()
    except Exception:
        db_session.rollback()


def _cache_get(key):
    item = _EXPLANATION_CACHE.get(key)
    if item:
        expires_at, value = item
        if expires_at >= time.time():
            return value
        _EXPLANATION_CACHE.pop(key, None)

    try:
        _ensure_ai_cache_tables()
        now = datetime.utcnow()
        row = db_session.query(AICacheEntry) \
            .filter(AICacheEntry.cache_key == key) \
            .first()
        if row is None:
            return None
        if row.expires_at < now:
            db_session.delete(row)
            db_session.commit()
            return None
        value = json.loads(row.value_json)
        ttl_left = max(1.0, (row.expires_at - now).total_seconds())
        _EXPLANATION_CACHE[key] = (time.time() + ttl_left, value)
        return value
    except Exception:
        db_session.rollback()
        return None


def _cache_set(key, value):
    ttl = int(current_app.config.get("AI_EXPLANATION_CACHE_TTL", 86400))
    expires_at = datetime.utcnow() + timedelta(seconds=ttl)
    _EXPLANATION_CACHE[key] = (time.time() + ttl, value)
    try:
        _ensure_ai_cache_tables()
        now = datetime.utcnow()
        row = db_session.query(AICacheEntry) \
            .filter(AICacheEntry.cache_key == key) \
            .first()
        if row is None:
            row = AICacheEntry(
                cache_type="explanation",
                cache_key=key,
                value_json=json.dumps(value, ensure_ascii=False),
                expires_at=expires_at,
            )
            db_session.add(row)
        else:
            row.cache_type = "explanation"
            row.value_json = json.dumps(value, ensure_ascii=False)
            row.expires_at = expires_at
            row.updated_at = now
        db_session.commit()
    except Exception:
        db_session.rollback()
