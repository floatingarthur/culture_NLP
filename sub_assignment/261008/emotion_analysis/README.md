# 댓글 감정 분석

`week06`을 복사한 `emotion_analysis` 폴더입니다. 수업의 함수 이름과 CSV 읽기 → 분할 → 학습 → 검증 비교 → 오류 확인 → 최종 테스트 순서를 유지합니다.

## 데이터 넣기

`data/comments_261010.csv`는 `2번csv.csv` 101행과 `3번csv.csv` 100행을 합친 실제 데이터입니다. 원본의 모든 값과 30개 컬럼을 보존했으며, UTF-8 BOM으로 저장했습니다. 원본 파일은 그대로 유지합니다.

필수 열은 `translated_ko`(번역된 한국어 댓글 본문)와 숫자 `label`(GT)입니다. 다른 열이 있어도 읽을 수 있고, 학습 특징에는 본문만 사용합니다. CSV는 UTF-8 또는 UTF-8 BOM으로 저장합니다.

| label | 의미 |
|---:|---|
| 0 | 부정 |
| 1 | 긍정 |
| 2 | 중립 |
| 3 | 혼합 |
| 4 | 판단 불가 |

0~4의 5개 클래스를 모두 학습·평가합니다. 4는 제외 대상이나 빈 라벨의 대체값이 아닙니다. 빈 라벨, 범위 밖 숫자, 문자열 클래스 이름은 오류로 처리합니다. 기존 보고서보다 위의 최신 정의가 우선합니다.

현재 데이터의 댓글은 200개이며 라벨별 수는 0=57, 1=92, 2=37, 3=9, 4=5입니다. 나머지 1행은 `label_reason`만 있는 메모이며, CSV에 보존하고 전처리에서 제외합니다. `label_name`과 숫자 `label`이 다른 62행도 원본 그대로 보존합니다. 정답에는 숫자 `label`을 사용하며 `label_name`, `human_label`, `is_trainable`은 GT나 학습 제외 기준으로 쓰지 않습니다. 불일치 수는 전처리 요약에 기록합니다.

선택 열: `comment_id` 또는 `review_id`, `parent_id`, `comment_type`, `author_channel_id`, `group_id`, `video_id`, `domain`, `source_type`.

- `parent_id`가 있으면 공백인 최상위 댓글만 사용합니다. `comment_type`도 있으면 top_level/reply 일치 여부를 검사합니다.
- `comment_type`만 있으면 top_level만 사용합니다. 두 열이 모두 없으면 최상위 댓글만 담은 파일이라고 가정합니다.
- 본문·GT·댓글 ID·댓글 유형이 모두 없는 메모 행은 학습에서 제외합니다. 실제 댓글의 누락된 본문이나 GT는 계속 오류로 처리합니다.
- Unicode/공백을 정리하고 URL·이메일·휴대전화·핸들을 치환합니다. 부정 표현, 짧은 댓글, 팬덤 어휘, 문장부호, 이모지는 보존합니다.
- 같은 정리된 본문의 GT가 다르면 중단해 재검토합니다. 동일 본문, 동일 작성자, 제공한 group_id를 연결해 한 그룹으로 만든 뒤 중복 본문을 축약합니다. 원문에서 서로 다른 댓글이 번역 후 동일해진 경우에도 GT 충돌을 확인하세요.
- 영상 ID는 분할 그룹으로 쓰지 않습니다. 단일 영상의 모든 행을 같은 group_id로 설정하면 분할할 수 없습니다.
- 각 라벨에 최소 5개의 독립 그룹이 필요하며, 남은 develop 자료의 내부 4분할과 모든 분할의 다섯 클래스 포함 여부도 확인합니다. 이는 실행 조건이지 충분한 연구 표본 수를 보장하는 값은 아닙니다.

## 설치와 실행

Python 3.11 또는 3.12를 사용합니다. 아래 명령은 `emotion_analysis`의 상위 폴더에서 실행합니다. 현재 위치에서는 `CNT3054-main`, 폴더를 옮긴 뒤에는 `sub_assignment/261008`에서 실행하면 됩니다.

```powershell
py -3.12 -m venv emotion_analysis/.venv
./emotion_analysis/.venv/Scripts/python.exe -m pip install -r emotion_analysis/requirements-notebook.txt
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py
```

이미 가상환경이 있으면 첫 줄은 생략합니다. `py`가 없으면 설치된 Python 3.11/3.12 실행 파일을 사용합니다. macOS/Linux에서는 `python3 -m venv emotion_analysis/.venv`와 `emotion_analysis/.venv/bin/python`을 사용합니다. 데이터는 스크립트 기준 경로로 찾으므로 실행할 때 `--csv`를 생략해도 됩니다.

모든 `.py`의 기본 데이터·결과 경로는 `Path(__file__).resolve().parent`로 찾는 코드 폴더를 기준으로 합니다. `train_lstm.py`와 `preprocess_comments.py`도 같은 폴더의 `train_sentiment.py`를 사용합니다. 노트북은 편집기가 제공하는 현재 노트북 경로를 우선 사용하고, 일반 Jupyter에서는 커널 작업 폴더를 기준으로 찾습니다. 상위 폴더에서 시작했으면 그 아래의 학습 코드·노트북 폴더를 검색하므로 `sub_assignment`, `261008`, `CNT3054-main` 같은 상위 이름을 고정하지 않습니다. 후보가 여러 개면 현재 노트북 폴더에서 실행하도록 안내합니다. 다른 복사본에서 가져온 모듈이 커널에 남아 있으면 현재 폴더에서 다시 가져옵니다. 명시적으로 지정한 `--csv`/`--output-dir`의 상대 경로는 명령을 실행하는 작업 폴더 기준입니다.

```powershell
# 전처리 결과만 먼저 확인 (라벨링 완료한 CSV 사용)
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/preprocess_comments.py

# 일반 4종: LR(두 n-gram 설정), LinearSVC, MultinomialNB, XGBoost
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py

# 수업 LR 비교만 먼저 실행할 경우
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py --models unigram unigram_bigram

# LSTM을 같은 분할의 후보로 추가
./emotion_analysis/.venv/Scripts/python.exe -m pip install -r emotion_analysis/requirements-lstm.txt
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py --include-lstm

# 선택과 설정 변경을 끝낸 후 최종 테스트
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py --include-lstm --evaluate-test

# 그래프 없이 저장 / 다른 CSV 지정
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py --no-plots
./emotion_analysis/.venv/Scripts/python.exe emotion_analysis/train_sentiment.py --csv emotion_analysis/data/comments_261010.csv
```

`train_lstm.py`를 실행해도 LSTM을 포함한 공통 비교 흐름을 사용합니다. `--lstm-epochs 5`처럼 최대 epoch를 변경할 수 있습니다. 기본 ML 실행에는 TensorFlow가 필요하지 않습니다.

## 수업 코드와 같은 분석 흐름

1. `load_data`: translated_ko를 내부 text로 연결하고 label·답글·중복·그룹을 검사합니다.
2. `split_data`: seed 42, StratifiedGroupKFold 5분할 후 4분할로 약 60/20/20을 만듭니다.
3. `make_model`: train만으로 TF-IDF와 분류기를 함께 학습하는 Pipeline을 만듭니다.
4. `compare_models`: 다수 클래스 기준선과 후보를 같은 validation으로 비교합니다. test 인자가 없습니다.
5. `prediction_table`, `feature_weights`, `save_plots`: 오류 방향, 클래스별 지표·확률·특징을 저장합니다.
6. `run_experiment`: 수업처럼 선택한 train 모델을 유지하고 validation을 합쳐 재학습하지 않습니다.
7. `--evaluate-test`일 때만 기준선과 선택 모델을 test에서 평가합니다.

LSTM은 train 전용 토큰 단어장 → Embedding → LSTM → 5개 출력의 softmax 흐름이며, validation loss로 early stopping하고 최적 가중치를 복원합니다. 후보 선택은 다른 모델과 동일한 validation Macro-F1입니다. XGBoost/LSTM의 내부 클래스와 저장되는 GT·예측은 모두 0~4이며, 0을 부정으로 학습합니다.

기본 비교표는 Dummy + LR 두 설정 + SVM + NB + XGBoost = 6행이며, LSTM 포함 시 7행입니다. 동점 우선순위는 unigram, unigram_bigram, linear_svm, multinomial_nb, xgboost, lstm입니다. 후보가 기준선보다 좋아졌는지도 별도 기록합니다.

TF-IDF는 수업의 한 글자 단어 패턴을 확장해 이모지·문장부호도 토큰에 포함합니다. 따라서 수업과 완전히 같은 특징 공간은 아닙니다. `examples/`는 복사한 수업의 0/1 합성 데이터 결과로, 우리 데이터의 점수나 기대 성능이 아닙니다. 최신 다섯 클래스와 합성 이진 예제의 수치를 직접 비교해 향상을 주장하지 않습니다.

## 결과 파일

매 실행은 `outputs/run_날짜시간/`에 저장합니다. `--output-dir`는 비어 있는 폴더만 허용합니다.

| 파일 | 내용 |
|---|---|
| validation_scores.csv | 후보별 Accuracy·macro P/R/F1·Weighted-F1·학습 시간 |
| validation_classification_reports.csv | 기준선/모델/클래스별 P/R/F1/support |
| validation_predictions.csv, validation_errors.csv | 선택 모델의 GT·예측·오류 방향·확률 또는 SVM 점수 |
| split_assignments.csv | review_id·group_id·GT·분할. 모든 후보가 같은 분할 사용 |
| feature_weights.csv | LR/SVM 클래스별 계수, NB 로그확률 또는 트리 중요도. LSTM 선택 시 빈 표 |
| validation_confusion_matrix.png | 0·1·2·3·4 순서의 5×5 행렬, 행=실제/열=예측 |
| feature_weights.png, top_features.csv | 해석 가능한 선택 모델의 특징 그림과 대응표 |
| metrics.json | 데이터 해시·전처리 요약·라벨·분할·후보 설정·선택 근거·패키지 버전 |
| lstm/ | LSTM 포함 시 model.keras·vocabulary.json·settings.json·training_history.csv |
| test_* | --evaluate-test 때만 기준선·고정 모델의 최종 점수·오류·보고서·그림 |

SVM의 `score_0..score_4`는 확률이 아닙니다. 나머지 모델의 `p_0..p_4`도 감정 강도를 뜻하지 않습니다. 낮은 신뢰도를 자동으로 4로 바꾸지 않습니다. `known_features=0` 또는 `known_tokens=0`은 학습 어휘와의 불일치이며 중립/판단 불가 GT가 아닙니다.

노트북은 복사된 이름 `week06_sentiment_lab.ipynb`를 유지하되 내용은 최신 스키마로 변경했습니다. `emotion_analysis/.venv` 커널을 선택하고 위에서부터 실행합니다. 기본 INCLUDE_LSTM과 RUN_FINAL_TEST는 False입니다.

## 검증

```powershell
./emotion_analysis/.venv/Scripts/python.exe -m unittest discover -s emotion_analysis/tests -v
```

테스트는 임시 폴더의 명시적인 5클래스 fixture를 사용하며 실제 comments_261010.csv를 바꾸지 않습니다. 실제 데이터의 점수와 오류 사례는 실행한 outputs에서 확인합니다.

## 폴더 이동과 제출

`emotion_analysis` 폴더만으로 실행에 필요한 소스와 설정이 모두 포함됩니다. 원본 `week06`이나 `CNT3054-main`의 다른 파일은 필요하지 않습니다. `sub_assignment/261008/emotion_analysis`로 옮겨도 스크립트와 노트북은 폴더 안의 코드와 데이터를 찾습니다.

제출할 때 `.venv/`, `__pycache__/`, `.ipynb_checkpoints/`는 제외하세요. 가상환경은 이 컴퓨터의 Python 경로를 포함하므로 다른 컴퓨터에서는 위 설치 명령으로 새로 만들어야 합니다. 나머지 폴더 구조와 파일은 유지하고, 병합된 `data/comments_261010.csv`를 함께 제출하세요. LSTM을 실행하려면 `requirements-lstm.txt`도 설치합니다.

공식 API 참고: [그룹 분할](https://scikit-learn.org/1.8/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html), [LinearSVC](https://scikit-learn.org/1.8/modules/generated/sklearn.svm.LinearSVC.html), [XGBoost](https://xgboost.readthedocs.io/en/stable/python/python_api.html), [TextVectorization](https://www.tensorflow.org/api_docs/python/tf/keras/layers/TextVectorization), [LSTM](https://www.tensorflow.org/api_docs/python/tf/keras/layers/LSTM).
