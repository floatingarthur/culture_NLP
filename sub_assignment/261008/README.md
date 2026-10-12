# 댓글 감정 분석 실습

`emotion_analysis/`는 CNT3054 Week 06의 코드 구조를 유지하면서 조별 YouTube 댓글 데이터에 맞게 수정한 실습 폴더입니다. 이 폴더만 내려받아 실행할 수 있습니다.

- 실행 파일: [emotion_analysis/week06_sentiment_lab.ipynb](emotion_analysis/week06_sentiment_lab.ipynb)
- 데이터: [emotion_analysis/data/comments_261010.csv](emotion_analysis/data/comments_261010.csv)
- 상세 실행 안내: [emotion_analysis/README.md](emotion_analysis/README.md)
- 제출용 압축 파일: [emotion_analysis.zip](emotion_analysis.zip)

노트북의 기존 실행 결과와 `outputs/`의 학습·평가 결과를 함께 보관했습니다. 제출용 ZIP에는 가상환경과 Python 캐시가 포함되지 않습니다.

## 설치와 실행

Python 3.11 또는 3.12를 사용합니다. `sub_assignment/261008`에서 PowerShell로 실행합니다.

```powershell
py -3.12 -m venv emotion_analysis/.venv
./emotion_analysis/.venv/Scripts/python.exe -m pip install -r emotion_analysis/requirements-notebook.txt
./emotion_analysis/.venv/Scripts/python.exe -m pip install -r emotion_analysis/requirements-lstm.txt
```

Jupyter 또는 VS Code에서 `week06_sentiment_lab.ipynb`를 열고 위에서 만든 가상환경을 커널로 선택해 셀을 순서대로 실행합니다. 일반 머신러닝 모델만 사용할 때는 LSTM 의존성 설치를 생략할 수 있습니다.

입력 본문은 `translated_ko`, 정답은 숫자 `label`입니다. 라벨 기준은 **0=부정, 1=긍정, 2=중립, 3=혼합, 4=판단 불가**입니다. 최상위 댓글 200개를 train/validation/test 120/40/40개로 분할하며, 후보의 validation Macro-F1을 비교해 최종 모델을 선택합니다.

후보는 Logistic Regression, LinearSVC, MultinomialNB, XGBoost와 LSTM입니다. 저장된 실행에서는 XGBoost가 선택되었습니다. 최종 test 평가는 기준선과 선택된 모델에 대해서만 수행합니다.
