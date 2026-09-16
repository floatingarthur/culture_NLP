# YouTube 뉴스 댓글 수집 실습

예시 저장소의 `main()` 중심 실행 구조를 참고하되, 이번 과제에 필요한 **단일
영상의 최상위 댓글과 답글 전체 수집**만 남긴 코드입니다. 음성, 자막, 재생목록 수집은 하지
않습니다.

기본 대상 영상은 `https://youtu.be/S1bcy4h1mS8`입니다.

## 파일 구조

- `collect_comments.py`: 최상위 댓글과 각 parent의 답글 페이지 반복 수집, 중복 제거, CSV/로그 저장
- `upload_supabase.py`: DB 연결 확인, 테이블 생성, 수집 결과 업로드, 건수 결과 저장
- `setup_tables.sql`: `lab_videos`, `lab_comments` 테이블 정의
- `demo_pages.json`: API 키 없이 흐름을 점검하는 가상 응답
- `.env.example`: 환경변수 이름 예시. 실제 값은 `.env`에 저장

참고한 원본: <https://github.com/111usionBin/jtbc-2025>

## 1. 실행 환경 준비

PowerShell에서 다음 명령을 한 줄씩 실행합니다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`의 `google_cloud_api_key`에 YouTube Data API v3 키를 입력합니다. 현재
프로젝트처럼 `keys.env`를 사용해도 자동으로 인식합니다. 두 파일은 GitHub나
제출 파일에 포함하지 않습니다.

## 2. API 없이 먼저 점검

```powershell
.\.venv\Scripts\python.exe collect_comments.py --demo --max-pages 2
```

가상 최상위 댓글 2개와 답글 2개가 `output/실행시각_DEMO_VIDEO_1/` 아래에 저장됩니다. 로그의
`mode`가 `DEMO_FICTIONAL`이므로 실제 댓글과 구분할 수 있습니다.

## 3. 실제 영상과 API 연결 확인

```powershell
.\.venv\Scripts\python.exe collect_comments.py --check
```

채널, 제목, API 표시 댓글 수와 최소 예상 호출 수가 보이면 연결된 것입니다. 다른 영상을 쓰려면
`--video "영상_URL"`을 추가합니다.

## 4. 댓글 수집

모든 공개 최상위 댓글과 답글을 끝까지 수집:

```powershell
.\.venv\Scripts\python.exe collect_comments.py
```

점검 목적으로 최상위 댓글 3페이지까지만 제한하려면:

```powershell
.\.venv\Scripts\python.exe collect_comments.py --max-pages 3
```

`--max-pages 0`과 `--max-reply-pages 0`이 기본값이며 `nextPageToken`이 없어질
때까지 반복한다는 뜻입니다. 각 실행은 새 폴더에 다음 파일을 만듭니다.

- `comments.csv`: 최상위 댓글과 답글의 ID, parent ID, 작성자, 본문, 작성/수정 시각, 좋아요 수
- `collection_log.json`: 수집 조건, 페이지 수, 중복 수, 종료 이유

`stop_reason`이 `no_next_page`이고 `collection_complete`가 `true`여야 코드가
접근할 수 있는 공개 댓글을 끝까지 받은 것입니다. 숨김, 삭제, 검토 중인 댓글은
일반 API 키로 보이지 않을 수 있으며 댓글 작성일 필터는 적용하지 않습니다.

## API 호출량과 quota 보호

- `videos.list`, `commentThreads.list`, `comments.list`는 각각 호출당 1 quota unit입니다.
- 목록 호출 한 번에 최대 100개를 받습니다.
- 기본 일일 quota는 10,000 units이며, 코드의 기본 안전 예산은 9,500회입니다.
- 답글은 parent마다 `comments.list`를 따로 호출하므로 댓글 수만으로 정확한 사전
  요청 횟수를 계산할 수 없습니다.
- 실제 호출 수와 남은 실행 예산은 `collection_log.json`에 기록됩니다.

안전 예산을 더 낮추려면 `--max-api-calls 500`처럼 지정합니다. 예산에 도달하면
받은 데이터는 저장하지만 `collection_complete`는 `false`가 됩니다. Google Cloud
프로젝트에서 오늘 실제로 남은 quota는 API 키만으로 조회할 수 없으므로 Cloud
Console의 **APIs & Services > YouTube Data API v3 > Quotas**에서 확인합니다.

## 5. Supabase 저장

Supabase에서 프로젝트를 만든 뒤 **Connect > Session pooler > URI**를 복사합니다.
연결 문자열과 DB 비밀번호는 다음 명령으로 `.env`에 저장할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe upload_supabase.py --configure
.\.venv\Scripts\python.exe upload_supabase.py --check-db
.\.venv\Scripts\python.exe upload_supabase.py --init-db
```

수집 결과 폴더를 지정해 업로드합니다.

```powershell
.\.venv\Scripts\python.exe upload_supabase.py --folder "output\실행폴더명"
```

업로드 후 같은 폴더에 `db_upload_result.json`이 생성됩니다. 이 파일에는 CSV 행
수, 새로 저장된 댓글 수, DB에서 중복으로 제외한 수, 해당 영상의 DB 댓글 총수가
기록됩니다.

## 중복 저장 방지 방식

1. 수집 중에는 `seen_comment_ids` 집합으로 같은 `comment_id`를 한 번만 CSV에 씁니다.
2. CSV를 읽을 때도 같은 `comment_id`가 반복되면 한 번만 업로드합니다.
3. DB에서는 `lab_comments.comment_id`를 기본키로 만들고
   `ON CONFLICT (comment_id) DO NOTHING`을 사용합니다.

따라서 같은 수집 폴더를 다시 업로드해도 DB 댓글 수가 늘어나지 않습니다.
