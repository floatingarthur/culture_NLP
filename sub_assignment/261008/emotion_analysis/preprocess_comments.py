"""Save the exact preprocessing used by train_sentiment.load_data for inspection."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
from train_sentiment import BASE, DEFAULT_DATA, load_data, write_csv


def main(argv=None):
    parser = argparse.ArgumentParser(description="라벨링한 comments_261010.csv의 전처리·그룹 결과 저장")
    parser.add_argument("--csv", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir or BASE / "outputs" / datetime.now(timezone.utc).strftime("preprocess_%Y%m%dT%H%M%S%fZ")
    try:
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise ValueError(f"출력 폴더가 비어 있지 않습니다: {output}")
        data, metadata = load_data(args.csv)
        output.mkdir(parents=True, exist_ok=True)
        write_csv(data.drop(columns=["_text_key"]), output / "comments_preprocessed.csv")
        (output / "preprocessing_summary.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    except (ValueError, OSError, pd.errors.ParserError) as exc:
        parser.exit(1, f"입력/설정 확인: {exc}\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("[SAVED]", output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
