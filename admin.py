import copy
import datetime
import os
import uuid
from functools import wraps

import bcrypt
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import create_access_token, verify_jwt_in_request, get_jwt, current_user
from sqlalchemy.orm import joinedload
from sqlalchemy import or_, func
from werkzeug.utils import secure_filename

from database import db_session
from models import Admin, Student, Team, ExchangingNeed, CustomQuestionnaireItem, SystemSetting, QuestionnaireItem, \
    MatchingScore, QuestionnaireAnswer, TeamRequest, TeamInvitation, get_system_setting, CustomQuestionnaireAnswer, \
    ExchangingRequest, Announcement, QuestionnairePage

admin_pages = Blueprint('admin_pages', __name__, template_folder="templates/admin")

def admin_required():
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            if claims["role"] == "admin":
                return fn(*args, **kwargs)
            else:
                return jsonify({
                    "code": 403,
                    "msg": "无权限！"
                }), 403

        return decorator

    return wrapper


@admin_pages.route('/login', methods=['POST'])
def login():
    if request.json is not None:
        email = request.json.get('email', None)
        password = request.json.get('password', None)
        admin = db_session.query(Admin).where(Admin.email == email).first()
        if admin is not None and admin.check_password(password):
            admin.last_logged_at = datetime.datetime.now()
            db_session.commit()
            access_token = create_access_token(identity=admin.id, additional_headers={
                "role": "admin"
            }, additional_claims={
                "role": "admin"
            })

            return jsonify({
                "code": 200,
                "msg": "success",
                "data": {
                    "access_token": access_token
                }
            })

    return jsonify({
        "code": 401,
        "msg": "邮箱地址或密码错误！"
    })


@admin_pages.post('/logout')
@admin_required()
def logout():
    return jsonify({
        "code": 200,
        "msg": "success"
    })


@admin_pages.get("/userinfo")
@admin_required()
def userinfo():
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "user": current_user.to_dict(rules=['-password'])
        }
    })


@admin_pages.post("/change_password")
@admin_required()
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


@admin_pages.get("/student/list")
@admin_required()
def student_list():
    students = (
        db_session.query(
            Student.id,
            Student.name,
            Student.last_logged_at,
            Student.gender,
            Student.created_at,
            Student.team_id,
            Team.id.label("team_id"),
            Team.description.label("team_description"),
            func.count(QuestionnaireAnswer.id).label("answers_count")
        )
        .outerjoin(Team)  # Explicit join with Team
        .outerjoin(QuestionnaireAnswer)  # Outer join with QuestionnaireAnswers, only if answers need to be counted
        .group_by(Student.id, Team.id)
        .all()
    )

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "students": [{
                "id": student.id,
                "name": student.name,
                "team": {
                    "id": student.team_id,
                    "description": student.team_description
                } if student.team_id else None,
                "last_logged_at": student.last_logged_at,
                "gender": student.gender,
                "created_at": student.created_at,
                "has_answered_questionnaire": student.answers_count > 0,
                "team_id": student.team_id
            } for student in students]
        }
    })


@admin_pages.post("/student/import")
@admin_required()
def student_import():
    if request.json is not None and request.json.get("students", None) is not None:
        students = request.json.get("students")

        data_to_store = []
        students_id_in_store = [item[0] for item in db_session.query(Student.id).all()]
        for student in copy.deepcopy(students):
            # 导入时要把学生姓名里的空格替换为#
            space_count = student.split()
            if len(space_count) != 4:
                continue
            id, name, gender, password = space_count
            id = int(id)

            # 查重
            if id in students_id_in_store:
                continue
            else:
                students_id_in_store.append(id)
                students.remove(student)

            name = name.replace("#", " ")
            password = bcrypt.hashpw(bytes(password, encoding="utf8"), bcrypt.gensalt())
            data_to_store.append(
                Student(id=id, name=name, gender=gender, password=password, last_logged_at=None)
            )

        db_session.bulk_save_objects(data_to_store)

        db_session.commit()

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                "fail_to_import": students
            }
        })

    return jsonify({
        "code": 406,
        "msg": "获取数据失败"
    })


@admin_pages.post("/student/update")
@admin_required()
def student_update():
    if request.json is not None:
        student_id = request.json.get('id')
        name = request.json.get('name')
        contact = request.json.get('contact')
        gender = request.json.get('gender')
        password = request.json.get('password', None)
        team_id = request.json.get('team_id', None)
        student = db_session.query(Student).get(student_id)

        if student is not None:
            result = student.set_team(team_id)
            if result is not True:
                return result

            student.name = name
            student.contact = contact
            student.gender = gender

            if password is not None:
                hashed_password = bcrypt.hashpw(bytes(password, encoding='utf8'), bcrypt.gensalt())
                student.password = hashed_password

            db_session.commit()

            return jsonify({
                "code": 200,
                "msg": "success"
            })

        else:
            return jsonify({
                "code": 404,
                "msg": "学生不存在"
            })

    return jsonify({
        "code": 400,
        "msg": "更新失败"
    })


@admin_pages.get('/student/info')
@admin_required()
def student_info():
    if request.args is not None:
        id = request.args.get('student_id', None)
        student = db_session.query(Student).filter(Student.id == id) \
            .outerjoin(Team).outerjoin(CustomQuestionnaireItem).outerjoin(ExchangingNeed).first()
        if student is not None:
            return jsonify({
                "code": 200,
                "msg": "success",
                "data": {
                    "student": student.to_dict(only=[
                        'id', 'name', 'last_logged_at', 'contact', 'team_id', 'questionnaire_answers', 'gender',
                        'team.students.name', 'team.students.id', 'team.description', 'team_requests.id',
                        'team_requests.team_id'
                    ])
                }
            })

    return jsonify({
        "code": 404,
        "msg": "学生不存在"
    })


@admin_pages.post('/student/questionnaire')
@admin_required()
def student_questionnaire():
    if request.json is not None:
        id = request.json.get('student_id', None)
        questionnaire_answers = request.json.get('questionnaire_answers')

        if type(questionnaire_answers) is not dict:
            return jsonify({
                "code": 400,
                "msg": "问卷答案数据错误"
            })
        student = db_session.query(Student).get(id)
        if student is None:
            return jsonify({
                "code": 404,
                "msg": "学生不存在"
            })

        exist_answers = student.questionnaire_answers
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
            for exist_answer in exist_answers:
                if exist_answer.item_id == key:
                    need_to_create = False
                    if exist_answer.answer != str(value['answer']) or exist_answer.weight != value['weight']:
                        exist_answer.answer = str(value['answer'])
                        exist_answer.weight = value['weight']
                        exist_answer.updated_at = datetime.datetime.now()
                        db_session.commit()
                        data_changed = True

            if need_to_create:
                new_answer = QuestionnaireAnswer(item_id=key, answer=str(value['answer']), student_id=student.id,
                                                 weight=value['weight'])
                bulk_save_models.append(new_answer)
                data_changed = True

        # 重头戏 存数据

        if data_changed:
            db_session.bulk_save_objects(bulk_save_models)
            db_session.commit()

            # 删除匹配得分
            db_session.query(MatchingScore) \
                .filter((MatchingScore.to_student_id == student.id) | (
                    MatchingScore.from_student_id == student.id)) \
                .delete(synchronize_session=False)

            db_session.commit()

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                "fail_to_save": missed_items
            }
        })


@admin_pages.delete('/student/delete')
@admin_required()
def student_delete():
    if request.json is not None:
        id = request.json.get('student_id', None)
        student = db_session.query(Student).get(id)
        if student is not None:
            db_session.query(MatchingScore).where(or_(MatchingScore.from_student_id == student.id, MatchingScore.to_student_id == student.id)).delete()
            db_session.query(TeamInvitation).where(TeamInvitation.from_student_id == student.id).delete()
            db_session.query(TeamInvitation).where(TeamInvitation.to_student_id == student.id).update({
                TeamInvitation.to_student_id: 0,
                TeamInvitation.status: -2,
                TeamInvitation.reason: "目标用户已被删除"
            })

            db_session.delete(student)
            db_session.commit()

    return jsonify({
        "code": 200,
        "msg": "success"
    })


@admin_pages.get('/system_setting/list')
@admin_required()
def system_setting_list():
    system_settings = db_session.query(SystemSetting).all()
    system_settings = [system_setting.to_dict() for system_setting in system_settings]

    return jsonify({
        "code": 200,
        "msg": "success",
        "data": system_settings
    })


@admin_pages.get('/system_setting/get')
@admin_required()
def system_setting_get():
    key = request.args.get('key', None)
    if key is not None:
        system_setting = db_session.query(SystemSetting).filter(SystemSetting.key == key).first()
        if system_setting is None:
            value = None
        else:
            value = system_setting.value
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                key: value
            }
        })

    return jsonify({
        "code": 400,
        "msg": "缺少必要参数"
    })


@admin_pages.post('/system_setting/update')
@admin_required()
def system_setting_update():
    if request.json is not None:
        for key, value in request.json.items():
            item_in_db = db_session.query(SystemSetting).filter(SystemSetting.key == key).first()
            if item_in_db is not None:
                if item_in_db.value == value:
                    continue
                item_in_db.value = value
                item_in_db.updated_at = datetime.datetime.now()

            else:
                item = SystemSetting(key=key, value=value)
                db_session.add(item)

            db_session.commit()

    return jsonify({
        "code": 200,
        "msg": "success"
    })


@admin_pages.delete('/system_setting/delete')
@admin_required()
def system_setting_delete():
    if request.json is not None:
        key = request.json.get('key', None)
        item = db_session.query(SystemSetting).filter(SystemSetting.key == key).first()

        if item is not None:
            db_session.delete(item)
            db_session.commit()

    return jsonify({
        "code": 200,
        "msg": "success"
    })


@admin_pages.post('/system_style/upload')
@admin_required()
def system_style_upload():
    """
    Upload system style assets (login background / student logo).
    Returns a URL under /static/uploads/...
    """
    kind = request.args.get("kind", "")
    if kind not in ("login_bg", "student_logo"):
        return jsonify({"code": 400, "msg": "kind 参数错误"}), 400

    f = request.files.get("file")
    if f is None:
        return jsonify({"code": 400, "msg": "缺少 file"}), 400

    # size limit: 10MB
    if request.content_length is not None and int(request.content_length) > 10 * 1024 * 1024:
        return jsonify({"code": 400, "msg": "图片最大 10MB"}), 400

    filename = secure_filename(f.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return jsonify({"code": 400, "msg": "仅支持 png/jpg/jpeg 格式"}), 400

    rel_dir = os.path.join("uploads", "system_style")
    abs_dir = os.path.join(current_app.root_path, "static", rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    out_name = f"{kind}-{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(abs_dir, out_name)
    f.save(abs_path)

    url = f"/static/{rel_dir}/{out_name}"
    return jsonify({"code": 200, "msg": "success", "data": {"url": url}})


@admin_pages.get('/announcement/list')
@admin_required()
def announcement_list():
    items = db_session.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": [a.to_dict() for a in items]
    })


@admin_pages.post('/announcement')
@admin_required()
def announcement_create():
    if request.json is None:
        return jsonify({"code": 400, "msg": "缺少参数"}), 400
    title = request.json.get('title', '').strip()
    content = request.json.get('content') or ''
    if not title:
        return jsonify({"code": 400, "msg": "标题不能为空"}), 400
    item = Announcement(title=title, content=content)
    db_session.add(item)
    db_session.commit()
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": item.to_dict()
    })


@admin_pages.put('/announcement/<int:aid>')
@admin_required()
def announcement_update(aid):
    item = db_session.query(Announcement).filter(Announcement.id == aid).first()
    if item is None:
        return jsonify({"code": 404, "msg": "公告不存在"}), 404
    if request.json:
        if 'title' in request.json:
            title = request.json.get('title', '').strip()
            if title:
                item.title = title
        if 'content' in request.json:
            item.content = request.json.get('content') or ''
    item.updated_at = datetime.datetime.now()
    db_session.commit()
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": item.to_dict()
    })


@admin_pages.delete('/announcement/<int:aid>')
@admin_required()
def announcement_delete(aid):
    item = db_session.query(Announcement).filter(Announcement.id == aid).first()
    if item is None:
        return jsonify({"code": 404, "msg": "公告不存在"}), 404
    db_session.delete(item)
    db_session.commit()
    return jsonify({"code": 200, "msg": "success"})


@admin_pages.get('/questionnaire/list')
@admin_required()
def questionnaire_list():
    # structured: pages + items
    pages = db_session.query(QuestionnairePage).order_by(QuestionnairePage.index.asc()).all()
    pages_out = []
    for p in pages:
        items = db_session.query(QuestionnaireItem) \
            .filter(QuestionnaireItem.page_id == p.id) \
            .order_by(QuestionnaireItem.index_in_page.asc()) \
            .all()
        pages_out.append({
            **p.to_dict(only=['id', 'title', 'remark', 'index', 'created_at', 'updated_at']),
            "items": [i.to_dict(only=[
                'id', 'title', 'weight', 'data_type', 'params', 'type',
                'index', 'page_id', 'index_in_page', 'created_at', 'updated_at'
            ]) for i in items]
        })
    return jsonify({"code": 200, "msg": "success", "data": {"pages": pages_out}})


@admin_pages.get('/questionnaire/pages')
@admin_required()
def questionnaire_pages_list():
    pages = db_session.query(QuestionnairePage).order_by(QuestionnairePage.index.asc()).all()
    return jsonify({"code": 200, "msg": "success", "data": [p.to_dict(only=['id', 'title', 'remark', 'index', 'created_at', 'updated_at']) for p in pages]})


@admin_pages.post('/questionnaire/page')
@admin_required()
def questionnaire_page_create():
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    title = (request.json.get("title") or "").strip()
    if not title:
        return jsonify({"code": 400, "msg": "title 不能为空"}), 400
    remark = request.json.get("remark") or ""
    index = request.json.get("index")
    if index is None:
        max_index = db_session.query(func.max(QuestionnairePage.index)).scalar()
        index = (max_index or 0) + 1
    p = QuestionnairePage(title=title, remark=remark, index=int(index))
    db_session.add(p)
    db_session.commit()
    return jsonify({"code": 200, "msg": "success", "data": p.to_dict()})


@admin_pages.put('/questionnaire/page/<int:page_id>')
@admin_required()
def questionnaire_page_update(page_id):
    p = db_session.query(QuestionnairePage).filter_by(id=page_id).first()
    if p is None:
        return jsonify({"code": 404, "msg": "分页不存在"}), 404
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    data = request.json
    if "title" in data:
        t = (data.get("title") or "").strip()
        if t:
            p.title = t
    if "remark" in data:
        p.remark = data.get("remark") or ""
    if "index" in data:
        p.index = int(data["index"])
    p.updated_at = datetime.datetime.now()
    db_session.commit()
    return jsonify({"code": 200, "msg": "success", "data": p.to_dict()})


@admin_pages.delete('/questionnaire/page/<int:page_id>')
@admin_required()
def questionnaire_page_delete(page_id):
    p = db_session.query(QuestionnairePage).filter_by(id=page_id).first()
    if p is None:
        return jsonify({"code": 404, "msg": "分页不存在"}), 404
    items_count = db_session.query(QuestionnaireItem).filter(QuestionnaireItem.page_id == page_id).count()
    if items_count > 0:
        return jsonify({"code": 400, "msg": "该分页下仍有题目，请先迁移或删除题目"}), 400
    db_session.delete(p)
    db_session.commit()
    return jsonify({"code": 200, "msg": "success"})


@admin_pages.post('/questionnaire/set')
@admin_required()
def questionnaire_set():
    if request.json is not None:
        # 删除所有的匹配分
        # 删除所有的问卷答案
        old_items = [MatchingScore, QuestionnaireAnswer, QuestionnaireItem]
        for old_item in old_items:
            db_session.query(old_item).delete()
            db_session.commit()

        # 重新写入
        item_list = []

        for piece in request.json:
            title = piece.get('title', None)
            weight = piece.get('weight', None)
            data_type = piece.get('data_type', None)
            params = piece.get('params', "{}")
            index = piece.get('index', None)
            id = piece.get('id', None)
            type = piece.get('type', 'text')
            if type == 'text':
                weight = 0

            item = QuestionnaireItem(
                title=title,
                weight=weight,
                data_type=data_type,
                params=str(params),
                index=index,
                id=id,
                type=type
            )
            item_list.append(item)

        db_session.bulk_save_objects(item_list)
        db_session.commit()

        return jsonify({
            "code": 200,
            "msg": "success"
        })


@admin_pages.post('/questionnaire/item')
@admin_required()
def questionnaire_item_create():
    """Add a single questionnaire item. Does not affect existing answers."""
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    data = request.json
    title = data.get('title')
    if not title:
        return jsonify({"code": 400, "msg": "title 不能为空"}), 400
    item_id = data.get('id')
    if not item_id:
        import uuid
        item_id = 'item_' + uuid.uuid4().hex[:16]
    if db_session.query(QuestionnaireItem).filter_by(id=item_id).first():
        return jsonify({"code": 400, "msg": "该 id 已存在"}), 400
    weight = data.get('weight', 1.0)
    data_type = data.get('data_type', 'string')
    params = data.get('params', '{}')
    if isinstance(params, dict):
        params = str(params)
    page_id = data.get('page_id')
    index_in_page = data.get('index_in_page')
    if page_id is None:
        first_page = db_session.query(QuestionnairePage).order_by(QuestionnairePage.index.asc()).first()
        page_id = first_page.id if first_page else None
    if index_in_page is None and page_id is not None:
        max_i = db_session.query(func.max(QuestionnaireItem.index_in_page)) \
            .filter(QuestionnaireItem.page_id == int(page_id)).scalar()
        index_in_page = (max_i or 0) + 1
    # legacy index (global) still maintained for backward compatibility
    index = data.get('index')
    if index is None:
        max_index = db_session.query(func.max(QuestionnaireItem.index)).scalar()
        index = (max_index or 0) + 1
    item_type = data.get('type', 'text')
    if item_type == 'text':
        weight = 0
    item = QuestionnaireItem(
        id=item_id,
        title=title,
        weight=float(weight),
        data_type=data_type,
        params=params,
        index=int(index),
        type=item_type,
        page_id=int(page_id) if page_id is not None else None,
        index_in_page=int(index_in_page) if index_in_page is not None else int(index),
    )
    db_session.add(item)
    db_session.commit()
    return jsonify({"code": 200, "msg": "success", "data": item.to_dict()})


@admin_pages.put('/questionnaire/item/<item_id>')
@admin_required()
def questionnaire_item_update(item_id):
    """Update a single questionnaire item. Does not delete existing answers."""
    item = db_session.query(QuestionnaireItem).filter_by(id=item_id).first()
    if item is None:
        return jsonify({"code": 404, "msg": "题目不存在"}), 404
    if request.json is None:
        return jsonify({"code": 400, "msg": "请求体不能为空"}), 400
    data = request.json
    if 'title' in data:
        item.title = data['title']
    if 'weight' in data:
        w = data['weight']
        if item.type != 'text':
            item.weight = float(w)
    if 'data_type' in data:
        item.data_type = data['data_type']
    if 'params' in data:
        p = data['params']
        item.params = str(p) if not isinstance(p, str) else p
    if 'index' in data:
        item.index = int(data['index'])
    if 'page_id' in data:
        item.page_id = int(data['page_id']) if data['page_id'] is not None else None
    if 'index_in_page' in data:
        item.index_in_page = int(data['index_in_page'])
    if 'type' in data:
        item.type = data['type']
        if item.type == 'text':
            item.weight = 0
    item.updated_at = datetime.datetime.now()
    db_session.commit()
    return jsonify({"code": 200, "msg": "success", "data": item.to_dict()})


@admin_pages.delete('/questionnaire/item/<item_id>')
@admin_required()
def questionnaire_item_delete(item_id):
    """Delete a single questionnaire item and all its answers."""
    item = db_session.query(QuestionnaireItem).filter_by(id=item_id).first()
    if item is None:
        return jsonify({"code": 404, "msg": "题目不存在"}), 404
    db_session.query(QuestionnaireAnswer).filter_by(item_id=item_id).delete(synchronize_session=False)
    db_session.delete(item)
    db_session.commit()
    return jsonify({"code": 200, "msg": "success"})


@admin_pages.post('/system_reset/perform')
@admin_required()
# @jwt_required(fresh=True)
def system_reset():
    if request.json is None:
        return jsonify({
            "code": 400,
            "msg": "密码不能为空"
        })

    password = request.json.get("password")

    # check_password
    if not current_user.check_password(password):
        return jsonify({
            "code": 400,
            "msg": "密码错误"
        })

    # 删除matching scores
    db_session.query(MatchingScore).delete()

    # 删除所有自定义问卷答案
    db_session.query(CustomQuestionnaireAnswer).delete()

    # 删除所有自定义问卷
    db_session.query(CustomQuestionnaireItem).delete()

    # 删除所有问卷答案
    db_session.query(QuestionnaireAnswer).delete()

    # 删除所有组队邀请信息
    db_session.query(TeamInvitation).delete()

    # 删除所有组队请求
    db_session.query(TeamRequest).delete()

    # 删除所有组队信息
    db_session.query(Team).delete()

    # 删除所有交换请求
    db_session.query(ExchangingNeed).delete()
    db_session.query(ExchangingRequest).delete()

    # 删除所有学生账号
    db_session.query(Student).delete()

    db_session.commit()

    return jsonify({
        "code": 200,
        "msg": "success"
    })


@admin_pages.get('/team/list')
@admin_required()
def team_list():
    teams = db_session.query(Team).options(joinedload(Team.students)).all()

    teams = [team.to_dict(only=['id', 'gender', 'description', 'created_at', 'students.id', 'students.name']) for team
             in teams]
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "teams": teams
        }
    })


@admin_pages.post('/team/join')
@admin_required()
def team_join():
    if request.json is not None:
        team_id = request.json.get('team_id', None)
        student_id = request.json.get('student_id', None)

        student = db_session.query(Student).get(student_id)

        if student is not None:
            if student.team_id is not None:
                return jsonify({
                    "code": 400,
                    "msg": "学生已经在其他队伍中，请先退出再加入"
                })

            team = db_session.query(Team).get(team_id)

            if team is None:
                return jsonify({
                    "code": 404,
                    "msg": "队伍不存在"
                })
            if int(student.gender) != int(team.gender):
                return jsonify({
                    "code": 400,
                    "msg": "性别不同，不能男女混寝"
                })

        students_count_in_team = db_session.query(Student).where(Student.team_id == team_id).count()

        if int(students_count_in_team) >= int(get_system_setting("team_max_student_count", 4)):
            return jsonify({
                "code": 400,
                "msg": "该队伍人数已满"
            })

        student.team_id = team_id
        db_session.commit()
        return jsonify({
            "code": 200,
            "msg": "success"
        })


@admin_pages.post('/team/kick')
@admin_required()
def team_kick():
    if request.json is not None:
        student_id = request.json.get('student_id', None)
        student = db_session.query(Student).get(student_id)
        if student is not None:
            if student.team is None:
                return jsonify({
                    "code": 400,
                    "msg": "学生未加入任何队伍"
                })

            # if len(student.team.students) <= 2:
            #     # 清退队伍里的所有学生 并删除队伍
            #     team = deepcopy(student.team)
            #     for item in student.team.students:
            #         item.team_id = None
            #
            #     db_session.bulk_save_objects(student.team.students)
            #     db_session.commit()
            #
            #     db_session.delete(team)
            #     db_session.commit()
            #     return jsonify({
            #         "code": 200,
            #         "msg": "success",
            #         "delete_team": True,
            #         "delete_team_id": team.id
            #     })

            student.team_id = None
            db_session.commit()

            return jsonify({
                "code": 200,
                "msg": "success"
            })


@admin_pages.delete('/team/delete')
@admin_required()
def team_delete():
    if request.json is not None:
        team_id = request.json.get('team_id', None)
        team = db_session.query(Team).get(team_id)

        if team is None:
            return jsonify({
                "code": 200,
                "msg": "success"
            })

        for student in team.students:
            student.team_id = None

        db_session.query(TeamInvitation).where(TeamInvitation.team_id == team_id).update({
            TeamInvitation.team_id: None
        })

        db_session.bulk_save_objects(team.students)

        db_session.delete(team)
        db_session.commit()

        return jsonify({
            "code": 200,
            "msg": "success"
        })


@admin_pages.post('/team/create')
@admin_required()
def team_create():
    if request.json is not None:
        description = request.json.get("description", None)
        gender = request.json.get("gender", 1)

        team = Team(description=description, gender=gender)

        db_session.add(team)
        db_session.commit()

        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                "team_id": team.id
            }
        })


@admin_pages.post('/team/add_student')
@admin_required()
def team_add_student():
    if request.json is not None:
        student_ids = request.json.get('students')
        team_id = request.json.get('team_id')

        # 判断空队伍不能用 Student.team_id is None
        students = db_session.query(Student) \
            .filter(Student.id.in_(student_ids)) \
            .filter(Student.team_id.is_(None)) \
            .all()

    if len(students) is not len(student_ids):
        for student in students:
            student_ids.remove(student.id)

        return jsonify({
            "code": 400,
            "msg": "部分学生不存在于数据库中，或已经加入其他队伍",
            "data": {
                "students_not_found": student_ids
            }
        })
    else:
        for student in students:
            result = student.set_team(team_id)
            if result is not True:
                # Rollback
                db_session.query(Student).filter(Student.id.in_(student_ids)).update({
                    Student.team_id: None
                })
                db_session.commit()

                return result

        return jsonify({
            "code": 200,
            "msg": "success"
        })
