#!/usr/bin/env python3
"""
Seed local RMMT test data.

What this script does:
1) Checks whether questionnaire items already exist.
2) If no questionnaire exists, creates 3 pages and 9 test questions (3 per page).
3) Resets business data (admins/students/teams/answers and related tables).
4) Inserts:
   - 1 admin account
   - 20 student accounts (10 male, 10 female) with random profile data
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

QUESTION_BANK = [
    {
        "title": "What is your usual sleep schedule?",
        "weight": 10,
        "data_type": "string",
        "params": {"options": ["Before 23:00", "23:00-01:00", "After 01:00"]},
        "type": "select",
    },
    {
        "title": "How much do you care about room cleanliness?",
        "weight": 12,
        "data_type": "string",
        "params": {"options": ["Very much", "Normal", "Not much"]},
        "type": "radio",
    },
    {
        "title": "How sensitive are you to noise?",
        "weight": 9,
        "data_type": "string",
        "params": {"options": ["Sensitive", "Medium", "Not sensitive"]},
        "type": "select",
    },
    {
        "title": "Your usual weekend style",
        "weight": 8,
        "data_type": "string",
        "params": {"options": ["Study", "Workout", "Gaming", "Outdoors", "Movies"]},
        "type": "checkbox",
    },
    {
        "title": "Preferred room temperature (C)",
        "weight": 7,
        "data_type": "integer",
        "params": {"placeholder": "e.g. 24"},
        "type": "number",
    },
    {
        "title": "How often do you communicate with roommates?",
        "weight": 6,
        "data_type": "string",
        "params": {"options": ["Often", "Sometimes", "Rarely"]},
        "type": "select",
    },
    {
        "title": "When do you usually turn off lights?",
        "weight": 7,
        "data_type": "string",
        "params": {"options": ["Before 22:30", "22:30-00:00", "After 00:00"]},
        "type": "select",
    },
    {
        "title": "Do you accept visitors in dorm room?",
        "weight": 5,
        "data_type": "string",
        "params": {"options": ["Yes", "Only weekends", "No"]},
        "type": "radio",
    },
    {
        "title": "How do you handle shared expenses?",
        "weight": 8,
        "data_type": "string",
        "params": {"options": ["AA strictly", "Flexible AA", "One pays first"]},
        "type": "select",
    },
    {
        "title": "Preferred study environment",
        "weight": 9,
        "data_type": "string",
        "params": {"options": ["Quiet", "Background sound", "Any"]},
        "type": "radio",
    },
    {
        "title": "Hobbies you want roommates to share",
        "weight": 4,
        "data_type": "string",
        "params": {"options": ["Sports", "Music", "Coding", "Anime", "Travel"]},
        "type": "checkbox",
    },
    {
        "title": "Any allergies or special notes?",
        "weight": 5,
        "data_type": "string",
        "params": {"placeholder": "Optional short note"},
        "type": "input",
    },
]

QUESTIONNAIRE_PAGES = [
    ("生活作息", "作息、清洁与日常习惯相关问题"),
    ("学习社交", "学习与社交方式相关问题"),
    ("宿舍偏好", "住宿偏好与补充信息"),
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


def ensure_questionnaire(cur, rng: random.Random) -> None:
    cur.execute("SELECT COUNT(*) AS cnt FROM questionnaire_items")
    count = int(cur.fetchone()["cnt"])
    if count > 0:
        # ensure at least one page exists and map old items to first page
        cur.execute("SELECT id FROM questionnaire_pages ORDER BY `index` ASC LIMIT 1")
        first_page = cur.fetchone()
        if not first_page:
            cur.execute(
                "INSERT INTO questionnaire_pages(title, remark, `index`) VALUES(%s,%s,%s)",
                ("Default Page", "Auto-created for legacy questionnaire items", 1),
            )
            page_id = int(cur.lastrowid)
        else:
            page_id = int(first_page["id"])

        cur.execute("UPDATE questionnaire_items SET page_id=%s WHERE page_id IS NULL", (page_id,))
        cur.execute("UPDATE questionnaire_items SET index_in_page=`index` WHERE index_in_page IS NULL OR index_in_page=0")
        return

    # no questionnaire found -> create 3 pages and 9 generated questions (3 per page)
    page_ids: list[int] = []
    for pidx, (title, remark) in enumerate(QUESTIONNAIRE_PAGES, start=1):
        cur.execute(
            "INSERT INTO questionnaire_pages(title, remark, `index`) VALUES(%s,%s,%s)",
            (title, remark, pidx),
        )
        page_ids.append(int(cur.lastrowid))

    chosen = rng.sample(QUESTION_BANK, 9)
    for idx, item in enumerate(chosen, start=1):
        page_idx = (idx - 1) // 3
        index_in_page = ((idx - 1) % 3) + 1
        item_id = f"q_auto_{idx:02d}"
        cur.execute(
            """
            INSERT INTO questionnaire_items(
                id, title, weight, data_type, params, `index`, `type`, page_id, index_in_page
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                item_id,
                item["title"],
                item["weight"],
                item["data_type"],
                json.dumps(item["params"], ensure_ascii=False),
                idx,
                item["type"],
                page_ids[page_idx],
                index_in_page,
            ),
        )


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

    if item_type in {"select", "radio"} and options:
        answer = rng.choice(options)
    elif item_type == "checkbox" and options:
        count = rng.randint(1, min(3, len(options)))
        answer = ",".join(rng.sample(options, count))
    elif item_type in {"number", "integer"}:
        answer = str(rng.randint(18, 30))
    elif item_type in {"input", "text", "textarea"}:
        answer = f"pref-{random_word(rng, 4, 8)}"
    else:
        answer = f"ans-{random_word(rng, 4, 8)}"

    default_weight = float(item.get("weight") or 1)
    if default_weight < 0:
        weight = default_weight
    else:
        weight = float(rng.randint(1, 20))
    return answer, weight


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


def seed_data(cur, rng: random.Random, admin_email: str, admin_password: str, student_password: str, api_root: Path):
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

    # generate student IDs and profiles
    candidate_ids = rng.sample(range(20260001, 20269999), 20)
    male_ids = candidate_ids[:10]
    female_ids = candidate_ids[10:]

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
            ensure_questionnaire(cur, rng)
            reset_business_data(cur)
            summary = seed_data(
                cur=cur,
                rng=rng,
                admin_email=args.admin_email,
                admin_password=args.admin_password,
                student_password=args.student_password,
                api_root=api_root,
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
