"""Optional LSTM using the SAME Week06 splits; public GT stays 0..4, including negative."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


class LSTMClassifier:
    def __init__(self, seed=42, epochs=20, max_tokens=10000, max_length=128,
                 embedding_dim=64, units=64, batch_size=32, patience=3):
        self.seed = seed
        self.epochs = epochs
        self.max_tokens = max_tokens
        self.max_length = max_length
        self.embedding_dim = embedding_dim
        self.units = units
        self.batch_size = batch_size
        self.patience = patience

    @staticmethod
    def _tensorflow():
        try:
            import tensorflow as tf
        except ImportError as exc:
            raise ValueError("LSTM 실행에는 TensorFlow가 필요합니다. requirements-lstm.txt를 설치하세요.") from exc
        return tf

    @staticmethod
    def _tokenized(texts):
        import re
        from train_sentiment import TOKEN_PATTERN
        return np.asarray([" ".join(re.findall(TOKEN_PATTERN, str(text).lower())) for text in texts], dtype=str)

    def _dataset(self, tf, X, y=None, training=False):
        dataset = tf.data.Dataset.from_tensor_slices(X if y is None else (X, y))
        if training:
            dataset = dataset.shuffle(len(X), seed=self.seed, reshuffle_each_iteration=True)
        options = tf.data.Options()
        options.threading.private_threadpool_size = 1
        options.threading.max_intra_op_parallelism = 1
        return dataset.batch(self.batch_size).with_options(options)

    def fit(self, texts, labels, validation_texts, validation_labels):
        from train_sentiment import LABELS
        tf = self._tensorflow()
        tf.keras.utils.set_random_seed(self.seed)
        self.classes_ = np.asarray(LABELS)
        train_tokens = self._tokenized(texts)
        validation_tokens = self._tokenized(validation_texts)
        lengths = [max(1, len(text.split())) for text in train_tokens]
        self.sequence_length_ = min(self.max_length, max(1, int(np.ceil(np.percentile(lengths, 95)))))
        self.vectorizer_ = tf.keras.layers.TextVectorization(max_tokens=self.max_tokens,
            standardize=None, split="whitespace", output_mode="int", output_sequence_length=self.sequence_length_)
        # Adapt only on train; validation-only words remain OOV.
        self.vectorizer_.adapt(self._dataset(tf, train_tokens))
        train_X = self.vectorizer_(tf.constant(train_tokens)).numpy()
        validation_X = self.vectorizer_(tf.constant(validation_tokens)).numpy()
        self.model_ = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(self.sequence_length_,), dtype="int64"),
            tf.keras.layers.Embedding(input_dim=len(self.vectorizer_.get_vocabulary()), output_dim=self.embedding_dim, mask_zero=True),
            tf.keras.layers.LSTM(self.units), tf.keras.layers.Dropout(0.3),
            tf.keras.layers.Dense(len(LABELS), activation="softmax"),
        ])
        self.model_.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
        training = self._dataset(tf, train_X, np.asarray(labels, dtype=int), training=True)
        validation = self._dataset(tf, validation_X, np.asarray(validation_labels, dtype=int))
        history = self.model_.fit(training, validation_data=validation, epochs=self.epochs, verbose=0, shuffle=False,
            callbacks=[tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=self.patience, restore_best_weights=True)])
        self.history_ = pd.DataFrame(history.history)
        self.history_.insert(0, "epoch", np.arange(1, len(self.history_) + 1))
        self.settings_ = {
            "seed": self.seed, "max_epochs": self.epochs, "epochs_run": len(self.history_),
            "max_tokens": self.max_tokens, "vocabulary_size": len(self.vectorizer_.get_vocabulary()),
            "sequence_length": self.sequence_length_, "length_policy": "train p95 capped at max_length",
            "max_length": self.max_length, "embedding_dim": self.embedding_dim,
            "units": self.units, "batch_size": self.batch_size, "patience": self.patience,
            "early_stopping": "validation loss; restore_best_weights=True",
            "label_mapping": "public and internal [0,1,2,3,4]; 0 is negative",
        }
        return self

    def predict_proba(self, texts):
        tf = self._tensorflow()
        X = self.vectorizer_(tf.constant(self._tokenized(texts))).numpy()
        return self.model_.predict(self._dataset(tf, X), verbose=0)

    def predict(self, texts):
        return self.classes_[np.argmax(self.predict_proba(texts), axis=1)]

    def known_tokens(self, texts):
        tf = self._tensorflow()
        encoded = self.vectorizer_(tf.constant(self._tokenized(texts))).numpy()
        return (encoded > 1).sum(axis=1)  # 0 padding, 1 OOV.

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.model_.save(directory / "model.keras")
        (directory / "vocabulary.json").write_text(json.dumps(self.vectorizer_.get_vocabulary(), ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "settings.json").write_text(json.dumps(self.settings_, ensure_ascii=False, indent=2), encoding="utf-8")
        self.history_.to_csv(directory / "training_history.csv", index=False, encoding="utf-8-sig")


def main(argv=None):
    import sys
    from train_sentiment import main as run
    args = list(sys.argv[1:] if argv is None else argv)
    if "--include-lstm" not in args:
        args.append("--include-lstm")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
