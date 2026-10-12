"""Temporary five-class fixtures; no real comments or scores are shipped as data."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import train_sentiment as lab


def fixture():
    phrases = {0: "실망이다 별로다", 1: "멋지다 응원해", 2: "공연 일정 질문", 3: "좋지만 아쉽다", 4: "무슨 뜻인지 모르겠다"}
    return pd.DataFrame([
        {"comment_id": f"COMMENT{i:03d}_{label}", "group_id": f"TOPIC{i:03d}",
         "translated_ko": f"{phrases[label]} 샘플{i:03d}", "label": str(label)}
        for i in range(30) for label in lab.LABELS
    ])


class EmotionAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.csv = self.directory / "fixture.csv"
        self.raw = fixture()
        self.raw.to_csv(self.csv, index=False, encoding="utf-8-sig")
        self.data, self.info = lab.load_data(self.csv)
        self.splits = lab.split_data(self.data)

    def write_fixture(self, frame):
        path = self.directory / "changed.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def test_gt_mapping_and_negative_and_class4_preserved(self):
        self.assertEqual(lab.LABEL_NAMES, {0: "부정", 1: "긍정", 2: "중립", 3: "혼합", 4: "판단 불가"})
        self.assertEqual(self.data.label.value_counts().to_dict(), {0: 30, 1: 30, 2: 30, 3: 30, 4: 30})
        self.assertEqual(self.info["text_column"], "translated_ko")
        self.assertEqual(self.data.text.tolist(), self.raw.translated_ko.tolist())
        self.assertTrue(lab.DEFAULT_DATA.name == "comments_261010.csv")

    def test_two_column_csv_and_optional_reply_filter(self):
        minimal, _ = lab.load_data(self.write_fixture(self.raw[["translated_ko", "label"]]))
        self.assertEqual(len(minimal), 150)
        extra = self.raw.iloc[[0]].assign(comment_id="REPLY", parent_id="PARENT", comment_type="reply", translated_ko="", label="")
        top = self.raw.assign(parent_id="  ", comment_type="top_level")
        clean, info = lab.load_data(self.write_fixture(pd.concat([top, extra], ignore_index=True)))
        self.assertEqual(len(clean), 150)
        self.assertEqual(info["replies_removed"], 1)
        invalid = top.copy()
        invalid.loc[0, "comment_type"] = "reply"
        with self.assertRaisesRegex(ValueError, "일치하지"):
            lab.load_data(self.write_fixture(invalid))

    def test_missing_data_is_actionable_and_not_replaced_with_examples(self):
        for path, message in [(self.directory / "absent.csv", "데이터 파일"),
                              (self.write_fixture(self.raw.head(0)), "실제 데이터")]:
            with self.assertRaisesRegex(ValueError, message):
                lab.load_data(path)

    def test_invalid_gt_and_blank_text_rejected(self):
        for label in ["", "-1", "5", "1.0", "긍정"]:
            invalid = self.raw.copy()
            invalid.loc[0, "label"] = label
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "label은"):
                lab.load_data(self.write_fixture(invalid))
        invalid = self.raw.copy()
        invalid.loc[0, "translated_ko"] = "  "
        with self.assertRaisesRegex(ValueError, "빈 translated_ko"):
            lab.load_data(self.write_fixture(invalid))

    def test_annotation_only_row_excluded_without_changing_source(self):
        frame = self.raw.assign(label_reason="")
        memo = pd.DataFrame([{column: "" for column in frame.columns}]).assign(label_reason="검토 메모")
        path = self.write_fixture(pd.concat([frame, memo], ignore_index=True))
        before = path.read_bytes()
        clean, meta = lab.load_data(path)
        self.assertEqual(len(clean), 150)
        self.assertEqual(meta["input_rows"], 151)
        self.assertEqual(meta["annotation_rows_removed"], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_numeric_gt_used_despite_stale_metadata(self):
        frame = self.raw.assign(label_name="이전 라벨", human_label="9", is_trainable="FALSE")
        clean, meta = lab.load_data(self.write_fixture(frame))
        self.assertEqual(clean.label.tolist(), self.raw.label.astype(int).tolist())
        self.assertEqual(meta["label_name_mismatches"], 150)
        self.assertEqual(set(clean.label), {0, 1, 2, 3, 4})

    def test_emoji_punctuation_and_negation_are_preserved(self):
        text = lab.normalize_text("  안   좋다ㅠㅠ ❤ !!! ")
        self.assertEqual(text, "안 좋다ㅠㅠ ❤ !!!")
        tokens = lab.make_model((1, 2)).named_steps["tfidf"].build_analyzer()(text)
        self.assertIn("안", tokens)
        self.assertIn("❤", tokens)
        self.assertNotEqual(lab.text_key("좋다!"), lab.text_key("좋다?"))
        changed = self.raw.copy()
        changed.loc[0, "translated_ko"] = "❤❤"
        clean, _ = lab.load_data(self.write_fixture(changed))
        self.assertIn("❤❤", clean.text.tolist())
        replaced = lab.normalize_text("https://example.com @account a@b.com 010-1234-5678")
        for token in ["[URL]", "[USER]", "[EMAIL]", "[PHONE]"]:
            self.assertIn(token, replaced)

    def test_duplicate_conflicts_and_author_links_survive_dedup(self):
        frame = self.raw.copy()
        frame["author_channel_id"] = [f"AUTHOR{i:03}" for i in range(len(frame))]
        original = frame.iloc[[0]].copy()
        duplicate = original.assign(comment_id="DUPLICATE", group_id="BRIDGE", author_channel_id="BRIDGE_AUTHOR")
        another = frame.iloc[[4]].assign(comment_id="ANOTHER", translated_ko="다른 긍정 댓글", group_id="OTHER", author_channel_id="BRIDGE_AUTHOR")
        clean, meta = lab.load_data(self.write_fixture(pd.concat([frame, duplicate, another], ignore_index=True)))
        groups = clean.set_index("review_id").group_id
        self.assertEqual(groups.iloc[0], groups.loc["ANOTHER"])
        self.assertEqual(meta["duplicates_removed"], 1)
        duplicate["label"] = "2"
        with self.assertRaisesRegex(ValueError, "서로 다른 label"):
            lab.load_data(self.write_fixture(pd.concat([frame, duplicate], ignore_index=True)))

    def test_split_reproducibility_and_no_group_or_text_overlap(self):
        again = lab.split_data(self.data)
        for name, frame in self.splits.items():
            pd.testing.assert_frame_equal(frame, again[name])
            self.assertEqual(set(frame.label), set(lab.LABELS))
            for other_name, other in self.splits.items():
                if name == other_name:
                    continue
                for column in ["group_id", "review_id", "_text_key"]:
                    self.assertFalse(set(frame[column]) & set(other[column]))
        self.assertEqual(sum(len(frame) for frame in self.splits.values()), 150)

    def test_missing_class_and_single_video_group_fail_clearly(self):
        for frame in [self.raw[self.raw.label != "4"], self.raw.assign(group_id="SAME_VIDEO")]:
            with self.assertRaisesRegex(ValueError, "라벨|그룹"):
                lab.load_data(self.write_fixture(frame))

    def test_tfidf_is_train_only(self):
        model = lab.make_model()
        model.fit(self.splits["train"].text, self.splits["train"].label)
        tfidf = model.named_steps["tfidf"]
        vocab = dict(tfidf.vocabulary_)
        idf = tfidf.idf_.copy()
        model.predict(["검증전용어휘XYZ"])
        self.assertNotIn("검증전용어휘xyz", tfidf.vocabulary_)
        self.assertEqual(vocab, tfidf.vocabulary_)
        np.testing.assert_array_equal(idf, tfidf.idf_)

    def test_five_class_metrics_match_hand_calculation(self):
        scores = lab.score_predictions([0, 1, 2, 3, 4], [0, 1, 1, 3, 4])
        self.assertAlmostEqual(scores["accuracy"], 0.8)
        self.assertAlmostEqual(scores["f1_macro"], (1 + 2 / 3 + 0 + 1 + 1) / 5)
        self.assertAlmostEqual(scores["recall_macro"], 0.8)

    def test_all_classical_candidates_and_output_types(self):
        models, baseline, scores, selected = lab.compare_models(self.splits["train"], self.splits["validation"])
        self.assertEqual(len(scores), 6)
        self.assertEqual(set(models), set(lab.CANDIDATES))
        self.assertIn(selected, models)
        for name, model in models.items():
            result = lab.prediction_table(model, self.splits["validation"])
            self.assertTrue(set(result.prediction).issubset(set(lab.LABELS)))
            weights = lab.feature_weights(model)
            self.assertFalse(weights.empty)
            if name == "linear_svm":
                self.assertTrue(all(f"score_{label}" in result for label in lab.LABELS))
                self.assertNotIn("p_1", result)
            else:
                self.assertTrue(all(f"p_{label}" in result for label in lab.LABELS))
                np.testing.assert_allclose(result[[f"p_{k}" for k in lab.LABELS]].sum(axis=1), 1, atol=1e-6)

    def test_validation_does_not_predict_test_and_ties_prefer_unigram(self):
        seen = []
        original = lab.Pipeline.predict

        def audited_predict(model, text, **kwargs):
            seen.extend(list(text))
            return original(model, text, **kwargs)

        with patch.object(lab.Pipeline, "predict", audited_predict):
            _, scores, meta = lab.run_experiment(self.csv, self.directory / "validation", plots=False,
                                                 model_names=["unigram_bigram", "unigram"])
        self.assertFalse(set(seen) & set(self.splits["test"].text))
        self.assertFalse(meta["test_evaluated"])
        self.assertFalse(list((self.directory / "validation").glob("test_*")))
        self.assertEqual(meta["selected_model"], "unigram")

    def test_saved_multiclass_reports_and_non_destructive_output(self):
        out, scores, meta = lab.run_experiment(self.csv, self.directory / "final", evaluate_test=True,
                                               plots=False, model_names=["unigram", "linear_svm"])
        self.assertEqual(np.asarray(meta["test_confusion_matrix_labels_0_1_2_3_4"]).shape, (5, 5))
        self.assertEqual(len(pd.read_csv(out / "test_scores.csv")), 2)
        self.assertEqual(len(pd.read_csv(out / "validation_classification_reports.csv")), 15)
        self.assertTrue((out / "test_predictions.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(json.loads((out / "metrics.json").read_text(encoding="utf-8"))["labels"], [0, 1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, "비어 있지"):
            lab.run_experiment(self.csv, out, plots=False)

    def test_all_estimator_plot_paths(self):
        for name in ["unigram", "multinomial_nb", "xgboost"]:
            model = lab.make_model(lab.CANDIDATES[name], model_name=name)
            model.fit(self.splits["train"].text, self.splits["train"].label)
            out = self.directory / name
            out.mkdir()
            lab.save_plots(lab.prediction_table(model, self.splits["validation"]), lab.feature_weights(model), out, "validation")
            self.assertTrue((out / "validation_confusion_matrix.png").read_bytes().startswith(b"\x89PNG"))
            self.assertTrue((out / "feature_weights.png").exists())

    def test_cli_header_only_fails_with_helpful_message(self):
        with contextlib.redirect_stderr(io.StringIO()) as message, self.assertRaises(SystemExit) as result:
            lab.main(["--csv", str(self.write_fixture(self.raw.head(0)))])
        self.assertEqual(result.exception.code, 1)
        self.assertIn("실제 데이터", message.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("tensorflow"), "optional TensorFlow not installed")
    def test_lstm_public_gt_vocabulary_outputs_and_saved_model(self):
        from train_lstm import LSTMClassifier
        import tensorflow as tf
        train, val = self.splits["train"], self.splits["validation"]
        unseen = val.text + " 검증전용어휘XYZ"
        model = LSTMClassifier(epochs=1, embedding_dim=8, units=8, batch_size=16)
        model.fit(train.text, train.label, unseen, val.label)
        self.assertNotIn("검증전용어휘xyz", model.vectorizer_.get_vocabulary())
        predictions = lab.prediction_table(model, val)
        self.assertTrue(set(predictions.prediction).issubset({0, 1, 2, 3, 4}))
        np.testing.assert_allclose(predictions[[f"p_{k}" for k in lab.LABELS]].sum(axis=1), 1, atol=1e-6)
        self.assertEqual(model.known_tokens(["검증전용어휘XYZ"])[0], 0)
        self.assertTrue(lab.feature_weights(model).empty)
        out = self.directory / "lstm"
        model.save(out)
        loaded = tf.keras.models.load_model(out / "model.keras")
        self.assertEqual(loaded.output_shape[-1], 5)


if __name__ == "__main__":
    unittest.main()
