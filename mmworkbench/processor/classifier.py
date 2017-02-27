# coding=utf-8
"""
This module contains the domain classifier component.
"""

from __future__ import unicode_literals
from builtins import object

import logging
import os

from sklearn.externals import joblib

from ..exceptions import ClassifierLoadError, FileNotFoundError
from ..core import Query

logger = logging.getLogger(__name__)

from ..models import ModelConfig

class Classifier(object):
    DEFAULT_CONFIG = None
    MODEL_CLASS = None

    def __init__(self, resource_loader):
        """Initializes a classifier

        Args:
            resource_loader (ResourceLoader): An object which can load resources for the classifier
        """
        self._resource_loader = resource_loader
        self._model = None  # will be set when model is fit or loaded

    def fit(self, model_type=None, features=None, params_grid=None, cv=None, queries=None):
        """Trains the model

        Args:
            model_type (str): The type of model to use. If omitted, the default model type will
                be used.
            features (dict): If omitted, the default features for the model type will be used.
            params_grid (dict): If omitted the default params will be used
            cv (None, optional): Description
            queries (list of ProcessedQuery): The labeled queries to use as training data

        """
        raise NotImplementedError('Subclasses must implement this method')

    def predict(self, query):
        """Predicts a domain for the specified query

        Args:
            query (Query): The input query

        Returns:
            str: the predicted domain
        """
        raise NotImplementedError('Subclasses must implement this method')

    def predict_proba(self, query):
        """Generates multiple hypotheses and returns their associated probabilities

        Args:
            query (Query): The input query

        Returns:
            list: a list of tuples of the form (str, float) grouping predictions and their
                probabilities
        """
        raise NotImplementedError('Subclasses must implement this method')

    def evaluate(self, use_blind=False):
        """Evaluates the model on the specified data

        Returns:
            TYPE: Description
        """
        raise NotImplementedError('Subclasses must implement this method')

    def _get_model_class(self, model_type):
        return self.MODEL_CLASS

    def get_model_config(self, model_type=None, features=None, params_grid=None, cv=None,
                         model_name=None):
        model_name = model_name or self.DEFAULT_CONFIG['default_model']
        model_config = self.DEFAULT_CONFIG['models'][model_name]
        model_type = model_type or model_config['model_type']
        features = features or model_config['features']
        params_grid = params_grid or model_config['params_grid']
        cv = cv or model_config['cv']
        return ModelConfig(model_type, None, params_grid, features, cv)

    def dump(self, model_path):
        """Persists the model to disk.

        Args:
            model_path (str): The location on disk where the model should be stored

        """
        # make directory if necessary
        folder = os.path.dirname(model_path)
        if not os.path.isdir(folder):
            os.makedirs(folder)

        joblib.dump(self._model, model_path)

    def load(self, model_path):
        """Loads the model from disk

        Args:
            model_path (str): The location on disk where the model is stored

        """
        try:
            self._model = joblib.load(model_path)
        except FileNotFoundError:
            msg = 'Unable to load {}. Pickle file not found at {!r}'
            raise ClassifierLoadError(msg.format(self.__class__.__name__, model_path))


class StandardClassifier(Classifier):
    """The Standard classifier is a generic base for classification of strings.

    Attributes:
        DEFAULT_CONFIG (dict): The default configuration
        MODEL_CLASS (type): The the class of the underlying model.
    """
    DEFAULT_CONFIG = None
    MODEL_CLASS = None

    def fit(self, model_type=None, features=None, params_grid=None, cv=None, queries=None):
        """Trains the model

        Args:
            model_type (str): The type of model to use. If omitted, the default model type will
                be used.
            features (dict): If omitted, the default features for the model type will be used.
            params_grid (dict): If omitted the default params will be used
            cv (None, optional): Description
            queries (list of ProcessedQuery): The labeled queries to use as training data

        """
        queries, classes = self._get_queries_and_classes(queries)
        config = self.get_model_config(model_type, features, params_grid, cv)

        model_class = self._get_model_class(model_type)
        model = model_class(config)
        gazetteers = self._resource_loader.get_gazetteers()
        model.register_resources(gazetteers=gazetteers)
        model.fit(queries, classes)
        self._model = model

    def predict(self, query):
        """Predicts a domain for the specified query

        Args:
            query (Query): The input query

        Returns:
            str: the predicted domain
        """
        if not isinstance(query, Query):
            query = self._resource_loader.query_factory.create_query(query)
        return self._model.predict([query])[0]

    def predict_proba(self, query):
        """Generates multiple hypotheses and returns their associated probabilities

        Args:
            query (Query): The input query

        Returns:
            list: a list of tuples of the form (str, float) grouping predictions and their
                probabilities
        """
        return self._model.predict_proba([query])[0]

    def evaluate(self, use_blind=False):
        """Evaluates the model on the specified data

        Returns:
            TYPE: Description
        """
        raise NotImplementedError('Still need to implement this. Sorry!')

    def load(self, model_path):
        super().load(model_path)
        if self._model:
            gazetteers = self._resource_loader.get_gazetteers()
            self._model.register_resources(gazetteers=gazetteers)

    def _get_queries_and_classes(self, queries=None):
        """Returns the set of queries and their classes to train on

        Args:
            queries (list): A list of ProcessedQuery objects to train. If not passed, the default
                training set will be loaded.

        """
        raise NotImplementedError('Subclasses must implement this method')
