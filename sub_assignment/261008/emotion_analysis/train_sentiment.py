"""Week06 flow for translated_ko / GT: 0 negative, 1 positive, 2 neutral, 3 mixed, 4 undetermined.

Default: validation only. --evaluate-test evaluates the fixed winner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import importlib.metadata
import json
import platform
from pathlib import Path
import re
from time import perf_counter
import unicodedata

import numpy as np
import pandas as pd
import sklearn
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report, confusion_matrix,
                             f1_score, precision_score, recall_score)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

BASE = Path(__file__).resolve().parent
DEFAULT_DATA = BASE / "data" / "comments_261010.csv"
SEED = 42
LABELS = [0, 1, 2, 3, 4]
LABEL_NAMES = {0: "부정", 1: "긍정", 2: "중립", 3: "혼합", 4: "판단 불가"}
LABEL_NAMES_EN = {0: "negative", 1: "positive", 2: "neutral", 3: "mixed", 4: "undetermined"}
CANDIDATES = {
    "unigram": (1, 1), "unigram_bigram": (1, 2),
    "linear_svm": (1, 2), "multinomial_nb": (1, 2), "xgboost": (1, 2),
}
# Extend the teaching pattern to preserve punctuation/emoji-only comments.
TOKEN_PATTERN = r"(?u)\b\w+\b|[^\w\s]"


def normalize_text(text: str) -> str:
    """Keep negation/slang/emoji; normalize whitespace and replace identifiable patterns."""
    # NFKC changes ㅋㅋ/ㅠㅠ into conjoining jamo. Preserve expressive jamo
    # while normalizing full-width characters and other Unicode variants.
    parts = re.split(r"([\u3131-\u318e]+)", html.unescape(text))
    text = "".join(part if re.fullmatch(r"[\u3131-\u318e]+", part)
                   else unicodedata.normalize("NFKC", part) for part in parts)
    text = re.sub(r"https?://\S+|www\.\S+", " [URL] ", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " [EMAIL] ", text)
    text = re.sub(r"(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)", " [PHONE] ", text)
    text = re.sub(r"(?<!\w)@[\w.-]+", " [USER] ", text)
    return " ".join(text.split())


def text_key(text: str) -> str:
    """Conservative duplicate key; preserve punctuation, case and emoji."""
    return normalize_text(text)


def _build_groups(data: pd.DataFrame) -> pd.Series:
    """Union same text, supplied groups and authors BEFORE dropping duplicates."""
    parents = list(range(len(data)))

    def find(i):
        while i != parents[i]:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[max(a, b)] = min(a, b)

    for column in ["_text_key"] + [c for c in ["group_id", "author_channel_id"] if c in data]:
        seen = {}
        for i, value in enumerate(data[column]):
            key = str(value).strip()
            if not key:
                continue
            if key in seen:
                union(i, seen[key])
            else:
                seen[key] = i
    return pd.Series([f"GROUP{find(i) + 1:06d}" for i in range(len(data))], index=data.index)


def _check_groups(data: pd.DataFrame, folds: int, context: str):
    counts = data.groupby("label")["group_id"].nunique().reindex(LABELS, fill_value=0)
    if data["group_id"].nunique() < folds or counts.min() < folds:
        raise ValueError(f"{context}: 각 라벨이 최소 {folds}개의 독립 그룹에 있어야 합니다. 라벨별 그룹 수: {counts.to_dict()}")


def load_data(path: str | Path = DEFAULT_DATA) -> tuple[pd.DataFrame, dict]:
    """Required translated_ko,label; optional reply/author/group metadata.

    Class 4 is a GT class, not excluded or inferred from missing labels.
    Only the translated text is used as a model feature.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"데이터 파일이 없습니다: {path}\ntranslated_ko,label 열이 있는 CSV를 이 경로에 넣으세요.")
    data = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    missing = {"translated_ko", "label"} - set(data.columns)
    if missing:
        raise ValueError(f"CSV에 필요한 열이 없습니다: {sorted(missing)}")
    original_rows = len(data)
    if original_rows == 0:
        raise ValueError("comments_261010.csv에 실제 데이터가 없습니다. 헤더 아래에 라벨링한 댓글을 넣으세요.")
    # Keep memo rows in the source CSV, but exclude records with neither
    # comment text, GT nor comment identity from the training population.
    annotation_only = data["translated_ko"].str.strip().eq("") & data["label"].str.strip().eq("")
    for column in ["comment_id", "review_id", "parent_id", "comment_type"]:
        if column in data:
            annotation_only &= data[column].str.strip().eq("")
    annotation_rows_removed = int(annotation_only.sum())
    data = data.loc[~annotation_only].copy()
    if data.empty:
        raise ValueError("comments_261010.csv에 실제 댓글 데이터가 없습니다. 메모 행 외의 라벨링한 댓글을 넣으세요.")
    replies_removed = 0
    if "parent_id" in data:
        top_level = data["parent_id"].str.strip().eq("")
        if "comment_type" in data:
            types = data["comment_type"].str.strip()
            consistent = (top_level & types.eq("top_level")) | (~top_level & types.eq("reply"))
            if not consistent.all():
                raise ValueError("parent_id와 comment_type이 일치하지 않습니다. 댓글/답글 구분을 확인하세요.")
        replies_removed = int((~top_level).sum())
        data = data.loc[top_level].copy()
    elif "comment_type" in data:
        types = data["comment_type"].str.strip()
        if not types.isin(["top_level", "reply"]).all():
            raise ValueError("comment_type은 top_level 또는 reply여야 합니다.")
        replies_removed = int(types.eq("reply").sum())
        data = data.loc[types.eq("top_level")].copy()
    data = data.reset_index(drop=True)
    if data.empty:
        raise ValueError("답글 제외 후 최상위 댓글이 없습니다.")
    data["text"] = data["translated_ko"].map(normalize_text)
    if data["text"].eq("").any():
        raise ValueError("빈 translated_ko가 있습니다. 번역 댓글 본문을 확인하세요.")
    data["label"] = data["label"].str.strip()
    valid = data["label"].isin([str(label) for label in LABELS])
    if not valid.all():
        invalid = sorted(data.loc[~valid, "label"].unique().tolist())
        raise ValueError(f"label은 0(부정), 1(긍정), 2(중립), 3(혼합), 4(판단 불가)여야 합니다. 빈 라벨도 허용하지 않습니다: {invalid}")
    data["label"] = data["label"].astype(int)
    label_name_mismatches = int((data["label_name"].str.strip() != data["label"].map(LABEL_NAMES)).sum()) if "label_name" in data else 0
    data["_text_key"] = data["text"].map(text_key)
    if data.groupby("_text_key")["label"].nunique().gt(1).any():
        raise ValueError("같은 정규화 문장에 서로 다른 label이 있습니다. 원문/번역과 GT 기준을 검토하세요.")
    if "review_id" not in data:
        data["review_id"] = data["comment_id"] if "comment_id" in data else [f"ROW{i + 1:06d}" for i in range(len(data))]
    data["review_id"] = data["review_id"].str.strip()
    if data["review_id"].eq("").any() or data["review_id"].duplicated().any():
        raise ValueError("review_id 또는 comment_id는 비어 있지 않고 각 행마다 달라야 합니다.")
    if "group_id" in data and data["group_id"].str.strip().eq("").any():
        raise ValueError("group_id가 비어 있습니다. 열을 제거해 자동 그룹을 쓰거나 모든 그룹을 지정하세요.")
    before_dedup = len(data)
    data["group_id"] = _build_groups(data)
    data = data.drop_duplicates("_text_key").copy().reset_index(drop=True)
    if set(data["label"]) != set(LABELS):
        raise ValueError(f"학습에는 0~4 라벨이 모두 필요합니다. 없는 라벨: {sorted(set(LABELS) - set(data['label']))}")
    _check_groups(data, 5, "전체 자료")
    if "source_type" not in data:
        data["source_type"] = "user_provided_comments"
    metadata = {
        "input_rows": original_rows, "annotation_rows_removed": annotation_rows_removed, "replies_removed": replies_removed,
        "top_level_rows": before_dedup, "deduplicated_rows": len(data),
        "duplicates_removed": before_dedup - len(data), "groups": int(data["group_id"].nunique()),
        "source_types": sorted(data["source_type"].unique().tolist()),
        "text_column": "translated_ko", "target_column": "label", "label_names": LABEL_NAMES,
        "label_name_mismatches": label_name_mismatches, "gt_policy": "numeric label is authoritative; label_name/human_label/is_trainable are not GT",
        "group_method": "connected components of text, provided group_id and author_channel_id",
        "preprocessing": "NFKC, HTML unescape, whitespace; URL/email/phone/handle placeholders; punctuation and emoji preserved",
    }
    return data, metadata


def split_data(data: pd.DataFrame, seed: int = SEED) -> dict[str, pd.DataFrame]:
    """Keep Week06's approximate 60/20/20 grouped, stratified split."""
    _check_groups(data, 5, "전체 자료")
    outer = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
    develop_idx, test_idx = next(outer.split(data["text"], data["label"], data["group_id"]))
    develop = data.iloc[develop_idx]
    _check_groups(develop, 4, "train+validation 자료")
    inner = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=seed)
    train_idx, val_idx = next(inner.split(develop["text"], develop["label"], develop["group_id"]))
    splits = {"train": develop.iloc[train_idx].copy(), "validation": develop.iloc[val_idx].copy(), "test": data.iloc[test_idx].copy()}
    for name, frame in splits.items():
        if set(frame["label"]) != set(LABELS):
            raise ValueError(f"{name}에 0~4 라벨이 모두 필요합니다. 그룹/클래스 분포와 자료 규모를 점검하세요.")
    frames = list(splits.values())
    for i, frame in enumerate(frames):
        for other in frames[i + 1:]:
            for column in ["review_id", "group_id", "_text_key"]:
                if set(frame[column]) & set(other[column]):
                    raise ValueError(f"분할 간 {column}이 겹칩니다.")
    return splits


class EmotionXGBClassifier(ClassifierMixin, BaseEstimator):
    """Use public GT 0..4 directly, including the negative class."""
    def __init__(self, seed=SEED, n_estimators=200):
        self.seed = seed
        self.n_estimators = n_estimators

    def fit(self, X, y):
        try:
            from xgboost import XGBClassifier
        except ImportError as exc:
            raise ValueError("XGBoost가 없습니다. requirements.txt를 설치하거나 --models로 다른 후보를 선택하세요.") from exc
        self.classes_ = np.asarray(LABELS)
        self.model_ = XGBClassifier(objective="multi:softprob", num_class=len(LABELS),
            n_estimators=self.n_estimators, max_depth=3, learning_rate=0.05,
            tree_method="hist", random_state=self.seed, n_jobs=1, eval_metric="mlogloss")
        self.model_.fit(X, np.asarray(y, dtype=int))
        return self

    def predict(self, X):
        return self.model_.predict(X).astype(int)

    def predict_proba(self, X):
        return self.model_.predict_proba(X)

    @property
    def feature_importances_(self):
        return self.model_.feature_importances_


def make_model(ngram_range: tuple[int, int] = (1, 1), seed: int = SEED, model_name="unigram") -> Pipeline:
    """Preserve the original TF-IDF -> classifier Pipeline and train-only fit."""
    if model_name in ("unigram", "unigram_bigram"):
        classifier = LogisticRegression(C=1.0, max_iter=1000, random_state=seed)
    elif model_name == "linear_svm":
        classifier = LinearSVC(C=1.0, max_iter=5000, random_state=seed)
    elif model_name == "multinomial_nb":
        classifier = MultinomialNB(alpha=1.0)
    elif model_name == "xgboost":
        classifier = EmotionXGBClassifier(seed=seed)
    else:
        raise ValueError(f"알 수 없는 모델: {model_name}")
    return Pipeline([
        ("tfidf", TfidfVectorizer(token_pattern=TOKEN_PATTERN, ngram_range=ngram_range, min_df=1, lowercase=True)),
        ("classifier", classifier),
    ])


def score_predictions(y_true, y_pred) -> dict:
    """Four-class metrics; retain Week06's f1_macro selection key."""
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_macro": float(precision_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)),
        "recall_macro": float(recall_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)),
        "f1_macro": float(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)),
        "f1_weighted": float(f1_score(y_true, y_pred, labels=LABELS, average="weighted", zero_division=0)),
    }


def compare_models(train: pd.DataFrame, validation: pd.DataFrame, seed: int = SEED,
                   model_names=None, include_lstm=False, lstm_epochs=20):
    """No test argument; preserve the teaching function's return tuple."""
    names = list(CANDIDATES) if model_names is None else list(model_names)
    if not names or len(set(names)) != len(names) or set(names) - set(CANDIDATES):
        raise ValueError(f"중복 없이 다음 후보를 하나 이상 지정하세요: {list(CANDIDATES)}")
    models, records = {}, []
    baseline = DummyClassifier(strategy="most_frequent")
    start = perf_counter()
    baseline.fit(train[["text"]], train["label"])
    elapsed = perf_counter() - start
    records.append({"model": "majority_baseline", **score_predictions(validation["label"], baseline.predict(validation[["text"]])), "fit_seconds": elapsed})
    for name in names:
        model = make_model(CANDIDATES[name], seed, name)
        start = perf_counter()
        model.fit(train["text"], train["label"])
        elapsed = perf_counter() - start
        models[name] = model
        records.append({"model": name, **score_predictions(validation["label"], model.predict(validation["text"])), "fit_seconds": elapsed})
    if include_lstm:
        from train_lstm import LSTMClassifier
        model = LSTMClassifier(seed=seed, epochs=lstm_epochs)
        start = perf_counter()
        model.fit(train["text"], train["label"], validation["text"], validation["label"])
        elapsed = perf_counter() - start
        models["lstm"] = model
        records.append({"model": "lstm", **score_predictions(validation["label"], model.predict(validation["text"])), "fit_seconds": elapsed})
    priority = list(CANDIDATES) + ["lstm"]
    selected = max(records[1:], key=lambda row: (row["f1_macro"], -priority.index(row["model"])))["model"]
    return models, baseline, pd.DataFrame(records), selected


def prediction_table(model, frame: pd.DataFrame) -> pd.DataFrame:
    columns = [c for c in ["review_id", "group_id", "video_id", "domain", "translated_ko", "text", "label"] if c in frame]
    result = frame[columns].copy()
    result["prediction"] = model.predict(frame["text"])
    result["label_name"] = result["label"].map(LABEL_NAMES)
    result["prediction_name"] = result["prediction"].map(LABEL_NAMES)
    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(frame["text"])
        for i, label in enumerate(model.classes_):
            result[f"p_{label}"] = probabilities[:, i]
    elif hasattr(model, "decision_function"):
        margins = model.decision_function(frame["text"])
        for i, label in enumerate(model.classes_):
            result[f"score_{label}"] = margins[:, i]
    result["correct"] = result["label"] == result["prediction"]
    result["error_type"] = ["correct" if true == pred else f"{true}->{pred}" for true, pred in zip(result["label"], result["prediction"])]
    if isinstance(model, Pipeline):
        result["known_features"] = model.named_steps["tfidf"].transform(frame["text"]).getnnz(axis=1)
    else:
        result["known_tokens"] = model.known_tokens(frame["text"])
    return result.reset_index(drop=True)


def feature_weights(model) -> pd.DataFrame:
    """Class coefficients / NB log probabilities / tree importance, never binary weights."""
    columns = ["class_label", "class_name", "feature", "weight", "weight_type"]
    if not isinstance(model, Pipeline):
        return pd.DataFrame(columns=columns)
    classifier = model.named_steps["classifier"]
    features = model.named_steps["tfidf"].get_feature_names_out()
    if hasattr(classifier, "coef_"):
        matrix, kind = classifier.coef_, "class_coefficient"
    elif hasattr(classifier, "feature_log_prob_"):
        matrix, kind = classifier.feature_log_prob_, "class_log_probability"
    else:
        return pd.DataFrame({"class_label": "all", "class_name": "all", "feature": features,
            "weight": classifier.feature_importances_, "weight_type": "tree_importance"}).sort_values("weight").reset_index(drop=True)
    frames = [pd.DataFrame({"class_label": label, "class_name": LABEL_NAMES[int(label)],
        "feature": features, "weight": matrix[i], "weight_type": kind}) for i, label in enumerate(classifier.classes_)]
    return pd.concat(frames, ignore_index=True).sort_values(["class_label", "weight"]).reset_index(drop=True)


def write_csv(frame: pd.DataFrame, path: Path):
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _class_report(y_true, y_pred) -> pd.DataFrame:
    report = classification_report(y_true, y_pred, labels=LABELS, output_dict=True, zero_division=0)
    return pd.DataFrame([{"label": label, "label_name": LABEL_NAMES[label], **report[str(label)]} for label in LABELS])


def save_plots(predictions: pd.DataFrame, weights: pd.DataFrame, output: Path, prefix: str):
    """Keep Week06's plotting step with five classes and per-class feature weights."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from sklearn.metrics import ConfusionMatrixDisplay
    installed = {font.name for font in font_manager.fontManager.ttflist}
    font = next((f for f in ["Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "NanumGothic"] if f in installed), None)
    with plt.rc_context({"font.family": font or "DejaVu Sans", "axes.unicode_minus": False}):
        fig, ax = plt.subplots(figsize=(7.2, 5.6))
        ConfusionMatrixDisplay.from_predictions(predictions["label"], predictions["prediction"], labels=LABELS,
            display_labels=[f"{LABEL_NAMES_EN[label]} ({label})" for label in LABELS], cmap="Blues", colorbar=False, ax=ax)
        ax.set_title(f"{prefix}: held-out predictions")
        ax.tick_params(axis="x", rotation=20)
        fig.tight_layout()
        fig.savefig(output / f"{prefix}_confusion_matrix.png", dpi=160)
        plt.close(fig)
        if prefix != "validation" or weights.empty:
            return
        tops = []
        for label, group in weights.groupby("class_label", sort=False):
            group = group.sort_values("weight")
            top = pd.concat([group.head(6), group.tail(6)]).drop_duplicates("feature").copy()
            top["plot_label"] = top["feature"] if font else [f"feature {i+1:02}" for i in range(len(top))]
            tops.append(top)
        write_csv(pd.concat(tops, ignore_index=True), output / "top_features.csv")
        fig, axes = plt.subplots(len(tops), 1, figsize=(9, 4 * len(tops)), squeeze=False)
        for ax, top in zip(axes[:, 0], tops):
            ax.barh(top["plot_label"], top["weight"], color="#207b8d")
            ax.set_title(f"Class {top.iloc[0]['class_label']} / {top.iloc[0]['weight_type']}")
            ax.set_xlabel("Training-only feature weights (not causal effects)")
        fig.tight_layout()
        fig.savefig(output / "feature_weights.png", dpi=140)
        plt.close(fig)


def _versions():
    versions = {"python": platform.python_version(), "pandas": pd.__version__, "scikit_learn": sklearn.__version__}
    for package in ["numpy", "matplotlib", "xgboost", "tensorflow"]:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def run_experiment(csv_path=DEFAULT_DATA, output_dir=None, evaluate_test=False, plots=True,
                   model_names=None, include_lstm=False, lstm_epochs=20):
    """Preserve load/split/compare/predictions/save/optional-test orchestration."""
    output = Path(output_dir) if output_dir else BASE / "outputs" / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S%fZ")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"출력 폴더가 비어 있지 않습니다. 새 경로를 지정하세요: {output}")
    data, data_meta = load_data(csv_path)
    splits = split_data(data)
    models, baseline, scores, selected = compare_models(splits["train"], splits["validation"],
        model_names=model_names, include_lstm=include_lstm, lstm_epochs=lstm_epochs)
    model = models[selected]  # Same trained model; no validation refit.
    output.mkdir(parents=True, exist_ok=True)
    write_csv(scores, output / "validation_scores.csv")
    validation = prediction_table(model, splits["validation"])
    weights = feature_weights(model)
    write_csv(validation, output / "validation_predictions.csv")
    write_csv(validation.loc[~validation["correct"]], output / "validation_errors.csv")
    write_csv(weights, output / "feature_weights.csv")
    reports = [_class_report(splits["validation"]["label"], baseline.predict(splits["validation"][["text"]])).assign(model="majority_baseline")]
    for name, candidate in models.items():
        reports.append(_class_report(splits["validation"]["label"], candidate.predict(splits["validation"]["text"])).assign(model=name))
    write_csv(pd.concat(reports, ignore_index=True), output / "validation_classification_reports.csv")
    manifest = pd.concat([frame[["review_id", "group_id", "label"]].assign(split=name) for name, frame in splits.items()])
    write_csv(manifest, output / "split_assignments.csv")
    candidate_settings = {name: {"ngram_range": list(CANDIDATES[name]),
        "classifier": type(candidate.named_steps["classifier"]).__name__,
        "parameters": candidate.named_steps["classifier"].get_params()}
        for name, candidate in models.items() if isinstance(candidate, Pipeline)}
    if "lstm" in models:
        models["lstm"].save(output / "lstm")
        candidate_settings["lstm"] = models["lstm"].settings_
    metadata = {
        "data": data_meta, "data_sha256": hashlib.sha256(Path(csv_path).read_bytes()).hexdigest(),
        "seed": SEED, "split_method": "StratifiedGroupKFold: 5 folds then 4 folds",
        "split_sizes": {name: len(frame) for name, frame in splits.items()},
        "split_label_counts": {name: {str(k): int(v) for k, v in frame["label"].value_counts().sort_index().items()} for name, frame in splits.items()},
        "labels": LABELS, "label_names": LABEL_NAMES,
        "selection_metric": "validation f1_macro; ties use fixed candidate order",
        "candidate_priority": list(CANDIDATES) + ["lstm"], "candidate_settings": candidate_settings,
        "selected_model": selected,
        "selected_ngram_range": list(CANDIDATES[selected]) if selected in CANDIDATES else None,
        "beats_majority_baseline": bool(scores.loc[scores.model.eq(selected), "f1_macro"].iloc[0] > scores.iloc[0]["f1_macro"]),
        "fit_scope": "train only, including vocabulary/IDF; LSTM validation early stopping; no validation refit",
        "decision_rule": "argmax in [1,2,3,4] order; SVM decision margins",
        "test_evaluated": bool(evaluate_test), "versions": _versions(),
        "warning": "Scores depend on human GT, translation and sampled comments. Class 4 is a labeled training class, not an automatic low-confidence rejection. Probabilities are not emotion intensity.",
    }
    if plots:
        save_plots(validation, weights, output, "validation")
    if evaluate_test:
        test = prediction_table(model, splits["test"])
        write_csv(test, output / "test_predictions.csv")
        write_csv(test.loc[~test["correct"]], output / "test_errors.csv")
        baseline_pred = baseline.predict(splits["test"][["text"]])
        test_scores = pd.DataFrame([
            {"model": "majority_baseline", **score_predictions(test["label"], baseline_pred)},
            {"model": selected, **score_predictions(test["label"], test["prediction"])},
        ])
        write_csv(test_scores, output / "test_scores.csv")
        write_csv(_class_report(test["label"], test["prediction"]), output / "test_classification_reports.csv")
        metadata["test_scores"] = test_scores.to_dict(orient="records")
        metadata["test_confusion_matrix_labels_0_1_2_3_4"] = confusion_matrix(test["label"], test["prediction"], labels=LABELS).tolist()
        report = classification_report(test["label"], test["prediction"], labels=LABELS,
            target_names=[f"{LABEL_NAMES[k]} ({k})" for k in LABELS], zero_division=0, digits=4)
        (output / "test_classification_report.txt").write_text(report, encoding="utf-8")
        if plots:
            save_plots(test, weights, output, "test")
    (output / "metrics.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, scores, metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Emotion analysis: translated_ko / GT label 0~4 (기본: 검증만 평가)")
    parser.add_argument("--csv", type=Path, default=DEFAULT_DATA, help="translated_ko,label 열이 있는 UTF-8 CSV")
    parser.add_argument("--output-dir", type=Path, help="비어 있는 출력 폴더")
    parser.add_argument("--evaluate-test", action="store_true", help="선택 종료 후 고정 모델 테스트 평가")
    parser.add_argument("--no-plots", action="store_true", help="PNG 없이 CSV/JSON 저장")
    parser.add_argument("--models", nargs="+", choices=list(CANDIDATES), help="비교할 일반 모델 (기본: 전체)")
    parser.add_argument("--include-lstm", action="store_true", help="같은 분할에 LSTM 추가; requirements-lstm.txt 필요")
    parser.add_argument("--lstm-epochs", type=int, default=20, help="LSTM 최대 학습 epoch")
    args = parser.parse_args(argv)
    if args.lstm_epochs < 1:
        parser.error("--lstm-epochs는 1 이상이어야 합니다.")
    try:
        output, scores, meta = run_experiment(args.csv, args.output_dir, args.evaluate_test,
            not args.no_plots, args.models, args.include_lstm, args.lstm_epochs)
    except (ValueError, OSError, pd.errors.ParserError) as exc:
        parser.exit(1, f"입력/설정 확인: {exc}\n")
    print("[DATA]", ", ".join(meta["data"]["source_types"]))
    print("[LABELS]", LABEL_NAMES)
    if meta["data"]["label_name_mismatches"]:
        print("[GT] label_name 불일치", meta["data"]["label_name_mismatches"], "행; 정답은 숫자 label을 사용합니다.")
    print("[SPLIT]", meta["split_sizes"])
    print(scores.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("[SELECTED]", meta["selected_model"], "(validation macro-F1)")
    if args.evaluate_test:
        print("[FINAL TEST]", json.dumps(meta["test_scores"], ensure_ascii=False))
        print("[CAUTION] 테스트 결과를 보고 다시 모델을 고르면 새 독립 평가 자료가 필요합니다.")
    else:
        print("[TEST] 미평가. 비교/수정 종료 후 --evaluate-test를 사용하세요.")
    if not meta["beats_majority_baseline"]:
        print("[BASELINE] 선택 후보가 기준선 Macro-F1을 개선하지 못했습니다.")
    print("[SAVED]", output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
