-- 영상 한 편당 한 행을 저장한다.
CREATE TABLE IF NOT EXISTS public.lab_videos (
    video_id TEXT PRIMARY KEY,
    title TEXT,
    channel_title TEXT,
    video_published_at TIMESTAMPTZ,
    video_url TEXT,
    api_reported_comment_count INTEGER CHECK (api_reported_comment_count >= 0),
    is_demo BOOLEAN NOT NULL DEFAULT FALSE,
    first_collected_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 최상위 댓글 한 개당 한 행을 저장한다.
-- comment_id를 기본키로 지정하여 같은 댓글이 두 번 저장되는 것을 막는다.
CREATE TABLE IF NOT EXISTS public.lab_comments (
    comment_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES public.lab_videos(video_id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES public.lab_comments(comment_id) ON DELETE CASCADE,
    comment_type TEXT NOT NULL CHECK (comment_type IN ('top_level', 'reply')),
    author_display_name TEXT,
    author_channel_id TEXT,
    text_raw TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    like_count INTEGER NOT NULL CHECK (like_count >= 0),
    reply_count INTEGER NOT NULL CHECK (reply_count >= 0),
    collected_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lab_comments_video_id_idx
    ON public.lab_comments(video_id);

CREATE INDEX IF NOT EXISTS lab_comments_parent_id_idx
    ON public.lab_comments(parent_id);

-- 브라우저에서 익명 API로 표가 공개되지 않게 RLS를 켠다.
-- 이 실습 코드는 DB 연결 문자열로 접속하므로 별도의 공개 정책이 필요 없다.
ALTER TABLE public.lab_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lab_comments ENABLE ROW LEVEL SECURITY;
