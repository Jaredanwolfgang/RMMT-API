#!/usr/bin/env python3
"""
Seed local RMMT test data.

What this script does:
1) Rebuilds the MVP questionnaire with 6 pages and 19 questions.
3) Resets business data (admins/students/teams/answers and related tables).
4) Inserts:
   - 1 admin account
   - 32 student accounts (16 male, 16 female) with random profile data by default
   - questionnaire answers for all students, each with random weights
   - team distribution per gender:
       * 4 students in a full team
       * 2 students in a half team
       * 4 students with no team
5) Generates simple avatar PNG files for students who are not in a full team (满员队不生成头像).
6) Exports admin/student login accounts to RMMT-API/test-data/accounts.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import string
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt
import pymysql


@dataclass
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


PROVINCES = [
    "Beijing",
    "Shanghai",
    "Guangdong",
    "Zhejiang",
    "Jiangsu",
    "Sichuan",
    "Hubei",
    "Shandong",
    "Henan",
    "Fujian",
]

MBTI_TYPES = [
    "INTJ",
    "INTP",
    "ENTJ",
    "ENTP",
    "INFJ",
    "INFP",
    "ENFJ",
    "ENFP",
    "ISTJ",
    "ISFJ",
    "ESTJ",
    "ESFJ",
    "ISTP",
    "ISFP",
    "ESTP",
    "ESFP",
]

# 每词 ≤12 字符，与前端「三个词、每词最多 12 字符」一致
INTEREST_WORDS = [
    "read",
    "coding",
    "running",
    "film",
    "gaming",
    "art",
    "travel",
    "java",
    "ruby",
    "golf",
    "yoga",
    "swimming",
    "drawing",
    "hiking",
    "music",
    "reading",
    "movies",
    "design",
    "fitness",
    "basketball",
    "photography",
]

MVP_QUESTIONNAIRE = [
    ("基础生活习惯", "作息规律与日常节奏", [
        ("q_mvp_sleep_time", "你通常几点睡觉？", ["22:00 前", "22:00–23:30", "23:30–01:00", "01:00–02:00", "02:00 后"]),
        ("q_mvp_wake_time", "你通常几点起床？", ["7:00 前", "7:00–8:30", "8:30–10:00", "10:00–12:00", "12:00 后"]),
        ("q_mvp_routine", "你的作息规律程度是？", ["非常规律", "比较规律", "一般", "有点不规律", "非常不规律"]),
    ]),
    ("卫生与公共空间", "整洁、打扫和公共空间偏好", [
        ("q_mvp_cleanliness", "你平时对宿舍整洁程度的习惯是？", ["非常随意", "有点随意", "一般", "比较整洁", "非常整洁"]),
        ("q_mvp_cleaning_frequency", "你希望宿舍公共区域多久打扫一次？", ["随缘", "每两周一次", "每周一次", "每周多次", "尽量每天保持干净"]),
        ("q_mvp_smell_sensitivity", "你对气味、垃圾、外卖盒等问题的敏感程度是？", ["完全不敏感", "不太敏感", "一般", "比较敏感", "非常敏感"]),
    ]),
    ("噪声与安静程度", "宿舍声音、外放和夜间使用设备习惯", [
        ("q_mvp_quietness", "你在宿舍通常有多安静？", ["非常安静", "比较安静", "一般", "偶尔会外放/语音", "经常外放/语音"]),
        ("q_mvp_noise_sensitivity", "你对舍友制造声音的敏感程度是？", ["完全不敏感", "不太敏感", "一般", "比较敏感", "非常敏感"]),
        ("q_mvp_night_computer", "你晚上是否经常使用电脑、打游戏、语音、看视频？", ["几乎不", "偶尔", "一般", "经常", "几乎每天"]),
    ]),
    ("社交与宿舍氛围", "舍友关系、社交能量和访客接受度", [
        ("q_mvp_roommate_relation", "你理想中的舍友关系是？", ["互不打扰", "偶尔聊天", "可以一起吃饭/学习", "经常一起活动", "像朋友一样相处"]),
        ("q_mvp_social_energy", "你的社交能量是？", ["很低，喜欢独处", "偏低", "一般", "偏高", "很高，喜欢热闹"]),
        ("q_mvp_visitor_acceptance", "你接受舍友带朋友来宿舍的程度是？", ["尽量不要", "偶尔可以", "提前说就可以", "比较无所谓", "完全无所谓"]),
    ]),
    ("学习与生活节奏", "宿舍学习、工作和日常状态偏好", [
        ("q_mvp_dorm_study", "你是否经常在宿舍学习或工作？", ["几乎不", "偶尔", "一半一半", "经常", "几乎每天"]),
        ("q_mvp_dorm_state", "你在宿舍时更偏向哪种状态？", ["安静休息", "自己做自己的事", "学习/工作", "娱乐放松", "聊天互动"]),
    ]),
    ("文本题", "用于语义向量和后续解释的开放问题", [
        ("q_mvp_self_intro", "请简单介绍一下你自己", None),
        ("q_mvp_interests", "你的兴趣爱好是什么？", None),
        ("q_mvp_dorm_atmosphere", "你喜欢什么样的宿舍氛围？", None),
        ("q_mvp_representative_thing", "写一个能代表你的东西", None),
        ("q_mvp_roommate_note", "有什么希望未来舍友提前知道的事情？", None),
    ]),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed RMMT test data")
    parser.add_argument("--db-host", default=os.getenv("DB_HOST", "127.0.0.1"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--db-user", default=os.getenv("DB_USER", "root"))
    parser.add_argument("--db-password", default=os.getenv("DB_PASSWORD", ""))
    parser.add_argument("--db-name", default=os.getenv("DB_NAME", "roommate"))
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="Admin123456")
    parser.add_argument("--student-password", default="Student123")
    parser.add_argument("--student-count", type=int, default=32, help="Total generated students, split evenly by gender")
    parser.add_argument("--seed", type=int, default=20260319, help="Random seed for reproducible data")
    return parser.parse_args()


def db_connect(cfg: DbConfig):
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def random_digits(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(length))


def random_word(rng: random.Random, min_len: int = 5, max_len: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    size = rng.randint(min_len, max_len)
    return "".join(rng.choice(chars) for _ in range(size))


def random_name(rng: random.Random, gender: int, idx: int) -> str:
    prefix = "M" if gender == 1 else "F"
    return f"{prefix}Student{idx:02d}"


def random_contact(rng: random.Random) -> str:
    """三个词，与前端一致用英文分号分隔（无空格）。"""
    picks = rng.sample(INTEREST_WORDS, 3)
    return ";".join(picks)


def upsert_system_setting(cur, key: str, value: str) -> None:
    cur.execute("SELECT id FROM system_settings WHERE `key`=%s LIMIT 1", (key,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE system_settings SET `value`=%s WHERE id=%s", (value, row["id"]))
    else:
        cur.execute("INSERT INTO system_settings(`key`,`value`) VALUES(%s,%s)", (key, value))


def ensure_schema(cur) -> None:
    # page table used by current student questionnaire page flow
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questionnaire_pages (
          id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          title VARCHAR(255) NOT NULL,
          remark TEXT NULL,
          `index` INT DEFAULT 1,
          created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questionnaire_page_answers (
          id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          student_id BIGINT NOT NULL,
          page_id INT NOT NULL,
          answers_json LONGTEXT NULL,
          status TINYINT DEFAULT 0,
          created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_qpa_student (student_id),
          KEY idx_qpa_page (page_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )

    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'questionnaire_items'
          AND COLUMN_NAME = 'page_id'
        """
    )
    if int(cur.fetchone()["cnt"]) == 0:
        cur.execute("ALTER TABLE questionnaire_items ADD COLUMN page_id INT NULL")

    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'questionnaire_items'
          AND COLUMN_NAME = 'index_in_page'
        """
    )
    if int(cur.fetchone()["cnt"]) == 0:
        cur.execute("ALTER TABLE questionnaire_items ADD COLUMN index_in_page INT DEFAULT 1")


def ensure_questionnaire(cur) -> None:
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    for table in ("matching_scores", "questionnaire_answers", "questionnaire_page_answers", "questionnaire_items", "questionnaire_pages"):
        cur.execute(f"DELETE FROM {table}")
    cur.execute("SET FOREIGN_KEY_CHECKS=1")

    global_index = 1
    for pidx, (title, remark, items) in enumerate(MVP_QUESTIONNAIRE, start=1):
        cur.execute(
            "INSERT INTO questionnaire_pages(title, remark, `index`) VALUES(%s,%s,%s)",
            (title, remark, pidx),
        )
        page_id = int(cur.lastrowid)

        for index_in_page, (item_id, item_title, options) in enumerate(items, start=1):
            item_type = "textarea" if options is None else "radio"
            params = {"placeholder": "请输入你的回答"} if options is None else {"options": options}
            weight = 0 if options is None else 1
            cur.execute(
                """
                INSERT INTO questionnaire_items(
                    id, title, weight, data_type, params, `index`, `type`, page_id, index_in_page
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    item_id,
                    item_title,
                    weight,
                    "string",
                    json.dumps(params, ensure_ascii=False),
                    global_index,
                    item_type,
                    page_id,
                    index_in_page,
                ),
            )
            global_index += 1


def reset_business_data(cur) -> None:
    # keep questionnaire and system settings, reset user/business data
    tables = [
        "matching_scores",
        "custom_questionnaire_answers",
        "custom_questionnaire_items",
        "exchanging_requests",
        "exchanging_needs",
        "team_invitations",
        "team_requests",
        "questionnaire_answers",
        "questionnaire_page_answers",
        "students",
        "teams",
        "admins",
    ]
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    for table in tables:
        cur.execute(f"DELETE FROM {table}")
    cur.execute("SET FOREIGN_KEY_CHECKS=1")


def parse_item_options(params: Any) -> list[str]:
    if params is None:
        return []
    if isinstance(params, dict):
        data = params
    else:
        txt = str(params)
        data = {}
        try:
            data = json.loads(txt)
        except Exception:
            data = {}
    options = data.get("options") or data.get("data") or []
    if isinstance(options, list):
        return [str(x) for x in options if str(x).strip()]
    return []


def generate_answer_and_weight(rng: random.Random, item: dict[str, Any]) -> tuple[str, float]:
    item_type = str(item.get("type") or "text").lower()
    params = item.get("params")
    options = parse_item_options(params)
    item_id = str(item.get("id") or "")

    if item_type in {"select", "radio"} and options:
        answer = rng.choice(options)
    elif item_type == "checkbox" and options:
        count = rng.randint(1, min(3, len(options)))
        answer = ",".join(rng.sample(options, count))
    elif item_type in {"number", "integer"}:
        answer = str(rng.randint(18, 30))
    elif item_type in {"input", "text", "textarea"}:
        answer = generate_text_answer(rng, item_id)
    else:
        answer = f"ans-{random_word(rng, 4, 8)}"

    default_weight = float(item.get("weight") or 1)
    if default_weight < 0:
        weight = default_weight
    else:
        weight = float(rng.randint(1, 20))
    return answer, weight


def generate_text_answer(rng: random.Random, item_id: str) -> str:
    profiles = {
        "q_mvp_self_intro": [
            "我是比较慢热但好相处的人，平时喜欢按计划完成学习任务，也会给自己留休息时间。",
            "我性格外向，喜欢和朋友聊天，专业课之外会参加社团和运动。",
            "我比较独立，喜欢安静稳定的生活节奏，遇到公共事务会主动沟通。",
            "我平时状态比较松弛，喜欢把宿舍当成恢复精力的地方，也愿意互相照应。",
        ],
        "q_mvp_interests": [
            "喜欢音乐、电影、阅读和散步，偶尔会拍照记录生活。",
            "喜欢游戏、篮球、健身和看比赛，周末经常运动。",
            "喜欢编程、科幻、桌游和动漫，空闲时会折腾小项目。",
            "喜欢旅行、美食、摄影和逛展，也喜欢和朋友一起探索城市。",
        ],
        "q_mvp_dorm_atmosphere": [
            "希望宿舍整体安静温暖，大家互相尊重边界，有事及时沟通。",
            "希望宿舍像朋友一样相处，可以一起吃饭聊天，但也保留个人空间。",
            "希望宿舍适合学习和休息，公共区域保持清爽，晚上尽量降低噪声。",
            "希望氛围轻松一点，大家可以分享日常，也能接受彼此不同节奏。",
        ],
        "q_mvp_representative_thing": [
            "一本随身笔记本，代表我喜欢记录想法和慢慢整理生活。",
            "一副耳机，代表我需要音乐陪伴，也重视自己的安静空间。",
            "一双跑鞋，代表我喜欢保持行动感，也愿意尝试新事情。",
            "一款合作游戏，代表我喜欢团队配合和轻松交流。",
        ],
        "q_mvp_roommate_note": [
            "我睡前比较需要安静，如果要语音或外放，希望能提前说一声。",
            "我对公共区域卫生比较在意，外卖盒和垃圾希望当天处理。",
            "我偶尔会晚归或赶作业，但会尽量控制声音和灯光。",
            "我欢迎直接沟通，不太喜欢把小问题憋很久，大家商量着来最好。",
        ],
    }
    choices = profiles.get(item_id)
    if not choices:
        return f"我希望宿舍生活稳定、舒服，也愿意和舍友互相配合。{random_word(rng, 4, 8)}"
    return rng.choice(choices)


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack("!I", len(data)) + chunk_type + data + struct.pack(
        "!I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    )


def create_color_png(path: Path, r: int, g: int, b: int, width: int = 64, height: int = 64) -> None:
    # tiny valid RGB PNG generated with stdlib only
    raw_rows = []
    row = bytes([r, g, b]) * width
    for _ in range(height):
        raw_rows.append(b"\x00" + row)  # filter type 0
    raw = b"".join(raw_rows)
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def seed_data(cur, rng: random.Random, admin_email: str, admin_password: str, student_password: str, api_root: Path, student_count: int):
    # settings needed by team logic
    upsert_system_setting(cur, "team_max_student_count", "4")

    # admin
    cur.execute(
        "INSERT INTO admins(username,email,password,last_logged_at) VALUES(%s,%s,%s,NULL)",
        ("admin", admin_email, hash_password(admin_password)),
    )

    # load questionnaire items
    cur.execute(
        """
        SELECT id, title, weight, data_type, params, `index`, `type`, page_id, index_in_page
        FROM questionnaire_items
        ORDER BY `index` ASC
        """
    )
    items = cur.fetchall()
    if not items:
        raise RuntimeError("No questionnaire_items found after initialization.")

    # build page->items map for page_answer table
    # create teams for each gender
    team_ids: dict[str, int] = {}
    for gender, gname in [(1, "male"), (2, "female")]:
        cur.execute(
            "INSERT INTO teams(gender, description) VALUES(%s,%s)",
            (gender, f"{gname}-full-team"),
        )
        team_ids[f"{gname}_full"] = int(cur.lastrowid)

        cur.execute(
            "INSERT INTO teams(gender, description) VALUES(%s,%s)",
            (gender, f"{gname}-half-team"),
        )
        team_ids[f"{gname}_half"] = int(cur.lastrowid)

    if student_count < 2 or student_count % 2 != 0:
        raise ValueError("--student-count 必须是大于等于 2 的偶数")

    # generate student IDs and profiles
    candidate_ids = rng.sample(range(20260001, 20269999), student_count)
    per_gender_count = student_count // 2
    male_ids = candidate_ids[:per_gender_count]
    female_ids = candidate_ids[per_gender_count:]

    avatar_dir = api_root / "static" / "uploads" / "student_avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    # 避免上次 seed 留下的 png 让满员学生仍显示头像
    for p in avatar_dir.glob("*.png"):
        try:
            p.unlink()
        except OSError:
            pass

    all_students: list[dict[str, Any]] = []
    all_answers: list[tuple[str, str, int, float]] = []
    all_page_answers: list[tuple[int, int, str]] = []
    account_rows: list[dict[str, Any]] = [
        {
            "role": "admin",
            "login_account": admin_email,
            "password": admin_password,
            "name": "admin",
            "gender": "",
            "team_id": "",
            "team_status": "",
        }
    ]

    for gender, sid_list in [(1, male_ids), (2, female_ids)]:
        gname = "male" if gender == 1 else "female"
        for idx, sid in enumerate(sid_list):
            if idx < 4:
                team_id = team_ids[f"{gname}_full"]
                team_status = "full_team"
            elif idx < 6:
                team_id = team_ids[f"{gname}_half"]
                team_status = "half_team"
            else:
                team_id = None
                team_status = "no_team"

            profile = {
                "id": int(sid),
                "team_id": team_id,
                "gender": gender,
                "contact": random_contact(rng),
                "password": hash_password(student_password),
                "name": random_name(rng, gender, idx + 1),
                "qq": str(rng.randint(100000, 999999999)),
                "wechat": f"wx_{random_word(rng, 6, 10)}",
                "mbti": rng.choice(MBTI_TYPES),
                "province": rng.choice(PROVINCES),
            }
            all_students.append(profile)
            account_rows.append(
                {
                    "role": "student",
                    "login_account": str(sid),
                    "password": student_password,
                    "name": profile["name"],
                    "gender": "male" if gender == 1 else "female",
                    "team_id": str(team_id) if team_id is not None else "",
                    "team_status": team_status,
                }
            )

            # generate per-item answers
            page_payloads: dict[int, dict[str, dict[str, Any]]] = {}
            for item in items:
                ans, weight = generate_answer_and_weight(rng, item)
                all_answers.append((item["id"], ans, int(sid), float(weight)))
                pid = item.get("page_id")
                if pid is not None:
                    page_payloads.setdefault(int(pid), {})
                    page_payloads[int(pid)][item["id"]] = {"answer": ans, "weight": float(weight)}

            for pid, payload in page_payloads.items():
                all_page_answers.append((int(sid), pid, json.dumps(payload, ensure_ascii=False)))

            # 组队已满员（每性别 4 人一队）的学生不生成随机头像
            if team_status != "full_team":
                r = rng.randint(30, 220)
                g = rng.randint(30, 220)
                b = rng.randint(30, 220)
                create_color_png(avatar_dir / f"{sid}.png", r, g, b)

    # insert students
    for st in all_students:
        cur.execute(
            """
            INSERT INTO students(id, team_id, gender, contact, password, name, qq, wechat, mbti, province)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                st["id"],
                st["team_id"],
                st["gender"],
                st["contact"],
                st["password"],
                st["name"],
                st["qq"],
                st["wechat"],
                st["mbti"],
                st["province"],
            ),
        )

    # insert answers
    for item_id, ans, sid, weight in all_answers:
        cur.execute(
            """
            INSERT INTO questionnaire_answers(item_id, answer, student_id, weight, vector)
            VALUES(%s,%s,%s,%s,NULL)
            """,
            (item_id, ans, sid, weight),
        )

    # insert page answers
    for sid, pid, ans_json in all_page_answers:
        cur.execute(
            """
            INSERT INTO questionnaire_page_answers(student_id, page_id, answers_json, status)
            VALUES(%s,%s,%s,1)
            """,
            (sid, pid, ans_json),
        )

    return {
        "admin_email": admin_email,
        "admin_password": admin_password,
        "student_password": student_password,
        "male_ids": male_ids,
        "female_ids": female_ids,
        "accounts": account_rows,
    }


def write_accounts_csv(csv_path: Path, rows: list[dict[str, Any]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["role", "login_account", "password", "name", "gender", "team_id", "team_status"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_summary(cur, summary: dict[str, Any]) -> None:
    cur.execute("SELECT COUNT(*) AS c FROM admins")
    admin_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM students")
    student_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM questionnaire_items")
    question_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM questionnaire_answers")
    answer_count = int(cur.fetchone()["c"])
    cur.execute("SELECT COUNT(*) AS c FROM teams")
    team_count = int(cur.fetchone()["c"])

    cur.execute(
        """
        SELECT gender, team_id, COUNT(*) AS c
        FROM students
        GROUP BY gender, team_id
        ORDER BY gender, team_id
        """
    )
    dist = cur.fetchall()

    print("Seed complete.")
    print(f"- admins: {admin_count}")
    print(f"- students: {student_count}")
    print(f"- questionnaire_items: {question_count}")
    print(f"- questionnaire_answers: {answer_count}")
    print(f"- teams: {team_count}")
    print("- team distribution (gender, team_id, count):")
    for row in dist:
        print(f"  - {row['gender']}, {row['team_id']}, {row['c']}")
    print("")
    print("Login accounts:")
    print(f"- admin email: {summary['admin_email']}")
    print(f"- admin password: {summary['admin_password']}")
    print(f"- student password (all): {summary['student_password']}")
    print(f"- sample male ids: {summary['male_ids'][:3]}")
    print(f"- sample female ids: {summary['female_ids'][:3]}")
    print("- accounts csv: RMMT-API/test-data/accounts.csv")


def main() -> int:
    args = parse_args()
    cfg = DbConfig(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
    )
    rng = random.Random(args.seed)

    script_dir = Path(__file__).resolve().parent
    api_root = script_dir.parent

    conn = db_connect(cfg)
    try:
        with conn.cursor() as cur:
            ensure_schema(cur)
            ensure_questionnaire(cur)
            reset_business_data(cur)
            summary = seed_data(
                cur=cur,
                rng=rng,
                admin_email=args.admin_email,
                admin_password=args.admin_password,
                student_password=args.student_password,
                api_root=api_root,
                student_count=args.student_count,
            )
            conn.commit()
            write_accounts_csv(script_dir / "accounts.csv", summary["accounts"])
            print_summary(cur, summary)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
