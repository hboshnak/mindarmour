# Copyright 2020 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Membership Inference
"""

import numpy as np

import mindspore as ms
from mindspore.train import Model
from mindspore.dataset.engine import Dataset
from mindspore import Tensor
from mindarmour.diff_privacy.evaluation.attacker import get_attack_model
from mindarmour.utils.logger import LogUtil

LOGGER = LogUtil.get_instance()
TAG = "MembershipInference"


def _eval_info(pred, truth, option):
    """
    Calculate the performance according to pred and truth.

    Args:
        pred (numpy.ndarray): Predictions for each sample.
        truth (numpy.ndarray): Ground truth for each sample.
        option(str): Type of evaluation indicators; Possible
            values are 'precision', 'accuracy' and 'recall'.

    Returns:
        float32, Calculated evaluation results.

    Raises:
        ValueError, size of parameter pred or truth is 0.
        ValueError, value of parameter option must be in ["precision", "accuracy", "recall"].
    """
    if pred.size == 0 or truth.size == 0:
        msg = "Size of pred or truth is 0."
        LOGGER.error(TAG, msg)
        raise ValueError(msg)

    if option == "accuracy":
        count = np.sum(pred == truth)
        return count / len(pred)
    if option == "precision":
        count = np.sum(pred & truth)
        if np.sum(pred) == 0:
            return -1
        return count / np.sum(pred)
    if option == "recall":
        count = np.sum(pred & truth)
        if np.sum(truth) == 0:
            return -1
        return count / np.sum(truth)

    msg = "The metric value {} is undefined.".format(option)
    LOGGER.error(TAG, msg)
    raise ValueError(msg)


def _softmax_cross_entropy(logits, labels):
    """
    Calculate the SoftmaxCrossEntropy result between logits and labels.

    Args:
        logits (numpy.ndarray): Numpy array of shape(N, C).
        labels (numpy.ndarray): Numpy array of shape(N, )

    Returns:
        numpy.ndarray: Numpy array of shape(N, ), containing loss value for each vector in logits.
    """
    labels = np.eye(logits.shape[1])[labels].astype(np.int32)
    logits = np.exp(logits) / np.sum(np.exp(logits), axis=1, keepdims=True)
    return -1*np.sum(labels*np.log(logits), axis=1)


class MembershipInference:
    """
    Evaluation proposed by Shokri, Stronati, Song and Shmatikov is a grey-box attack.
    The attack requires obtain loss or logits results of training samples.

    References: `Reza Shokri, Marco Stronati, Congzheng Song, Vitaly Shmatikov.
    Membership Inference Attacks against Machine Learning Models. 2017.
    <https://arxiv.org/abs/1610.05820v2>`_

    Args:
        model (Model): Target model.

    Examples:
        >>> train_1, train_2 are non-overlapping datasets from training dataset of target model.
        >>> test_1, test_2 are non-overlapping datasets from test dataset of target model.
        >>> We use train_1, test_1 to train attack model, and use train_2, test_2 to evaluate attack model.
        >>> model = Model(network=net, loss_fn=loss, optimizer=opt, metrics={'acc', 'loss'})
        >>> inference_model = MembershipInference(model)
        >>> config = [{"method": "KNN", "params": {"n_neighbors": [3, 5, 7]}}]
        >>> inference_model.train(train_1, test_1, config)
        >>> metrics = ["precision", "recall", "accuracy"]
        >>> result = inference_model.eval(train_2, test_2, metrics)

    Raises:
        TypeError: If type of model is not mindspore.train.Model.
    """

    def __init__(self, model):
        if not isinstance(model, Model):
            msg = "Type of parameter 'model' must be Model, but got {}.".format(type(model))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        self.model = model
        self.method_list = ["knn", "lr", "mlp", "rf"]
        self.attack_list = []

    def train(self, dataset_train, dataset_test, attack_config):
        """
        Depending on the configuration, use the incoming data set to train the attack model.
        Save the attack model to self.attack_list.

        Args:
            dataset_train (mindspore.dataset): The training dataset for the target model.
            dataset_test (mindspore.dataset): The test set for the target model.
            attack_config (list): Parameter setting for the attack model. The format is
                [{"method": "knn", "params": {"n_neighbors": [3, 5, 7]}},
                 {"method": "lr", "params": {"C": np.logspace(-4, 2, 10)}}].
                The support methods list is in self.method_list, and the params of each method
                must within the range of changeable parameters. Tips of params implement
                can be found in
                "https://scikit-learn.org/0.16/modules/generated/sklearn.grid_search.GridSearchCV.html".

        Raises:
            KeyError: If each config in attack_config doesn't have keys {"method", "params"}
            ValueError: If the method(case insensitive) in attack_config is not in ["lr", "knn", "rf", "mlp"].
        """
        if not isinstance(dataset_train, Dataset):
            msg = "Type of parameter 'dataset_train' must be Dataset, but got {}".format(type(dataset_train))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        if not isinstance(dataset_test, Dataset):
            msg = "Type of parameter 'test_train' must be Dataset, but got {}".format(type(dataset_train))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        if not isinstance(attack_config, list):
            msg = "Type of parameter 'attack_config' must be list, but got {}.".format(type(attack_config))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        for config in attack_config:
            if not isinstance(config, dict):
                msg = "Type of each config in 'attack_config' must be dict, but got {}.".format(type(config))
                LOGGER.error(TAG, msg)
                raise TypeError(msg)
            if {"params", "method"} != set(config.keys()):
                msg = "Each config in attack_config must have keys 'method' and 'params'," \
                      "but your key value is {}.".format(set(config.keys()))
                LOGGER.error(TAG, msg)
                raise KeyError(msg)
            if str.lower(config["method"]) not in self.method_list:
                msg = "Method {} is not support.".format(config["method"])
                LOGGER.error(TAG, msg)
                raise ValueError(msg)

        features, labels = self._transform(dataset_train, dataset_test)
        for config in attack_config:
            self.attack_list.append(get_attack_model(features, labels, config))

    def eval(self, dataset_train, dataset_test, metrics):
        """
        Evaluate the different privacy of the target model.
        Evaluation indicators shall be specified by metrics.

        Args:
            dataset_train (mindspore.dataset): The training dataset for the target model.
            dataset_test (mindspore.dataset): The test dataset for the target model.
            metrics (Union[list, tuple]): Evaluation indicators. The value of metrics
                must be in ["precision", "accuracy", "recall"]. Default: ["precision"].

        Returns:
            list, Each element contains an evaluation indicator for the attack model.
        """
        if not isinstance(dataset_train, Dataset):
            msg = "Type of parameter 'dataset_train' must be Dataset, but got {}".format(type(dataset_train))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        if not isinstance(dataset_test, Dataset):
            msg = "Type of parameter 'test_train' must be Dataset, but got {}".format(type(dataset_train))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        if not isinstance(metrics, (list, tuple)):
            msg = "Type of parameter 'config' must be Union[list, tuple], but got {}.".format(type(metrics))
            LOGGER.error(TAG, msg)
            raise TypeError(msg)

        metrics = set(metrics)
        metrics_list = {"precision", "accuracy", "recall"}
        if not metrics <= metrics_list:
            msg = "Element in 'metrics' must be in {}, but got {}.".format(metrics_list, metrics)
            LOGGER.error(TAG, msg)
            raise ValueError(msg)

        result = []
        features, labels = self._transform(dataset_train, dataset_test)
        for attacker in self.attack_list:
            pred = attacker.predict(features)
            item = {}
            for option in metrics:
                item[option] = _eval_info(pred, labels, option)
            result.append(item)
        return result

    def _transform(self, dataset_train, dataset_test):
        """
        Generate corresponding loss_logits feature and new label, and return after shuffle.

        Args:
            dataset_train: The training set for the target model.
            dataset_test: The test set for the target model.

        Returns:
            - numpy.ndarray, Loss_logits features for each sample. Shape is (N, C).
                N is the number of sample. C = 1 + dim(logits).
            - numpy.ndarray, Labels for each sample, Shape is (N,).
        """
        features_train, labels_train = self._generate(dataset_train, 1)
        features_test, labels_test = self._generate(dataset_test, 0)
        features = np.vstack((features_train, features_test))
        labels = np.hstack((labels_train, labels_test))
        shuffle_index = np.array(range(len(labels)))
        np.random.shuffle(shuffle_index)
        features = features[shuffle_index]
        labels = labels[shuffle_index]
        return features, labels

    def _generate(self, dataset_x, label):
        """
        Return a loss_logits features and labels for training attack model.

        Args:
            dataset_x (mindspore.dataset): The dataset to be generate.
            label (int32): Whether dataset_x belongs to the target model.

        Returns:
            - numpy.ndarray, Loss_logits features for each sample. Shape is (N, C).
                N is the number of sample. C = 1 + dim(logits).
            - numpy.ndarray, Labels for each sample, Shape is (N,).
        """
        loss_logits = np.array([])
        for batch in dataset_x.create_dict_iterator():
            batch_data = Tensor(batch['image'], ms.float32)
            batch_labels = batch['label'].astype(np.int32)
            batch_logits = self.model.predict(batch_data).asnumpy()
            batch_loss = _softmax_cross_entropy(batch_logits, batch_labels)

            batch_feature = np.hstack((batch_loss.reshape(-1, 1), batch_logits))
            if loss_logits.size == 0:
                loss_logits = batch_feature
            else:
                loss_logits = np.vstack((loss_logits, batch_feature))

        if label == 1:
            labels = np.ones(len(loss_logits), np.int32)
        elif label == 0:
            labels = np.zeros(len(loss_logits), np.int32)
        else:
            msg = "The value of label must be 0 or 1, but got {}.".format(label)
            LOGGER.error(TAG, msg)
            raise ValueError(msg)
        return loss_logits, labels