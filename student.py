import datetime
import os
import time
from functools import wraps

import bcrypt
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import create_access_token, verify_jwt_in_request, get_jwt, current_user
from sqlalchemy.orm import joinedload
from werkzeug.utils import secure_filename

from database import db_session
import json

from ai_client import AIClientError
from ai_roommate import explain_match, explain_team_match, search_roommates, search_dorm_teams
from matching_v2 import MatchV2Config, calculate_match_v2
from models import Student, QuestionnaireItem, QuestionnaireAnswer, MatchingScore, Team, TeamInvitation, \
    TeamRequest, get_system_setting, Announcement, QuestionnairePage, QuestionnairePageAnswer
    
student_pages = Blueprint('student_pages', __name__, template_folder="templates/student")

ALLOWED_AVATAR_EXTS = (".png", ".jpg", ".jpeg")


def _avatar_dir():
    return os.path.join(current_app.root_path, "static", "uploads", "student_avatar")


def get_student_avatar_url(student_id):
    base = _avatar_dir()
    for ext in ALLOWED_AVATAR_EXTS:
        fn = f"{student_id}{ext}"
        p = os.path.join(base, fn)
        if os.path.exists(p):
            ts = int(os.path.getmtime(p))
            return f"/static/uploads/student_avatar/{fn}?t={ts}"
    return ""

def student_required():
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            if claims["role"] == "student":
                return fn(*args, **kwargs)
            else:
                return jsonify({
                    "code": 403,
                    "msg": "无权限！"
                }), 403

        return decorator

    return wrapper


@student_pages.route('/login', methods=['POST'])
def login():
    if request.json is not None:
        id = request.json.get('id', None)
        password = request.json.get('password', None)
        student = db_session.query(Student).where(Student.id == id).first()
        if student is not None and student.check_password(password):
            student.last_logged_at = datetime.datetime.now()
            db_session.commit()
            token = create_access_token(identity=student.id, additional_headers={
                "role": "student"
            }, additional_claims={
                "role": "student"
            })

            return jsonify({
                "code": 200,
                "msg": "登录成功！",
                "data": {
                    "access_token": token
                }
            })

    return jsonify({
        "code": 401,
        "msg": "ID 或密码错误！"
    })


@student_pages.get("/userinfo")
@student_required()
def userinfo():
    user_data = current_user.to_dict(
        only=['id', 'name', 'gender', 'contact', 'qq', 'wechat', 'province', 'mbti', 'team_id', 'team.id',
              'team.students.id', 'team.students.name',
              'has_answered_questionnaire'])
    user_data['avatar_url'] = get_student_avatar_url(current_user.id)
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "user": user_data
        }
    })


@student_pages.post('/logout')
@student_required()
def logout():
    return jsonify({
        "code": 200,
        "msg": "success"
    })


@student_pages.get('/questionnaire/list')
@student_required()
def questionnaire_list():
    # legacy flat list (kept for compatibility)
    questionnaire_items = db_session.query(QuestionnaireItem).order_by(QuestionnaireItem.index.asc()).all()
    questionnaire_items = [questionnaire_item.to_dict() for questionnaire_item in questionnaire_items]
    return jsonify({"code": 200, "msg": "success", "data": questionnaire_items})


@student_pages.get('/questionnaire/structure')
@student_required()
def questionnaire_structure():
    pages = db_session.query(QuestionnairePage).order_by(QuestionnairePage.index.asc()).all()
    out_pages = []
    for p in pages:
        items = db_session.query(QuestionnaireItem) \
            .filter(QuestionnaireItem.page_id == p.id) \
            .order_by(QuestionnaireItem.index_in_page.asc()) \
            .all()
        out_pages.append({
            **p.to_dict(only=['id', 'title', 'remark', 'index', 'created_at', 'updated_at']),
            "items": [i.to_dict(only=[
                'id', 'title', 'weight', 'data_type', 'params', 'type',
                'index', 'page_id', 'index_in_page', 'created_at', 'updated_at'
            ]) for i in items]
        })
    return jsonify({"code": 200, "msg": "success", "data": {"pages": out_pages}})


@student_pages.get('/questionnaire/page_answer')
@student_required()
def questionnaire_get_page_answer():
    page_id = request.args.get("page_id", None)
    if page_id is None:
        return jsonify({"code": 400, "msg": "缺少 page_id"}), 400
    row = db_session.query(QuestionnairePageAnswer) \
        .filter(QuestionnairePageAnswer.student_id == current_user.id) \
        .filter(QuestionnairePageAnswer.page_id == int(page_id)) \
        .first()
    if row is None or not row.answers_json:
        return jsonify({"code": 200, "msg": "success", "data": {}})
    try:
        return jsonify({"code": 200, "msg": "success", "data": json.loads(row.answers_json)})
    except Exception:
        return jsonify({"code": 200, "msg": "success", "data": {}})


@student_pages.post('/questionnaire/page_answer')
@student_required()
def questionnaire_save_page_answer():
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    page_id = request.json.get("page_id", None)
    answers = request.json.get("answers", None)
    if page_id is None or not isinstance(answers, dict):
        return jsonify({"code": 400, "msg": "参数错误"}), 400
    page_id = int(page_id)

    # upsert page draft
    row = db_session.query(QuestionnairePageAnswer) \
        .filter(QuestionnairePageAnswer.student_id == current_user.id) \
        .filter(QuestionnairePageAnswer.page_id == page_id) \
        .first()
    answers_json = json.dumps(answers, ensure_ascii=False)
    if row is None:
        row = QuestionnairePageAnswer(student_id=current_user.id, page_id=page_id, answers_json=answers_json, status=0)
        db_session.add(row)
    else:
        row.answers_json = answers_json
        row.updated_at = datetime.datetime.now()
    db_session.commit()

    # compatibility: upsert per-item answers into questionnaire_answers for matching logic
    exist_answers = db_session.query(QuestionnaireAnswer).filter(
        QuestionnaireAnswer.student_id == current_user.id).all()
    exist_by_item = {a.item_id: a for a in exist_answers}

    page_items = db_session.query(QuestionnaireItem).filter(QuestionnaireItem.page_id == page_id).all()
    valid_item_ids = {i.id for i in page_items}
    default_weight = {i.id: i.weight for i in page_items}

    bulk_save_models = []
    data_changed = False
    for item_id, value in answers.items():
        if item_id not in valid_item_ids:
            continue
        if not isinstance(value, dict) or "answer" not in value:
            continue
        w = value.get("weight", 1)
        if default_weight.get(item_id, 1) < 0:
            w = default_weight[item_id]
        ans_str = str(value.get("answer"))
        if item_id in exist_by_item:
            ex = exist_by_item[item_id]
            if ex.answer != ans_str or ex.weight != w:
                ex.answer = ans_str
                ex.weight = w
                ex.updated_at = datetime.datetime.now()
                ex.vector = None
                db_session.commit()
                data_changed = True
        else:
            bulk_save_models.append(
                QuestionnaireAnswer(item_id=item_id, answer=ans_str, student_id=current_user.id, weight=w, vector=None)
            )
            data_changed = True

    if data_changed and bulk_save_models:
        db_session.bulk_save_objects(bulk_save_models)
        db_session.commit()

        # 删除匹配得分（该页变动也会影响匹配）
        db_session.query(MatchingScore) \
            .filter((MatchingScore.to_student_id == current_user.id) | (
                MatchingScore.from_student_id == current_user.id)) \
            .delete(synchronize_session=False)
        db_session.commit()

    return jsonify({"code": 200, "msg": "success"})


@student_pages.get('/questionnaire/answer')
@student_required()
def questionnaire_get_answers():
    questionnaire_answers = db_session.query(QuestionnaireAnswer).filter(
        QuestionnaireAnswer.student_id == current_user.id).options(joinedload(QuestionnaireAnswer.item)).all()

    questionnaire_answers = [questionnaire_answer.to_dict(only=['item_id', 'answer', 'weight', 'item']) for
                             questionnaire_answer in questionnaire_answers]

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": questionnaire_answers
    })


@student_pages.post('/questionnaire/answer')
@student_required()
def questionnaire_set_answers():
    if request.json is not None:
        if type(request.json) is not dict:
            return jsonify({
                "code": 400,
                "msg": "问卷答案数据错误"
            })

        # TODO:: 整合到model里进行
        questionnaire_answers = request.json

        exist_answers = db_session.query(QuestionnaireAnswer).filter(
            QuestionnaireAnswer.student_id == current_user.id).all()
        questionnaire_items = db_session.query(QuestionnaireItem).all()
        default_weight = {}
        missed_items = []
        for questionnaire_item in questionnaire_items:
            default_weight[questionnaire_item.id] = questionnaire_item.weight

        bulk_save_models = []
        data_changed = False
        for key in questionnaire_answers.keys():
            value = questionnaire_answers[key]
            if key not in default_weight.keys():
                # 找不对对应item 存个屁
                missed_items.append((key, value))
                continue
            elif default_weight[key] < 0:
                value['weight'] = default_weight[key]

            need_to_create = True

            # 对已存在的答案进行修改 一般只会用到这一个

            # TODO: 权重范围限制
            # TODO: SQL性能调优
            for exist_answer in exist_answers:
                if exist_answer.item_id == key:
                    need_to_create = False
                    if exist_answer.answer != str(value['answer']) or exist_answer.weight != value['weight']:
                        exist_answer.answer = str(value['answer'])
                        exist_answer.weight = value['weight']
                        exist_answer.updated_at = datetime.datetime.now()
                        # exist_answer.vector = json.dumps(model.encode(exist_answer.answer).tolist())
                        exist_answer.vector = None
                        db_session.commit()
                        data_changed = True

            if need_to_create:
                new_answer = QuestionnaireAnswer(item_id=key, answer=str(value['answer']), student_id=current_user.id,
                                                 weight=value['weight'], vector=None)
                bulk_save_models.append(new_answer)
                data_changed = True

        # 重头戏 存数据

        if data_changed:
            db_session.bulk_save_objects(bulk_save_models)
            db_session.commit()

            # 删除匹配得分
            db_session.query(MatchingScore) \
                .filter((MatchingScore.to_student_id == current_user.id) | (
                    MatchingScore.from_student_id == current_user.id)) \
                .delete(synchronize_session=False)

            db_session.commit()

    return jsonify({
        "code": 200,
        "msg": "success"
    })


@student_pages.get('/team/recommend_teammates')
@student_required()
def team_recommend_teammates():
    algorithm = request.args.get("algorithm", "").lower()
    if algorithm == "v2":
        return team_recommend_teammates_v2()

    # 兼容两种分数方向：
    # 1) from=对方, to=我（原始推荐方向）
    # 2) from=我, to=对方（回退方向，避免因任务中断导致大厅全是无分）
    related_scores = db_session.query(MatchingScore) \
        .where((MatchingScore.to_student_id == current_user.id) | (MatchingScore.from_student_id == current_user.id)) \
        .all()

    incoming_score_map = {}
    outgoing_score_map = {}
    for piece in related_scores:
        if piece.to_student_id == current_user.id and piece.from_student_id != current_user.id:
            incoming_score_map[piece.from_student_id] = piece.score
        elif piece.from_student_id == current_user.id and piece.to_student_id != current_user.id:
            outgoing_score_map[piece.to_student_id] = piece.score

    same_gender_students = db_session.query(Student) \
        .where(Student.gender == current_user.gender) \
        .where(Student.id != current_user.id) \
        .all()

    students_with_score = []
    students_with_no_score_data = []

    for piece in same_gender_students:
        team_students_num = 0
        if piece.team_id is not None:
            team_students_num = db_session.query(Student) \
                .where(Student.team_id == piece.team_id) \
                .count()

        item = piece.to_dict(only=['id', 'name', 'contact', 'qq', 'wechat', 'province', 'mbti'])
        item['avatar_url'] = get_student_avatar_url(piece.id)
        item['team_students_num'] = team_students_num

        score = incoming_score_map.get(piece.id, None)
        if score is None:
            score = outgoing_score_map.get(piece.id, None)

        if score is None:
            students_with_no_score_data.append(item)
        else:
            item['score'] = score
            students_with_score.append(item)

    construct_data = sorted(students_with_score, key=lambda x: x['score'], reverse=True)

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "students_with_score": construct_data,
            "students_with_no_score": students_with_no_score_data
        }
    })


def _matching_v2_config_from_request():
    numeric_weight = request.args.get("numeric_weight", None)
    text_weight = request.args.get("text_weight", None)
    if numeric_weight is None and text_weight is None and request.is_json and request.json is not None:
        numeric_weight = request.json.get("numeric_weight", None)
        text_weight = request.json.get("text_weight", None)
    if numeric_weight is None and text_weight is None:
        return None, None
    try:
        numeric_weight = float(numeric_weight)
        text_weight = float(text_weight)
    except (TypeError, ValueError):
        return None, (jsonify({"code": 400, "msg": "匹配权重格式错误"}), 400)
    if numeric_weight < 0 or text_weight < 0 or abs((numeric_weight + text_weight) - 1.0) > 0.001:
        return None, (jsonify({"code": 400, "msg": "选择题权重与文本题权重之和必须等于 1"}), 400)
    return MatchV2Config(alpha=numeric_weight, beta=text_weight), None


def team_recommend_teammates_v2():
    match_config, error_response = _matching_v2_config_from_request()
    if error_response is not None:
        return error_response

    same_gender_students = db_session.query(Student) \
        .where(Student.gender == current_user.gender) \
        .where(Student.id != current_user.id) \
        .options(joinedload(Student.questionnaire_answers)) \
        .all()

    students_with_score = []

    for piece in same_gender_students:
        team_students_num = 0
        if piece.team_id is not None:
            team_students_num = db_session.query(Student) \
                .where(Student.team_id == piece.team_id) \
                .count()

        item = piece.to_dict(only=['id', 'name', 'contact', 'qq', 'wechat', 'province', 'mbti'])
        item['avatar_url'] = get_student_avatar_url(piece.id)
        item['team_students_num'] = team_students_num

        match_result = calculate_match_v2(current_user, piece, config=match_config, commit_vectors=db_session.commit)
        item.update(match_result)
        students_with_score.append(item)

    construct_data = sorted(students_with_score, key=lambda x: x['match_score'], reverse=True)

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "algorithm": "matching_v2",
            "students_with_score": construct_data,
            "students_with_no_score": []
        }
    })


def _team_max_student_count():
    try:
        return int(get_system_setting("team_max_student_count", 4))
    except (TypeError, ValueError):
        return 4


def _student_card_data(student):
    item = student.to_dict(only=['id', 'name', 'contact', 'qq', 'wechat', 'province', 'mbti'])
    item['avatar_url'] = get_student_avatar_url(student.id)
    item['team_students_num'] = 0
    if student.team_id is not None and student.team is not None:
        item['team_students_num'] = len(student.team.students or [])
    return item


def _load_current_team_students():
    if current_user.team_id is None:
        student = db_session.query(Student) \
            .where(Student.id == current_user.id) \
            .options(joinedload(Student.team).joinedload(Team.students),
                     joinedload(Student.questionnaire_answers).joinedload(QuestionnaireAnswer.item)) \
            .first()
        return [student]

    return db_session.query(Student) \
        .where(Student.team_id == current_user.team_id) \
        .options(joinedload(Student.team).joinedload(Team.students),
                 joinedload(Student.questionnaire_answers).joinedload(QuestionnaireAnswer.item)) \
        .all()


def _candidate_team_sizes(current_team_size, team_max):
    if current_team_size <= 0 or current_team_size >= team_max:
        return []
    if current_team_size == 1:
        sizes = [2, team_max - 1]
    else:
        sizes = [team_max - current_team_size]
    return sorted({size for size in sizes if 1 <= size < team_max})


def _candidate_key(team_id, members):
    if team_id is not None:
        return f"team:{team_id}"
    if len(members) == 1:
        return f"solo:{members[0].id}"
    return "virtual:" + ",".join(str(s.id) for s in members)


def _team_match_score(current_students, candidate_students, match_config=None):
    scores = []
    for source in current_students:
        for target in candidate_students:
            result = calculate_match_v2(source, target, config=match_config, commit_vectors=db_session.commit)
            scores.append(float(result.get("match_score", 0)))
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def _team_pairwise_scores(current_students, candidate_students, match_config=None):
    pairs_by_candidate = {student.id: [] for student in candidate_students}
    all_scores = []
    for source in current_students:
        for target in candidate_students:
            result = calculate_match_v2(source, target, config=match_config, commit_vectors=db_session.commit)
            score = round(float(result.get("match_score", 0)), 1)
            all_scores.append(score)
            pairs_by_candidate[target.id].append({
                "current_student_id": source.id,
                "current_student_name": source.name,
                "candidate_student_id": target.id,
                "score": score,
            })
    average = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0
    return average, pairs_by_candidate


def _recommend_dorm_team_rows(match_config=None):
    team_max = _team_max_student_count()
    current_students = [s for s in _load_current_team_students() if s is not None]
    current_ids = {s.id for s in current_students}
    current_team_size = len(current_students)
    candidate_sizes = _candidate_team_sizes(current_team_size, team_max)
    if not current_students or not candidate_sizes:
        return current_students, []

    students = db_session.query(Student) \
        .where(Student.gender == current_user.gender) \
        .where(Student.id.notin_(current_ids)) \
        .options(joinedload(Student.team).joinedload(Team.students),
                 joinedload(Student.questionnaire_answers).joinedload(QuestionnaireAnswer.item)) \
        .all()

    teams = {}
    solos = []
    for student in students:
        if student.team_id is None:
            solos.append(student)
            continue
        teams.setdefault(student.team_id, []).append(student)

    candidates = []
    for team_id, members in teams.items():
        if len(members) not in candidate_sizes:
            continue
        candidates.append({
            "team_id": team_id,
            "virtual": False,
            "members": sorted(members, key=lambda item: item.id),
        })

    if 1 in candidate_sizes:
        for student in solos:
            candidates.append({
                "team_id": None,
                "virtual": True,
                "members": [student],
            })

    rows = []
    for candidate in candidates:
        members = candidate["members"]
        score, pairwise_scores = _team_pairwise_scores(current_students, members, match_config=match_config)
        rows.append({
            "candidate_key": _candidate_key(candidate["team_id"], members),
            "team_id": candidate["team_id"],
            "virtual": candidate["virtual"],
            "member_count": len(members),
            "match_score": score,
            "score": score,
            "members": members,
            "pairwise_scores": pairwise_scores,
        })

    rows.sort(key=lambda item: item["match_score"], reverse=True)
    return current_students, rows


def _serialize_dorm_team_row(row):
    members = []
    for student in row["members"]:
        item = _student_card_data(student)
        pairwise_scores = row.get("pairwise_scores", {}).get(student.id, [])
        if pairwise_scores:
            item["score"] = round(sum(float(piece.get("score", 0)) for piece in pairwise_scores) / len(pairwise_scores), 1)
        else:
            item["score"] = row["match_score"]
        members.append(item)
    return {
        "candidate_key": row["candidate_key"],
        "team_id": row["team_id"],
        "virtual": row["virtual"],
        "member_count": row["member_count"],
        "match_score": row["match_score"],
        "score": row["score"],
        "members": members,
    }


@student_pages.get('/team/recommend_dorm_teams')
@student_required()
def team_recommend_dorm_teams():
    match_config, error_response = _matching_v2_config_from_request()
    if error_response is not None:
        return error_response

    current_students, rows = _recommend_dorm_team_rows(match_config=match_config)
    team_max = _team_max_student_count()
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "algorithm": "matching_v2",
            "team_max_student_count": team_max,
            "current_team_size": len(current_students),
            "candidate_sizes": _candidate_team_sizes(len(current_students), team_max),
            "teams_with_score": [_serialize_dorm_team_row(row) for row in rows],
        }
    })


@student_pages.post('/ai/match_explanation')
@student_required()
def ai_match_explanation():
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    target_student_id = request.json.get("target_student_id")
    search_query = str(request.json.get("search_query") or "").strip()
    search_highlights = request.json.get("search_highlights") or []
    if target_student_id is None:
        return jsonify({"code": 400, "msg": "缺少 target_student_id"}), 400
    try:
        target_student_id = int(target_student_id)
    except (TypeError, ValueError):
        return jsonify({"code": 400, "msg": "target_student_id 格式错误"}), 400

    target_student = db_session.query(Student) \
        .where(Student.id == target_student_id) \
        .options(joinedload(Student.questionnaire_answers).joinedload(QuestionnaireAnswer.item)) \
        .first()
    if target_student is None:
        return jsonify({"code": 404, "msg": "学生不存在"}), 404
    if target_student.id == current_user.id:
        return jsonify({"code": 400, "msg": "不能解释自己和自己的匹配"}), 400
    if target_student.gender != current_user.gender:
        return jsonify({"code": 400, "msg": "不支持男女混寝匹配解释"}), 400

    try:
        match_config, error_response = _matching_v2_config_from_request()
        if error_response is not None:
            return error_response
        data = explain_match(
            current_user,
            target_student,
            search_query=search_query or None,
            search_highlights=search_highlights if isinstance(search_highlights, list) else [],
            match_config=match_config,
        )
    except AIClientError as exc:
        return jsonify({"code": 503, "msg": str(exc)}), 503
    except Exception as exc:
        current_app.logger.exception("AI match explanation failed")
        return jsonify({"code": 500, "msg": f"AI解释生成失败：{exc}"}), 500

    return jsonify({"code": 200, "msg": "success", "data": data})


@student_pages.post('/ai/team_match_explanation')
@student_required()
def ai_team_match_explanation():
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    candidate_key = str(request.json.get("candidate_key") or "").strip()
    search_query = str(request.json.get("search_query") or "").strip()
    search_highlights = request.json.get("search_highlights") or []
    if not candidate_key:
        return jsonify({"code": 400, "msg": "缺少 candidate_key"}), 400

    try:
        match_config, error_response = _matching_v2_config_from_request()
        if error_response is not None:
            return error_response
        current_students, rows = _recommend_dorm_team_rows(match_config=match_config)
        target = next((row for row in rows if row["candidate_key"] == candidate_key), None)
        if target is None:
            return jsonify({"code": 404, "msg": "候选宿舍不存在或不再可匹配"}), 404
        data = explain_team_match(
            current_students,
            target["members"],
            target["match_score"],
            search_query=search_query or None,
            search_highlights=search_highlights if isinstance(search_highlights, list) else [],
        )
    except AIClientError as exc:
        return jsonify({"code": 503, "msg": str(exc)}), 503
    except Exception as exc:
        current_app.logger.exception("AI team match explanation failed")
        return jsonify({"code": 500, "msg": f"AI解释生成失败：{exc}"}), 500

    return jsonify({"code": 200, "msg": "success", "data": data})


@student_pages.post('/ai/search_roommates')
@student_required()
def ai_search_roommates():
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    query = str(request.json.get("query") or "").strip()
    if not query:
        return jsonify({"code": 400, "msg": "搜索内容不能为空"}), 400
    candidate_ids = request.json.get("candidate_ids")
    candidate_limit = request.json.get("candidate_limit", None)
    if candidate_ids is not None and not isinstance(candidate_ids, list):
        return jsonify({"code": 400, "msg": "candidate_ids 必须是数组"}), 400
    if candidate_ids is not None:
        try:
            candidate_ids = [int(x) for x in candidate_ids]
        except (TypeError, ValueError):
            return jsonify({"code": 400, "msg": "candidate_ids 包含非法 id"}), 400

    try:
        data = search_roommates(current_user, query, candidate_ids=candidate_ids, candidate_limit=candidate_limit)
    except AIClientError as exc:
        return jsonify({"code": 503, "msg": str(exc)}), 503
    except Exception as exc:
        current_app.logger.exception("AI roommate search failed")
        return jsonify({"code": 500, "msg": f"AI搜索失败：{exc}"}), 500

    return jsonify({"code": 200, "msg": "success", "data": data})


@student_pages.post('/ai/search_dorm_teams')
@student_required()
def ai_search_dorm_teams():
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    query = str(request.json.get("query") or "").strip()
    if not query:
        return jsonify({"code": 400, "msg": "搜索内容不能为空"}), 400
    candidate_keys = request.json.get("candidate_keys")
    candidate_limit = request.json.get("candidate_limit", None)
    if candidate_keys is not None and not isinstance(candidate_keys, list):
        return jsonify({"code": 400, "msg": "candidate_keys 必须是数组"}), 400

    try:
        match_config, error_response = _matching_v2_config_from_request()
        if error_response is not None:
            return error_response
        current_students, rows = _recommend_dorm_team_rows(match_config=match_config)
        data = search_dorm_teams(
            current_students,
            query,
            rows,
            candidate_keys=candidate_keys,
            candidate_limit=candidate_limit,
        )
    except AIClientError as exc:
        return jsonify({"code": 503, "msg": str(exc)}), 503
    except Exception as exc:
        current_app.logger.exception("AI dorm team search failed")
        return jsonify({"code": 500, "msg": f"AI搜索失败：{exc}"}), 500

    return jsonify({"code": 200, "msg": "success", "data": data})


# !!important!! 不推荐邀请同学直接进入队伍！！ 这样很可能会忽视队伍里的其他同学 一定要确定每个人的生活习惯都和自己的没有冲突
# !!important!! 这个API主要用于两个都没有入队伍的同学组成新的队伍
@student_pages.post('/team/invite')
@student_required()
def team_invite():
    if request.json is not None:
        target_student_id = request.json.get('target_student_id')
        team_id = current_user.team_id
        target_student = db_session.query(Student).get(target_student_id)

        if target_student is None:
            return jsonify({
                "code": 404,
                "msg": "学生不存在"
            })

        # 性别校验
        if target_student.gender != current_user.gender:
            return jsonify({
                "code": 400,
                "msg": "不支持男女混寝"
            })

        if target_student.team_id is not None:
            return jsonify({
                "code": 400,
                "msg": "对方已经在其他队伍中了，不能撬墙角"
            })

        if target_student_id == current_user.id:
            return jsonify({
                "code": 400,
                "msg": "不能自己和自己组队"
            })

        if team_id is not None:
            team = db_session.query(Team).get(team_id)
            if team is None:
                return jsonify({
                    "code": 404,
                    "msg": "队伍不存在"
                })
            # 校验性别
            if team.gender != target_student.gender:
                return jsonify({
                    "code": 400,
                    "msg": "不支持男女混寝"
                })

            # 看看满人了没有
            if len(team.students) >= int(get_system_setting('team_max_student_count')):
                return jsonify({
                    "code": 400,
                    "msg": "队伍满人了"
                })

            # 看看有没有重复的邀请函
            similar_invitation_count = db_session.query(TeamInvitation) \
                .filter(TeamInvitation.to_student_id == target_student_id) \
                .filter(TeamInvitation.team_id == team_id) \
                .filter(TeamInvitation.status == 0) \
                .count()

            if similar_invitation_count > 0:
                return jsonify({
                    "code": 400,
                    "msg": "该同学已经收到过加入本队伍的邀请函"
                })

            similar_request_count = db_session.query(TeamRequest) \
                .filter(TeamRequest.team_id == team_id) \
                .filter(TeamRequest.student_id == target_student_id) \
                .filter(TeamRequest.status == 0) \
                .count()

            if similar_request_count > 0:
                return jsonify({
                    "code": 400,
                    "msg": "该同学已经向本队发出过入队申请，请先处理他的申请"
                })
        else:
            similar_invitation_count = db_session.query(TeamInvitation) \
                .filter(TeamInvitation.to_student_id == target_student_id) \
                .filter(TeamInvitation.from_student_id == current_user.id) \
                .filter(TeamInvitation.team_id == team_id) \
                .filter(TeamInvitation.status == 0) \
                .count()

            if similar_invitation_count > 0:
                return jsonify({
                    "code": 400,
                    "msg": "你已经向该同学发出过组队申请了"
                })

        # 生成邀请函
        invitation = TeamInvitation(
            from_student_id=current_user.id,
            to_student_id=target_student_id,
            team_id=team_id
        )
        db_session.add(invitation)
        db_session.commit()

        return ({
            "code": 200,
            "msg": "success",
            "data": {
                "team_invitation_id": invitation.id
            }
        })

    return jsonify({
        "code": 400,
        "msg": "数据校验错误"
    })


# 入队申请
@student_pages.post('/team/request')
@student_required()
def team_request():
    if request.json is not None:
        team_id = request.json.get('team_id')
        if current_user.team_id is not None:
            return jsonify({
                "code": 400,
                "msg": "你已经在其他队伍中了，请先退出再加入"
            })

        if team_id is not None:
            team = db_session.query(Team).get(team_id)
            if team is None:
                return jsonify({
                    "code": 404,
                    "msg": "队伍不存在"
                })
            # 校验性别
            if team.gender != current_user.gender:
                return jsonify({
                    "code": 400,
                    "msg": "不支持男女混寝"
                })

            # 看看满人了没有
            if len(team.students) >= int(get_system_setting('team_max_student_count')):
                return jsonify({
                    "code": 400,
                    "msg": "队伍满人了"
                })

            # 看看有没有重复的邀请函
            similar_invitation_count = db_session.query(TeamInvitation) \
                .filter(TeamInvitation.to_student_id == current_user.id) \
                .filter(TeamInvitation.team_id == team_id) \
                .filter(TeamInvitation.status == 0) \
                .count()

            if similar_invitation_count > 0:
                return jsonify({
                    "code": 400,
                    "msg": "你已经收到过这个队伍的邀请函了，请先处理邀请函"
                })

            similar_request_count = db_session.query(TeamRequest) \
                .filter(TeamRequest.team_id == team_id) \
                .filter(TeamRequest.student_id == current_user.id) \
                .filter(TeamRequest.status == 0) \
                .count()

            if similar_request_count > 0:
                return jsonify({
                    "code": 400,
                    "msg": "你已经向这个队伍发出过申请了，不能重复申请"
                })

            # 生成申请
            team_request = TeamRequest(team_id=team_id, student_id=current_user.id)
            db_session.add(team_request)
            db_session.commit()

            return jsonify({
                "code": 200,
                "data": {
                    "team_request_id": team_request.id
                }
            })

    return jsonify({
        "code": 400,
        "msg": "数据校验错误"
    })


@student_pages.get('/team/invitations')
@student_required()
def team_invitation_list():
    # 返回自己发出去和收到的组队申请
    team_invitations = db_session \
        .query(TeamInvitation) \
        .filter((TeamInvitation.to_student_id == current_user.id) | (TeamInvitation.from_student_id == current_user.id)) \
        .options(
        joinedload(TeamInvitation.from_student), joinedload(TeamInvitation.to_student), joinedload(TeamInvitation.team)) \
        .order_by(TeamInvitation.id.desc()) \
        .all()

    team_invitations = [team_invitation.to_dict(
        only=['id', 'team_id', 'status', 'reason', 'created_at', 'to_student.name', 'to_student.id',
              'from_student.name', 'from_student.id', 'team.id',
              'team.description']) for
        team_invitation in team_invitations]

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "team_invitations": team_invitations
        }
    })


@student_pages.get('/team/requests')
@student_required()
def team_request_list():
    if current_user.team_id is None:
        # 如果没有入队，则返回申请列表

        team_requests = db_session.query(TeamRequest) \
            .filter(TeamRequest.student_id == current_user.id) \
            .options(joinedload(TeamRequest.team), joinedload(TeamRequest.student)) \
            .order_by(TeamRequest.id.desc()) \
            .all()

        team_requests = [team_request.to_dict(
            ['id', 'team_id', 'team.id', 'team.description', 'reason', 'team.students.id', 'team.students.name',
             'status', 'student.id', 'student.name', 'created_at']) for
            team_request in
            team_requests]

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                "team_requests": team_requests
            }
        })

    team_requests = db_session \
        .query(TeamRequest) \
        .where(TeamRequest.team_id == current_user.team_id) \
        .options(joinedload(TeamRequest.student)) \
        .options(joinedload(TeamRequest.student)) \
        .order_by(TeamRequest.id.desc()) \
        .all()

    team_requests = [team_request.to_dict(['id', 'status', 'student.id', 'reason', 'student.name', 'created_at']) for
                     team_request
                     in
                     team_requests]

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "team_requests": team_requests
        }
    })


@student_pages.post('/team/invitation/process')
@student_required()
def team_invitation_process():
    if request.json is not None:
        team_invitation_id = request.json.get("team_invitation_id", None)
        accept = bool(request.json.get("accept"))

        team_invitation = db_session.query(
            TeamInvitation) \
            .options(joinedload(TeamInvitation.from_student),
                     joinedload(TeamInvitation.to_student)) \
            .filter(TeamInvitation.id == team_invitation_id) \
            .first()

        if team_invitation is not None:
            if (team_invitation.to_student_id != current_user.id) \
                    and (team_invitation.from_student_id != current_user.id):
                return jsonify({
                    "code": 403,
                    "msg": "无权操作"
                })

            if team_invitation.status != 0:
                return jsonify({
                    "code": 400,
                    "msg": "该邀请已被处理"
                })

            if not accept and team_invitation.from_student.id == current_user.id:
                team_invitation.status = -2
                team_invitation.reason = "已被{}撤回".format(current_user.name)
                db_session.commit()
                return jsonify({
                    "code": 200,
                    "msg": "success"
                })

            elif not accept:
                team_invitation.status = -1
                team_invitation.reason = "已被{}拒绝".format(current_user.name)
                db_session.commit()
                return jsonify({
                    "code": 200,
                    "msg": "success"
                })
            elif accept and team_invitation.from_student.id == current_user.id:  # 防止自己给自己同意
                return jsonify({
                    "code": 403,
                    "msg": "无权操作"
                })
            else:
                if team_invitation.team is None:
                    # 创建请求的时候已经进行性别校验
                    team = Team(gender=current_user.gender)
                    db_session.add(team)
                    db_session.commit()

                    result = team_invitation.to_student.set_team(team.id)
                    if result is not True:
                        return result
                    result = team_invitation.from_student.set_team(team.id)
                    if result is not True:
                        return result
                    team_invitation.status = 1
                    team_invitation.reason = "已创建新的队伍"
                    db_session.commit()

                    return jsonify({
                        "code": 200,
                        "msg": "success",
                        "data":
                            {
                                "create_team": True,
                                "team_id": team.id
                            }
                    })

                else:
                    result = team_invitation.to_student.set_team(team_invitation.team_id)
                    if result is not True:
                        return result
                    else:
                        team_invitation.to_student.team_id = team_invitation.team_id
                        team_invitation.status = 1
                        team_invitation.reason = None
                        db_session.commit()
                        return jsonify({
                            "code": 200,
                            "msg": "success",
                            "data":
                                {
                                    "create_team": False,
                                    "team_id": team_invitation.team_id
                                }
                        })

        return jsonify({
            "code": 404,
            "msg": "邀请不存在"
        })

    return jsonify({
        "code": 400,
        "msg": "数据校验错误"
    })


@student_pages.post("/team/request/process")
@student_required()
def team_request_process():
    if request.json is not None:
        team_request_id = request.json.get("team_request_id", None)
        accept = bool(request.json.get("accept"))

        team_request = db_session.query(TeamRequest) \
            .join(TeamRequest.student).join(TeamRequest.team) \
            .filter(TeamRequest.id == team_request_id) \
            .first()

        if team_request is not None:
            # 进行一大堆校验
            if (team_request.team_id != current_user.team_id \
                and team_request.student_id != current_user.id) \
                    or (accept and team_request.student.id == current_user.id):  # 防止自己给自己同意
                return jsonify({
                    "code": 403,
                    "msg": "无权操作"
                })

            if team_request.status != 0:
                return jsonify({
                    "code": 400,
                    "msg": "该请求已被处理"
                })

            if not accept and team_request.student.id == current_user.id:
                team_request.status = -2
                team_request.reason = "已被{}撤回".format(current_user.name)
                db_session.commit()
                return jsonify({
                    "code": 200,
                    "msg": "success"
                })
            elif not accept:
                team_request.status = -1
                team_request.reason = "已被{}拒绝".format(current_user.name)
                db_session.commit()
                return jsonify({
                    "code": 200,
                    "msg": "success"
                })
            else:
                result = team_request.student.set_team(team_request.team_id)

                if result is True:
                    team_request.status = 1
                    team_request.reason = "已被{}接受".format(current_user.name)
                    db_session.commit()

                    return jsonify({
                        "code": 200,
                        "msg": "success"
                    })
                else:
                    return result

        return jsonify({
            "code": 404,
            "msg": "请求不存在"
        })

    return jsonify({
        "code": 400,
        "msg": "数据校验错误"
    })


@student_pages.get("/system_setting")
@student_required()
def get_system_settings():
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "team_max_student_count": get_system_setting("team_max_student_count"),
            "login_bg_url": get_system_setting("login_bg_url", ""),
            "student_guide_bg_color": get_system_setting("student_guide_bg_color", ""),
            "student_logo_url": get_system_setting("student_logo_url", ""),
            "student_nav_system_name": get_system_setting("student_nav_system_name", "Roommate Matcher"),
        }
    })


@student_pages.get("/public_style")
def get_public_style():
    """Public style settings used by unauthenticated pages (e.g. login)."""
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "login_bg_url": get_system_setting("login_bg_url", ""),
            "student_logo_url": get_system_setting("student_logo_url", ""),
            "student_guide_bg_color": get_system_setting("student_guide_bg_color", ""),
        }
    })


@student_pages.get("/announcement/list")
@student_required()
def announcement_list():
    items = db_session.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": [a.to_dict() for a in items]
    })


@student_pages.get("/student/<int:id>")
@student_required()
def get_student_detail(id):
    # TODO:: 优化Questionnaire_item的 SQL
    student = db_session.query(Student).filter(Student.id == id) \
        .outerjoin(Team).outerjoin(QuestionnaireAnswer) \
        .first()

    matching_score = db_session.query(MatchingScore) \
        .filter(MatchingScore.from_student_id == student.id) \
        .filter(MatchingScore.to_student_id == current_user.id) \
        .first()

    if matching_score is not None:
        student.score = matching_score.score
    else:
        student.score = None

    student_data = student.to_dict(
        only=['id', 'name', 'team', 'team_id', 'score', 'questionnaire_answers', 'contact', 'team.id',
              'team.students.id', 'qq', 'wechat', 'province', 'mbti',
              'team.students.name', 'team.students.contact', 'team.students.qq', 'team.students.wechat', 'team.students.province',
              'team.students.mbti', 'has_answered_questionnaire'])
    student_data['avatar_url'] = get_student_avatar_url(student.id)
    if student_data.get('team') and isinstance(student_data['team'].get('students'), list):
        for member in student_data['team']['students']:
            sid = member.get('id')
            if sid is not None:
                member['avatar_url'] = get_student_avatar_url(sid)

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": student_data
    })


@student_pages.get("/team/detail")
@student_required()
def get_team_detail():
    if current_user.team is None:
        return jsonify({
            "code": 400,
            "msg": "你还没有加入任何队伍"
        })
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": current_user.team.to_dict(['id', 'description', 'students.id',
                                           'students.name', 'students.contact', 'students.qq', 'students.wechat' ,'students.has_answered_questionnaire',
                                           'students.mbti', 'students.province', 'students.questionnaire_answers'])
    })


@student_pages.post("/team/quit")
@student_required()
def quit_team():
    if current_user.team is None:
        return jsonify({
            "code": 400,
            "msg": "你还没有加入任何队伍"
        })

    result = current_user.set_team(None)
    if result is not True:
        return result

    return jsonify({
        "code": 200,
        "msg": "success"
    })


@student_pages.post("/change_password")
@student_required()
def change_password():
    if request.json is not None:
        current_pw = request.json.get('current_password')
        new_pw = request.json.get('new_password')
        if len(new_pw) < 8:
            return jsonify({
                "code": 400,
                "msg": "密码不能小于八位"
            })
        if not current_user.check_password(current_pw):
            return jsonify({
                "code": 400,
                "msg": "旧密码不正确"
            })

        hashed_pw = bcrypt.hashpw(bytes(new_pw, encoding='utf8'), bcrypt.gensalt())
        current_user.password = hashed_pw
        db_session.commit()
        return jsonify({
            "code": 200,
            "msg": "success"
        })


@student_pages.post("/update_contact")
@student_required()
def update_contact():
    if request.json is not None:

        new_QQ = request.json.get('qq')
        new_Wechat = request.json.get('wechat')
        if (new_Wechat is None or len(new_Wechat) < 1) and (new_QQ is None or len(new_QQ) < 1):
            return jsonify({
                "code": 400,
                "msg": "QQ和Wechat不能同时为空"
            })
        new_Province = request.json.get('province')
        # if new_Phone is None:
        #     return jsonify({
        #         "code": 400,
        #         "msg": "电话不能为空"
        #     })
        new_MBTI = request.json.get('mbti')
        new_contact = request.json.get("contact")

        current_user.contact = new_contact
        current_user.qq = new_QQ
        current_user.wechat = new_Wechat
        current_user.province = new_Province
        current_user.mbti = new_MBTI

        db_session.commit()
        return jsonify({
            "code": 200,
            "msg": "success"
        })

    else:
        return jsonify({
            "code": 400,
            "msg": "数据校验错误"
        })


@student_pages.post("/avatar/upload")
@student_required()
def upload_avatar():
    f = request.files.get("file")
    if f is None:
        return jsonify({"code": 400, "msg": "缺少 file"}), 400

    if request.content_length is not None and int(request.content_length) > 10 * 1024 * 1024:
        return jsonify({"code": 400, "msg": "头像最大 10MB"}), 400

    filename = secure_filename(f.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_AVATAR_EXTS:
        return jsonify({"code": 400, "msg": "仅支持 png/jpg/jpeg 格式"}), 400

    save_dir = _avatar_dir()
    os.makedirs(save_dir, exist_ok=True)

    for old_ext in ALLOWED_AVATAR_EXTS:
        old_path = os.path.join(save_dir, f"{current_user.id}{old_ext}")
        if os.path.exists(old_path):
            os.remove(old_path)

    out_name = f"{current_user.id}{ext}"
    out_path = os.path.join(save_dir, out_name)
    f.save(out_path)
    url = f"/static/uploads/student_avatar/{out_name}?t={int(time.time())}"
    return jsonify({"code": 200, "msg": "success", "data": {"url": url}})
