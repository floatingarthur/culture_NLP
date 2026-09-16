"""YouTube 영상 한 편의 공개 댓글과 답글을 CSV/JSON으로 저장한다.

예시 저장소처럼 main()이 전체 순서를 관리하고 영상 조회, 댓글 수집, 저장을
함수로 분리했다. 음성ㆍ자막ㆍ재생목록은 수집하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / (".env" if (BASE_DIR / ".env").exists() else "keys.env")
DEFAULT_VIDEO_URL = "https://youtu.be/S1bcy4h1mS8"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"
DEMO_FILE = BASE_DIR / "demo_pages.json"

COLUMNS = [
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
]


class QuotaBudgetExceeded(RuntimeError):
    """이 실행에서 허용한 API 요청 예산을 모두 썼을 때 발생한다."""


@dataclass
class QuotaTracker:
    """실패한 요청도 quota를 쓸 수 있으므로 execute() 직전에 횟수를 센다."""

    limit: int
    used: int = 0

    def execute(self, request: Any) -> dict[str, Any]:
        if self.limit > 0 and self.used >= self.limit:
            raise QuotaBudgetExceeded(
                f"설정한 API 요청 예산 {self.limit}회를 모두 사용했습니다."
            )
        self.used += 1
        return request.execute()

    @property
    def remaining(self) -> int | None:
        return None if self.limit == 0 else self.limit - self.used


def utc_now_text() -> str:
    """현재 UTC 시각을 ISO 8601 문자열로 반환한다."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_video_id(value: str) -> str:
    """YouTube 영상 URL 또는 11자리 영상 ID에서 video_id를 꺼낸다."""

    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value

    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            parts = parsed.path.strip("/").split("/")
            candidate = parts[1] if len(parts) > 1 else ""
        else:
            candidate = ""
    else:
        candidate = ""

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
        raise ValueError(f"올바른 YouTube 영상 URL 또는 video_id가 아닙니다: {value}")
    return candidate


def make_youtube_client() -> Any:
    """.env의 Google API 키로 YouTube Data API 요청 객체를 만든다."""

    # --demo 실행은 이 패키지들이 없어도 가능하도록 여기에서만 불러온다.
    from dotenv import load_dotenv
    from googleapiclient.discovery import build
    import os

    load_dotenv(ENV_PATH)
    api_key = os.getenv("google_cloud_api_key", "").strip()
    if not api_key:
        raise RuntimeError(
            f"{ENV_PATH.name}에 google_cloud_api_key가 없습니다. "
            ".env.example을 참고해 키를 저장하세요."
        )
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def get_video_info(
    youtube: Any, video_id: str, quota_tracker: QuotaTracker
) -> dict[str, Any]:
    """영상 정보와 API가 보고하는 전체 댓글 수를 조회한다."""

    request = youtube.videos().list(part="snippet,statistics", id=video_id)
    # execute()를 호출하는 순간 실제 HTTP 요청이 Google 서버로 전송된다.
    response = quota_tracker.execute(request)
    items = response.get("items", [])
    if not items:
        raise RuntimeError(
            "영상을 찾지 못했습니다. 비공개/삭제 영상이거나 ID가 잘못되었을 수 있습니다."
        )

    snippet = items[0]["snippet"]
    statistics = items[0].get("statistics", {})
    api_count = statistics.get("commentCount")
    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "published_at": snippet.get("publishedAt"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        # 수집 행 수와 비교할 기준이다. 숨김/삭제/검토 댓글 때문에 다를 수 있다.
        "api_reported_comment_count": int(api_count) if api_count is not None else None,
    }


def parse_comment_resource(
    comment: dict[str, Any],
    video_id: str,
    collected_at: str,
    *,
    parent_id: str | None,
    reply_count: int,
) -> dict[str, Any]:
    """comment 리소스 하나를 CSV 한 행으로 바꾼다."""

    snippet = comment["snippet"]
    author_channel = snippet.get("authorChannelId") or {}
    return {
        "video_id": video_id,
        # comment_id는 모든 댓글/답글의 고유 ID로, 중복 제거와 DB 기본키에 쓴다.
        "comment_id": comment["id"],
        # 최상위 댓글은 빈 값, 답글은 자신이 속한 최상위 댓글 ID가 들어간다.
        "parent_id": parent_id or "",
        "comment_type": "reply" if parent_id else "top_level",
        "author_display_name": snippet.get("authorDisplayName", ""),
        "author_channel_id": author_channel.get("value", ""),
        "text_raw": snippet.get("textDisplay", ""),
        "published_at": snippet.get("publishedAt"),
        "updated_at": snippet.get("updatedAt"),
        "like_count": int(snippet.get("likeCount", 0)),
        # 최상위 댓글에는 답글 수, 답글 행에는 0을 기록한다.
        "reply_count": int(reply_count),
        "collected_at": collected_at,
    }


def parse_top_level_thread(
    item: dict[str, Any], video_id: str, collected_at: str
) -> dict[str, Any]:
    """commentThread에서 최상위 댓글 한 행을 만든다."""

    thread_snippet = item["snippet"]
    return parse_comment_resource(
        thread_snippet["topLevelComment"],
        video_id,
        collected_at,
        parent_id=None,
        reply_count=int(thread_snippet.get("totalReplyCount", 0)),
    )


def _append_if_new(
    row: dict[str, Any], seen_comment_ids: set[str], rows: list[dict[str, Any]]
) -> bool:
    """처음 보는 comment_id만 추가한다. 중복이면 False를 반환한다."""

    comment_id = row["comment_id"]
    if comment_id in seen_comment_ids:
        return False
    seen_comment_ids.add(comment_id)
    rows.append(row)
    return True


def _safe_api_error(exc: Exception) -> str:
    """API 키가 오류 메시지에 노출되지 않도록 안전한 요약만 만든다."""

    status = getattr(getattr(exc, "resp", None), "status", None)
    content = getattr(exc, "content", b"")
    reason = None
    try:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        payload = json.loads(content) if content else {}
        reason = payload.get("error", {}).get("message")
    except (TypeError, ValueError, AttributeError):
        pass

    parts = [type(exc).__name__]
    if status is not None:
        parts.append(f"HTTP {status}")
    if reason:
        parts.append(str(reason))
    return ": ".join(parts)


def collect_replies(
    youtube: Any,
    video_id: str,
    parent_id: str,
    collected_at: str,
    rows: list[dict[str, Any]],
    seen_comment_ids: set[str],
    max_reply_pages: int,
    quota_tracker: QuotaTracker,
) -> dict[str, Any]:
    """한 최상위 댓글의 모든 공개 답글을 nextPageToken 끝까지 조회한다."""

    page_token: str | None = None
    seen_page_tokens: set[str] = set()
    pages = 0
    replies = 0
    duplicates = 0
    error = None
    limited = False
    budget_exhausted = False

    while True:
        if max_reply_pages > 0 and pages >= max_reply_pages:
            limited = bool(page_token)
            break
        try:
            request = youtube.comments().list(
                part="snippet",
                parentId=parent_id,
                maxResults=100,
                textFormat="plainText",
                pageToken=page_token,
            )
            response = quota_tracker.execute(request)
        except QuotaBudgetExceeded as exc:
            error = str(exc)
            budget_exhausted = True
            break
        except Exception as exc:
            error = _safe_api_error(exc)
            break

        pages += 1
        for comment in response.get("items", []):
            row = parse_comment_resource(
                comment,
                video_id,
                collected_at,
                parent_id=comment.get("snippet", {}).get("parentId") or parent_id,
                reply_count=0,
            )
            if _append_if_new(row, seen_comment_ids, rows):
                replies += 1
            else:
                duplicates += 1

        next_token = response.get("nextPageToken")
        if not next_token:
            break
        if next_token in seen_page_tokens:
            error = "RepeatedPageToken: 답글 페이지 토큰이 반복되어 중단함"
            break
        seen_page_tokens.add(next_token)
        page_token = next_token

    return {
        "pages": pages,
        "replies": replies,
        "duplicates": duplicates,
        "error": error,
        "limited": limited,
        "budget_exhausted": budget_exhausted,
    }


def collect_comments(
    youtube: Any,
    video_id: str,
    collected_at: str,
    *,
    max_thread_pages: int = 0,
    max_reply_pages: int = 0,
    quota_tracker: QuotaTracker,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """영상의 공개 최상위 댓글과 답글을 가능한 범위에서 전부 수집한다.

    max_*_pages가 0이면 제한 없이 nextPageToken이 사라질 때까지 반복한다.
    commentThreads 응답의 replies는 일부뿐일 수 있으므로 사용하지 않고, 답글이
    있는 각 parent_id에 comments.list를 별도로 호출한다.
    """

    rows: list[dict[str, Any]] = []
    seen_comment_ids: set[str] = set()
    seen_thread_tokens: set[str] = set()
    thread_page_token: str | None = None
    thread_pages = 0
    reply_pages = 0
    top_level_count = 0
    reply_count = 0
    duplicates = 0
    reply_errors: list[dict[str, str]] = []
    top_level_error = None
    limited = False
    budget_exhausted = False

    while True:
        if max_thread_pages > 0 and thread_pages >= max_thread_pages:
            limited = bool(thread_page_token)
            break
        try:
            request = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=100,
                order="time",
                textFormat="plainText",
                pageToken=thread_page_token,
            )
            # list()는 요청 조건을 만들고 execute()가 실제 API 호출을 수행한다.
            response = quota_tracker.execute(request)
        except QuotaBudgetExceeded as exc:
            top_level_error = str(exc)
            budget_exhausted = True
            break
        except Exception as exc:
            top_level_error = _safe_api_error(exc)
            break

        thread_pages += 1
        for item in response.get("items", []):
            row = parse_top_level_thread(item, video_id, collected_at)
            if _append_if_new(row, seen_comment_ids, rows):
                top_level_count += 1
            else:
                duplicates += 1

            # totalReplyCount가 1 이상이면 comments.list로 답글 전체를 조회한다.
            if row["reply_count"] > 0:
                reply_stats = collect_replies(
                    youtube,
                    video_id,
                    row["comment_id"],
                    collected_at,
                    rows,
                    seen_comment_ids,
                    max_reply_pages,
                    quota_tracker,
                )
                reply_pages += reply_stats["pages"]
                reply_count += reply_stats["replies"]
                duplicates += reply_stats["duplicates"]
                limited = limited or reply_stats["limited"]
                budget_exhausted = (
                    budget_exhausted or reply_stats["budget_exhausted"]
                )
                if reply_stats["error"]:
                    reply_errors.append(
                        {
                            "parent_id": row["comment_id"],
                            "error": reply_stats["error"],
                        }
                    )
                if budget_exhausted:
                    break

        if budget_exhausted:
            break

        print(
            f"  최상위 페이지 {thread_pages}: 최상위 {top_level_count}개, "
            f"답글 {reply_count}개"
        )

        # 응답의 nextPageToken을 다음 요청의 pageToken으로 넘긴다.
        next_token = response.get("nextPageToken")
        if not next_token:
            thread_page_token = None
            break
        if next_token in seen_thread_tokens:
            top_level_error = "RepeatedPageToken: 최상위 댓글 페이지 토큰이 반복되어 중단함"
            break
        seen_thread_tokens.add(next_token)
        thread_page_token = next_token

    if top_level_error or reply_errors:
        stop_reason = "error_partial"
    elif limited:
        stop_reason = "page_limit"
    else:
        stop_reason = "no_next_page"

    return rows, {
        "thread_pages_received": thread_pages,
        "reply_pages_received": reply_pages,
        "api_list_calls": thread_pages + reply_pages,
        "api_calls_total_including_video_info": quota_tracker.used,
        "api_request_budget": quota_tracker.limit,
        "api_request_budget_remaining": quota_tracker.remaining,
        "top_level_comments_saved": top_level_count,
        "replies_saved": reply_count,
        "comments_saved": len(rows),
        "duplicates_skipped_in_run": duplicates,
        "stop_reason": stop_reason,
        "collection_complete": stop_reason == "no_next_page",
        "top_level_error": top_level_error,
        "reply_errors": reply_errors,
        "budget_exhausted": budget_exhausted,
    }


def _demo_reply_pages(
    demo_data: dict[str, Any], parent_id: str
) -> Iterable[dict[str, Any]]:
    return demo_data.get("reply_pages", {}).get(parent_id, [])


def collect_demo_comments(
    demo_data: dict[str, Any],
    collected_at: str,
    *,
    max_thread_pages: int = 0,
    max_reply_pages: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """API 없이 최상위 댓글/답글 페이지 흐름을 연습한다."""

    video_id = demo_data["video"]["video_id"]
    thread_pages_data = demo_data["thread_pages"]
    selected_thread_pages = (
        thread_pages_data[:max_thread_pages]
        if max_thread_pages > 0
        else thread_pages_data
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    top_count = 0
    reply_count = 0
    reply_page_count = 0
    limited = len(selected_thread_pages) < len(thread_pages_data)

    for page in selected_thread_pages:
        for item in page.get("items", []):
            top_row = parse_top_level_thread(item, video_id, collected_at)
            if _append_if_new(top_row, seen, rows):
                top_count += 1
            else:
                duplicates += 1

            all_reply_pages = list(_demo_reply_pages(demo_data, top_row["comment_id"]))
            selected_reply_pages = (
                all_reply_pages[:max_reply_pages]
                if max_reply_pages > 0
                else all_reply_pages
            )
            limited = limited or len(selected_reply_pages) < len(all_reply_pages)
            for reply_page in selected_reply_pages:
                reply_page_count += 1
                for comment in reply_page.get("items", []):
                    reply_row = parse_comment_resource(
                        comment,
                        video_id,
                        collected_at,
                        parent_id=comment["snippet"].get("parentId")
                        or top_row["comment_id"],
                        reply_count=0,
                    )
                    if _append_if_new(reply_row, seen, rows):
                        reply_count += 1
                    else:
                        duplicates += 1

    return rows, {
        "thread_pages_received": len(selected_thread_pages),
        "reply_pages_received": reply_page_count,
        "api_list_calls": len(selected_thread_pages) + reply_page_count,
        "api_calls_total_including_video_info": 0,
        "api_request_budget": 0,
        "api_request_budget_remaining": None,
        "top_level_comments_saved": top_count,
        "replies_saved": reply_count,
        "comments_saved": len(rows),
        "duplicates_skipped_in_run": duplicates,
        "stop_reason": "page_limit" if limited else "no_next_page",
        "collection_complete": not limited,
        "top_level_error": None,
        "reply_errors": [],
        "budget_exhausted": False,
    }


def save_results(
    rows: list[dict[str, Any]],
    video_info: dict[str, Any],
    stats: dict[str, Any],
    *,
    max_thread_pages: int,
    max_reply_pages: int,
    mode: str,
    collected_at: str,
    output_root: Path,
) -> Path:
    """댓글 CSV와 수집 조건/한계 JSON을 같은 실행 폴더에 저장한다."""

    stamp = collected_at.replace("-", "").replace(":", "").replace("T", "_")
    stamp = stamp.removesuffix("Z")
    folder = output_root / f"{stamp}_{video_info['video_id']}"
    suffix = 2
    while folder.exists():
        folder = output_root / f"{stamp}_{video_info['video_id']}_{suffix}"
        suffix += 1
    folder.mkdir(parents=True, exist_ok=False)

    with (folder / "comments.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    # videos.list 1회도 쿼터 1단위를 사용하므로 합계에 더한다.
    estimated_quota_units = stats["api_calls_total_including_video_info"]
    reported_count = video_info.get("api_reported_comment_count")
    count_difference = (
        reported_count - len(rows) if isinstance(reported_count, int) else None
    )
    log = {
        "schema_version": "2.0",
        "mode": mode,
        "video": video_info,
        "collection": {
            "collected_at": collected_at,
            "order": "time",
            "max_results_per_request": 100,
            "max_thread_pages": max_thread_pages,
            "max_reply_pages_per_parent": max_reply_pages,
            "includes_top_level_comments": True,
            "includes_replies": True,
            "comment_date_filter_applied": False,
            "estimated_quota_units_used": estimated_quota_units,
            "api_reported_minus_collected": count_difference,
            **stats,
        },
        "api_capacity": {
            "commentThreads_list_cost_per_call": 1,
            "comments_list_cost_per_call": 1,
            "videos_list_cost_per_call": 1,
            "max_results_per_list_call": 100,
            "default_daily_quota_units": 10000,
            "note": (
                "각 parent의 답글 수와 페이지 수에 따라 실제 수집 가능량이 달라짐. "
                "숨김·삭제·검토 중 댓글은 API 키 요청으로 수집되지 않을 수 있음."
            ),
        },
    }
    (folder / "collection_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return folder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="영상 한 편의 공개 최상위 댓글과 모든 공개 답글을 수집합니다."
    )
    parser.add_argument(
        "--video",
        default=DEFAULT_VIDEO_URL,
        help=f"영상 URL 또는 ID (기본값: {DEFAULT_VIDEO_URL})",
    )
    parser.add_argument(
        "--max-pages",
        dest="max_thread_pages",
        type=int,
        default=0,
        help="최상위 댓글 최대 페이지 수. 0은 끝까지 수집 (기본값: 0)",
    )
    parser.add_argument(
        "--max-reply-pages",
        type=int,
        default=0,
        help="각 parent별 답글 최대 페이지 수. 0은 끝까지 수집 (기본값: 0)",
    )
    parser.add_argument(
        "--max-api-calls",
        type=int,
        default=9500,
        help=(
            "이번 실행의 API 요청 안전 예산. 기본 9500, 0은 제한 없음. "
            "Google 기본 일일 quota 10000보다 여유를 둔 값"
        ),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="결과 상위 경로"
    )
    parser.add_argument(
        "--check", action="store_true", help="API와 영상 통계만 확인하고 수집하지 않음"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Google API 대신 가상 응답을 사용"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.max_thread_pages < 0
        or args.max_reply_pages < 0
        or args.max_api_calls < 0
    ):
        raise SystemExit("페이지 제한은 0 이상의 정수여야 합니다. 0은 제한 없음입니다.")
    if args.check and args.demo:
        raise SystemExit("--check와 --demo는 동시에 사용할 수 없습니다.")

    collected_at = utc_now_text()
    try:
        if args.demo:
            demo_data = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
            video_info = demo_data["video"]
            rows, stats = collect_demo_comments(
                demo_data,
                collected_at,
                max_thread_pages=args.max_thread_pages,
                max_reply_pages=args.max_reply_pages,
            )
            mode = "DEMO_FICTIONAL"
        else:
            video_id = parse_video_id(args.video)
            youtube = make_youtube_client()
            quota_tracker = QuotaTracker(limit=args.max_api_calls)
            video_info = get_video_info(youtube, video_id, quota_tracker)
            print(f"채널: {video_info['channel_title']}")
            print(f"제목: {video_info['title']}")
            print(f"API 표시 댓글 수: {video_info['api_reported_comment_count']}")
            if video_info["api_reported_comment_count"] is not None:
                minimum_calls = 1 + math.ceil(
                    video_info["api_reported_comment_count"] / 100
                )
                print(
                    f"요청 수의 이론상 최소치: 약 {minimum_calls}회 "
                    "(답글 parent별 추가 호출 때문에 실제로는 더 큼)"
                )
            print(
                f"이번 실행 API 요청 예산: "
                f"{args.max_api_calls if args.max_api_calls else '제한 없음'}"
            )
            print("API 연결 및 영상 조회 성공")
            if args.check:
                return 0
            rows, stats = collect_comments(
                youtube,
                video_id,
                collected_at,
                max_thread_pages=args.max_thread_pages,
                max_reply_pages=args.max_reply_pages,
                quota_tracker=quota_tracker,
            )
            mode = "LIVE"
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    folder = save_results(
        rows,
        video_info,
        stats,
        max_thread_pages=args.max_thread_pages,
        max_reply_pages=args.max_reply_pages,
        mode=mode,
        collected_at=collected_at,
        output_root=args.output_dir.resolve(),
    )
    print(f"저장 완료: {folder}")
    print(
        f"최상위 {stats['top_level_comments_saved']}개 + "
        f"답글 {stats['replies_saved']}개 = 총 {len(rows)}개"
    )
    if mode == "LIVE":
        reported = video_info.get("api_reported_comment_count")
        print(
            f"API 요청 사용: {stats['api_calls_total_including_video_info']} / "
            f"{stats['api_request_budget'] if stats['api_request_budget'] else '제한 없음'}"
        )
        if isinstance(reported, int):
            print(f"API 표시 댓글 수와 수집 행 차이: {reported - len(rows)}")
    print(f"종료 이유: {stats['stop_reason']}")
    if not stats["collection_complete"]:
        print("주의: 오류 또는 페이지 제한 때문에 전체 수집이 아닙니다.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
