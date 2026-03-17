import datetime

from sqlalchemy import text

from database import db_session, engine
from models import Base


def table_exists(table_name: str) -> bool:
    q = text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"
    )
    return (db_session.execute(q, {"t": table_name}).scalar() or 0) > 0


def column_exists(table_name: str, column_name: str) -> bool:
    q = text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
    )
    return (db_session.execute(q, {"t": table_name, "c": column_name}).scalar() or 0) > 0


def ensure_questionnaire_pages_table():
    # create_all will create new tables but won't alter existing ones
    Base.metadata.create_all(engine)


def ensure_questionnaire_items_page_columns():
    if not column_exists("questionnaire_items", "page_id"):
        db_session.execute(text("ALTER TABLE questionnaire_items ADD COLUMN page_id INT NULL"))
        db_session.execute(text("ALTER TABLE questionnaire_items ADD INDEX ix_questionnaire_items_page_id (page_id)"))
    if not column_exists("questionnaire_items", "index_in_page"):
        db_session.execute(text("ALTER TABLE questionnaire_items ADD COLUMN index_in_page INT NULL DEFAULT 1"))

    # FK (optional; MySQL may reject if existing data incompatible)
    # We'll only add FK if not present and the pages table exists.
    # If it fails, the app can still function without the FK constraint.
    try:
        db_session.execute(
            text(
                "ALTER TABLE questionnaire_items "
                "ADD CONSTRAINT fk_questionnaire_items_page_id "
                "FOREIGN KEY (page_id) REFERENCES questionnaire_pages(id) "
                "ON UPDATE CASCADE ON DELETE RESTRICT"
            )
        )
    except Exception:
        db_session.rollback()


def ensure_questionnaire_page_answers_table():
    if table_exists("questionnaire_page_answers"):
        return
    db_session.execute(
        text(
            "CREATE TABLE questionnaire_page_answers ("
            "id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
            "student_id BIGINT NOT NULL,"
            "page_id INT NOT NULL,"
            "answers_json LONGTEXT,"
            "status TINYINT DEFAULT 0,"
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
            "INDEX ix_qpa_student_id (student_id),"
            "INDEX ix_qpa_page_id (page_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
    )
    try:
        db_session.execute(
            text(
                "ALTER TABLE questionnaire_page_answers "
                "ADD CONSTRAINT fk_qpa_student_id FOREIGN KEY (student_id) REFERENCES students(id) "
                "ON UPDATE CASCADE ON DELETE CASCADE"
            )
        )
        db_session.execute(
            text(
                "ALTER TABLE questionnaire_page_answers "
                "ADD CONSTRAINT fk_qpa_page_id FOREIGN KEY (page_id) REFERENCES questionnaire_pages(id) "
                "ON UPDATE CASCADE ON DELETE CASCADE"
            )
        )
    except Exception:
        db_session.rollback()


def ensure_default_page_and_backfill():
    # Ensure at least one page exists
    page_id = db_session.execute(text("SELECT id FROM questionnaire_pages ORDER BY `index` ASC LIMIT 1")).scalar()
    if page_id is None:
        db_session.execute(
            text("INSERT INTO questionnaire_pages (title, remark, `index`, created_at, updated_at) VALUES (:t, :r, 1, NOW(), NOW())"),
            {"t": "默认", "r": ""},
        )
        db_session.commit()
        page_id = db_session.execute(text("SELECT id FROM questionnaire_pages ORDER BY `index` ASC LIMIT 1")).scalar()

    # Backfill items
    db_session.execute(
        text(
            "UPDATE questionnaire_items "
            "SET page_id = :pid "
            "WHERE page_id IS NULL"
        ),
        {"pid": page_id},
    )
    db_session.execute(
        text(
            "UPDATE questionnaire_items "
            "SET index_in_page = COALESCE(index_in_page, `index`)"
        )
    )


def migrate():
    ensure_questionnaire_pages_table()
    ensure_questionnaire_items_page_columns()
    ensure_questionnaire_page_answers_table()
    ensure_default_page_and_backfill()
    db_session.commit()
    print("[OK] questionnaire paging migration completed at", datetime.datetime.now())


if __name__ == "__main__":
    migrate()

