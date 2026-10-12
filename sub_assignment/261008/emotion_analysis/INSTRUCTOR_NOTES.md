# emotion_analysis 운영 메모

이 폴더는 week06 복사본이며 현재 GT는 0 부정 / 1 긍정 / 2 중립 / 3 혼합 / 4 판단 불가입니다.
수업의 실행 순서와 함수 구조를 유지한 자체 댓글 분석 코드는 train_sentiment.py입니다.
data/comments_261010.csv는 두 CSV의 모든 컬럼·행을 보존한 201행·30컬럼 병합 파일입니다.
댓글 없는 메모 1행은 전처리에서 제외하며, 숫자 label을 GT로 사용하는 댓글은 200개입니다.
label_name 불일치는 요약에 기록하며 label_name/human_label/is_trainable은 GT 기준으로 쓰지 않습니다.
examples는 기존 0/1 합성 교육용 결과입니다. 현재 데이터의 결과와 섞지 마세요.
노트북의 INCLUDE_LSTM과 RUN_FINAL_TEST는 기본 False입니다.
같은 분할로 후보를 검증한 뒤 설정을 고정하고 마지막 test를 평가하세요.
GT 기준, 실제 분할 수, validation 점수, 선택 이유, 실제 오류를 보고서에 기록하세요.
