# -*- coding: utf-8 -*-
#
# Copyright (c) 2015 Cisco Systems, Inc. and others.  All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This module contains the CRF entity recognizer.
"""
from distutils.util import strtobool
import logging
import os

import numpy as np

from .taggers import Tagger, extract_sequence_features
from .pytorch_crf import CRFModel

logger = logging.getLogger(__name__)

ZERO = 1e-20

STORE_CRF_FEATURES_IN_MEMORY = bool(
    strtobool(os.environ.get("MM_CRF_FEATURES_IN_MEMORY", "1").lower())
)


class CRFTagger(Tagger):
    """A Conditional Random Fields model."""

    def fit(self, X, y):
        self._clf.fit(X, y)
        return self

    # TODO: Refactor to move initialization into init() or setup_model()
    def set_params(self, **parameters):
        self._clf = CRFModel()
        self._clf.set_params(**parameters)
        return self

    def get_params(self, deep=True):
        return self._clf.get_params()

    def predict(self, X, dynamic_resource=None):
        return self._clf.predict(X)

    def predict_proba(self, examples, config, resources):
        """
        Args:
            examples (list of mindmeld.core.Query): a list of queries to predict on
            config (ModelConfig): The ModelConfig which may contain information used for feature
                                  extraction
            resources (dict): Resources which may be used for this model's feature extraction

        Returns:
            list of tuples of (mindmeld.core.QueryEntity): a list of predicted labels \
             with confidence scores
        """
        X, _, _ = self.extract_features(examples, config, resources, in_memory=True)
        seq = self._clf.predict(X)
        marginals_dict = self._clf.predict_marginals(X)
        marginal_tuples = []
        for query_index, query_seq in enumerate(seq):
            query_marginal_tuples = []
            for i, tag in enumerate(query_seq):
                query_marginal_tuples.append([tag, marginals_dict[query_index][i][tag]])
            marginal_tuples.append(query_marginal_tuples)
        return marginal_tuples

    def predict_proba_distribution(self, examples, config, resources):
        """
        Args:
            examples (list of mindmeld.core.Query): a list of queries to predict on
            config (ModelConfig): The ModelConfig which may contain information used for feature
                                  extraction
            resources (dict): Resources which may be used for this model's feature extraction

        Returns:
            list of list of ((list of str) and (list of float)): a list of predicted labels \
             with confidence scores
        """
        X, _, _ = self.extract_features(examples, config, resources, in_memory=True)
        seq = self._clf.predict(X)
        marginals_dict = self._clf.predict_marginals(X)
        predictions = []
        tag_maps = []
        for query_index, query_seq in enumerate(seq):
            tags = []
            preds = []
            for i, _ in enumerate(query_seq):
                tags.append(list(marginals_dict[query_index][i].keys()))
                preds.append(list(marginals_dict[query_index][i].values()))
            tag_maps.extend(tags)
            predictions.extend(preds)
        return [[tag_maps, predictions]]

    def extract_features(self,
                         examples,
                         config,
                         resources,
                         y=None,
                         fit=False,
                         in_memory=STORE_CRF_FEATURES_IN_MEMORY):
        """Transforms a list of examples into a feature matrix.

        Args:
            examples (list of mindmeld.core.Query): a list of queries
            config (ModelConfig): The ModelConfig which may contain information used for feature
                                  extraction
            resources (dict): Resources which may be used for this model's feature extraction

        Returns:
            (list of list of str): features in CRF suite format
        """
        # Extract features and classes
        feats = []
        # The FileBackedList now has support for indexing but it still loads the list
        # eventually into memory cause of the scikit-learn train_test_split function.
        # Created https://github.com/cisco/mindmeld/issues/417 for this.
        if not in_memory:
            logger.warning("PyTorch CRF does not currently support STORE_CRF_FEATURES_IN_MEMORY. This may be fixed in "
                           "a future release.")
        for _, example in enumerate(examples):
            feats.append(self.extract_example_features(example, config, resources))
        X = self._preprocess_data(feats, fit)
        return X, y, None

    @staticmethod
    def extract_example_features(example, config, resources):
        """Extracts feature dicts for each token in an example.

        Args:
            example (mindmeld.core.Query): A query.
            config (ModelConfig): The ModelConfig which may contain information used for feature \
                                  extraction.
            resources (dict): Resources which may be used for this model's feature extraction.

        Returns:
            list[dict]: Features.
        """
        return extract_sequence_features(
            example, config.example_type, config.features, resources
        )

    def _preprocess_data(self, X, fit=False):
        """Converts data into formats of CRF suite.

        Args:
            X (list of list of dict): features of an example
            fit (bool, optional): True if processing data at fit time, false for predict time.

        Returns:
            (list of list of str): features in CRF suite format
        """
        if fit:
            self._feat_binner.fit(X)

        # We want to use a list for in-memory and a LineGenerator for disk based
        new_X = X.__class__()
        # Maintain append code structure to make sure it supports in-memory and FileBackedList()
        for feat_seq in self._feat_binner.transform(X):
            new_X.append(feat_seq)
        return new_X

    def setup_model(self, config):
        self._feat_binner = FeatureBinner()

    @property
    def is_serializable(self):
        return False

    def dump(self, path):
        best_model_save_path = os.path.join(os.path.split(path)[0], "best_crf_wts.pt")
        self._clf.save_best_weights_path(best_model_save_path)

    def load(self, path):
        best_model_save_path = os.path.join(os.path.split(path)[0], "best_crf_wts.pt")
        self._clf.build_params(*self.get_torch_encoder().get_feats_and_classes())
        self._clf.load_best_weights_path(best_model_save_path)

    def get_torch_encoder(self):
        return self._clf.get_encoder()

    def set_torch_encoder(self, encoder):
        self._clf.set_encoder(encoder)


# Feature extraction for CRF


class FeatureMapper:
    """
    Mapper for one feature to map numerical values to corresponding bins which are generated
    by the mean and standard deviation of this feature.

    The size and number of bins are decided by num_std and size_std. For example, say
    num_std = 2 and size_std = 0.5, then the bins would look like:

    * bucket 0: (-INF, mean - std * 2)
    * bucket 1: [mean - std * 2, mean - std * 1.5)
    * bucket 2: [mean - std * 1.5, mean - std * 1)
    * ...
    * bucket 8: [mean + std * 1.5, mean + std * 2)
    * bucket 9: [mean + std * 2, INF)

    Attributes:
        _num_std (int): number of standard deviations to generate the bins
        _size_std (float): size of each bin in standard deviation
    """

    def __init__(self, num_std=2, size_std=0.5):
        self._num_std = num_std
        self._size_std = size_std

        self.values = []
        self.std = None
        self.mean = None
        self._std_bins = []

    def add_value(self, value):
        """Collect values for this feature.

        Args:
            value (numeric): A numeric value
        """
        self.values.append(value)

    def fit(self):
        """Calculate statistics and then create the bins."""
        self.std = np.std(self.values)
        self.mean = np.mean(self.values)

        range_start = self.mean - self.std * self._num_std
        num_bin = 2 * int(self._num_std / self._size_std)
        bins = [range_start]

        while num_bin > 0 and self.std > ZERO:
            range_start += self.std * self._size_std
            bins.append(range_start)
            num_bin -= 1
        self._std_bins = np.array(bins)

    def map_bucket(self, value):
        """
        Get corresponding bucket number for this value.

        Args:
           value (float): numerical value of this feature
        """
        return np.searchsorted(self._std_bins, value)


class FeatureBinner:
    """
    Class to convert features with numerical values to categorical values.
    """

    def __init__(self):
        self.features = {}

    def fit(self, X_train):
        """
        Create and fit FeatureMapper for numerical features.

        Args:
            X_train (list of list of dict): training data
        """
        for sentence in X_train:
            for word in sentence:
                for feat_name, feat_value in word.items():
                    self._collect_feature(feat_name, feat_value)

        for mapper in self.features.values():
            mapper.fit()

    def transform(self, X_train):
        """
        Convert numerical values to categorical values.

        Args:
            X_train (list of list of dict): training data
        """
        new_X_train = []
        for sentence in X_train:
            new_sentence = []
            for word in sentence:
                new_word = {}
                for feat_name, feat_value in word.items():
                    new_feats = self._map_feature(feat_name, feat_value)
                    if new_feats:
                        new_word.update(new_feats)
                new_sentence.append(new_word)
            new_X_train.append(new_sentence)
        return new_X_train

    def fit_transform(self, X_train):
        """
        Run fit and transform at once.

        Args:
            X_train (list of list of dict): training data
        """
        self.fit(X_train)
        return self.transform(X_train)

    def _collect_feature(self, feat_name, feat_value):
        """
        Collect numerical feature values to fit corresponding mapper.

        Args:
            feat_name (str): feature name
            feat_value (any): feature value
        """
        try:
            feat_value = float(feat_value)
        except ValueError:
            # Skip collection of non numerical features
            return
        mapper = self.features.get(feat_name, FeatureMapper())
        mapper.feat_name = feat_name
        mapper.add_value(feat_value)

        self.features[feat_name] = mapper

    def _map_feature(self, feat_name, feat_value):
        """
        Map numerical feature values to categorical values.

        Args:
            feat_name (str): feature name
            feat_value (any): feature value
        """
        try:
            feat_value = float(feat_value)
        except ValueError:
            # Don't do bucketing of non numerical features
            return {feat_name: feat_value}
        if feat_name not in self.features:
            return {feat_name: feat_value}

        mapper = self.features[feat_name]
        new_feat_value = mapper.map_bucket(feat_value)
        return {feat_name: new_feat_value}
