"""collect_comments.py의 결과를 Supabase PostgreSQL에 저장한다."""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / (".env" if (BASE_DIR / ".env").exists() else "keys.env")
SETUP_SQL_PATH = BASE_DIR / "setup_tables.sql"
REQUIRED_COLUMNS = {
    "video_id",
    "comment_id",
    "parent_id",
    "comment_type",
    "author_display_name",
    "author_channel_id",
    "text_raw",
    "published_at",
    "updated_at",
    "like_count",
    "reply_count",
    "collected_at",
}


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _build_connection_uri(uri_template: str, password: str) -> str:
    """Supabase URI의 비밀번호 자리에 URL 인코딩한 비밀번호를 넣는다."""

    encoded = quote(password, safe="")
    placeholders = ("[YOUR-PASSWORD]", "<YOUR-PASSWORD>", "{password}")
    for placeholder in placeholders:
        if placeholder in uri_template:
            return uri_template.replace(placeholder, encoded)

    parsed = urlsplit(uri_template)
    if not parsed.username or not parsed.hostname:
        raise ValueError("Supabase Connect 화면에서 복사한 PostgreSQL URI가 아닙니다.")

    username = quote(parsed.username, safe=".")
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{username}:{encoded}@{parsed.hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def configure_connection() -> None:
    """Session pooler URI와 DB 비밀번호를 받아 .env에 안전하게 저장한다."""

    from dotenv import set_key

    print("Supabase > Connect > Session pooler > URI 값을 붙여넣으세요.")
    uri_template = input("연결 URI: ").strip()
    password = getpass.getpass("Database password (화면에 표시되지 않음): ")
    connection_uri = _build_connection_uri(uri_template, password)
    parsed = urlsplit(connection_uri)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise ValueError("올바른 PostgreSQL 연결 URI가 아닙니다.")

    # 키와 연결 문자열은 출력하지 않고 .env에만 기록한다.
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "SUPABASE_CONNECTION_STRING", connection_uri)
    print(f"연결 정보를 {ENV_PATH.name}에 저장했습니다.")


def connect_db() -> Any:
    """.env의 연결 문자열을 사용해 암호화된 PostgreSQL 연결을 연다."""

    from dotenv import load_dotenv
    import psycopg2

    load_dotenv(ENV_PATH)
    connection_string = os.getenv("SUPABASE_CONNECTION_STRING", "").strip()
    if not connection_string:
        raise RuntimeError(
            ".env에 SUPABASE_CONNECTION_STRING이 없습니다. "
            "먼저 python upload_supabase.py --configure 를 실행하세요."
        )

    return psycopg2.connect(
        connection_string,
        sslmode="require",
        connect_timeout=10,
    )


def check_db() -> None:
    """DB 연결이 되는지 확인하되 비밀번호와 호스트는 출력하지 않는다."""

    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user")
            database, user = cursor.fetchone()
        print("DB 연결 성공")
        print(f"database: {database}")
        print(f"user: {user}")
    finally:
        conn.close()


def init_db() -> None:
    """setup_tables.sql을 실행해 실습용 테이블과 인덱스를 만든다."""

    sql = SETUP_SQL_PATH.read_text(encoding="utf-8")
    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
        conn.commit()
        print("lab_videos와 lab_comments 테이블 준비 완료")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _parse_nonnegative_int(value: str, column: str, row_number: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CSV {row_number}행의 {column} 값이 정수가 아닙니다.") from exc
    if number < 0:
        raise ValueError(f"CSV {row_number}행의 {column} 값이 음수입니다.")
    return number


def read_batch(folder: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """수집 폴더의 CSV와 로그를 읽고 업로드 전에 구조를 검사한다."""

    csv_path = folder / "comments.csv"
    log_path = folder / "collection_log.json"
    if not csv_path.is_file() or not log_path.is_file():
        raise FileNotFoundError(
            "선택한 폴더에 comments.csv와 collection_log.json이 모두 필요합니다."
        )

    metadata = json.loads(log_path.read_text(encoding="utf-8"))
    video_id = metadata.get("video", {}).get("video_id")
    if not video_id:
        raise ValueError("collection_log.json에 video.video_id가 없습니다.")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV에 필요한 열이 없습니다: {sorted(missing)}")

        for row_number, row in enumerate(reader, start=2):
            if row["video_id"] != video_id:
                raise ValueError(
                    f"CSV {row_number}행의 video_id가 로그의 video_id와 다릅니다."
                )
            comment_id = row["comment_id"].strip()
            if not comment_id:
                raise ValueError(f"CSV {row_number}행의 comment_id가 비어 있습니다.")
            if comment_id in seen:
                # 수집 코드에서도 막지만, 사용자가 CSV를 수정했을 때도 DB 전에 막는다.
                continue
            seen.add(comment_id)
            row["comment_id"] = comment_id
            if row["comment_type"] not in {"top_level", "reply"}:
                raise ValueError(
                    f"CSV {row_number}행의 comment_type이 올바르지 않습니다."
                )
            if row["comment_type"] == "reply" and not row["parent_id"].strip():
                raise ValueError(f"CSV {row_number}행 답글의 parent_id가 비어 있습니다.")
            row["like_count"] = _parse_nonnegative_int(
                row["like_count"], "like_count", row_number
            )
            row["reply_count"] = _parse_nonnegative_int(
                row["reply_count"], "reply_count", row_number
            )
            rows.append(row)

    return metadata, rows


def insert_batch(
    conn: Any, metadata: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, int]:
    """영상 1건을 먼저 저장한 뒤 댓글을 comment_id 기준으로 중복 없이 저장한다."""

    from psycopg2.extras import execute_values

    video = metadata["video"]
    collection = metadata["collection"]
    is_demo = metadata.get("mode") != "LIVE"

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.lab_videos (
                    video_id, title, channel_title, video_published_at,
                    video_url, api_reported_comment_count, is_demo, first_collected_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id) DO NOTHING
                """,
                (
                    video["video_id"],
                    video.get("title"),
                    video.get("channel_title"),
                    video.get("published_at"),
                    video.get("url"),
                    video.get("api_reported_comment_count"),
                    is_demo,
                    collection["collected_at"],
                ),
            )

            values = [
                (
                    row["comment_id"],
                    row["video_id"],
                    row["parent_id"].strip() or None,
                    row["comment_type"],
                    row["author_display_name"],
                    row["author_channel_id"].strip() or None,
                    row["text_raw"],
                    row["published_at"],
                    row["updated_at"] or None,
                    row["like_count"],
                    row["reply_count"],
                    row["collected_at"],
                )
                for row in rows
            ]
            inserted_ids: list[tuple[str]] = []
            if values:
                inserted_ids = execute_values(
                    cursor,
                    """
                    INSERT INTO public.lab_comments (
                        comment_id, video_id, parent_id, comment_type,
                        author_display_name, author_channel_id, text_raw,
                        published_at, updated_at, like_count, reply_count, collected_at
                    ) VALUES %s
                    ON CONFLICT (comment_id) DO NOTHING
                    RETURNING comment_id
                    """,
                    values,
                    page_size=100,
                    fetch=True,
                )

            cursor.execute(
                """
                SELECT
                    count(*),
                    count(*) FILTER (WHERE comment_type = 'top_level'),
                    count(*) FILTER (WHERE comment_type = 'reply')
                FROM public.lab_comments
                WHERE video_id = %s
                """,
                (video["video_id"],),
            )
            total_for_video, total_top_level, total_replies = map(
                int, cursor.fetchone()
            )

        conn.commit()
    except Exception:
        # 영상과 댓글 중 하나라도 실패하면 이번 묶음 전체를 취소한다.
        conn.rollback()
        raise

    inserted = len(inserted_ids)
    return {
        "csv_rows": len(rows),
        "inserted_comments": inserted,
        "duplicates_skipped_by_db": len(rows) - inserted,
        "db_comments_for_video": total_for_video,
        "db_top_level_comments_for_video": total_top_level,
        "db_replies_for_video": total_replies,
    }


def save_db_result(folder: Path, metadata: dict[str, Any], result: dict[str, int]) -> Path:
    """보고서에 사용할 DB 건수 조회 결과를 JSON 파일로 남긴다."""

    output = {
        "checked_at": utc_now_text(),
        "video_id": metadata["video"]["video_id"],
        **result,
        "duplicate_rule": "PRIMARY KEY(comment_id) + ON CONFLICT DO NOTHING",
    }
    path = folder / "db_upload_result.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def upload_folder(folder: Path) -> None:
    metadata, rows = read_batch(folder)
    conn = connect_db()
    try:
        result = insert_batch(conn, metadata, rows)
    finally:
        conn.close()

    result_path = save_db_result(folder, metadata, result)
    print(f"CSV 행 수: {result['csv_rows']}")
    print(f"새로 저장: {result['inserted_comments']}")
    print(f"DB에서 중복 제외: {result['duplicates_skipped_by_db']}")
    print(f"이 영상의 DB 댓글 총수: {result['db_comments_for_video']}")
    print(
        f"DB 구성: 최상위 {result['db_top_level_comments_for_video']}개 + "
        f"답글 {result['db_replies_for_video']}개"
    )
    print(f"DB 건수 결과 저장: {result_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="수집한 댓글을 Supabase에 저장합니다.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--configure", action="store_true", help="DB 연결 URI를 .env에 저장"
    )
    actions.add_argument(
        "--check-db", action="store_true", help="DB 연결만 확인"
    )
    actions.add_argument(
        "--init-db", action="store_true", help="실습용 테이블 생성"
    )
    actions.add_argument(
        "--folder", type=Path, help="comments.csv와 collection_log.json이 있는 폴더"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.configure:
            configure_connection()
        elif args.check_db:
            check_db()
        elif args.init_db:
            init_db()
        else:
            upload_folder(args.folder.resolve())
    except Exception as exc:
        # 연결 문자열 자체는 출력하지 않는다. psycopg2 오류도 한 줄만 보여 준다.
        print(f"오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
