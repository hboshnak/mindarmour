# Copyright 2019 Huawei Technologies Co., Ltd
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
Adversarial defense test.
"""
import logging

import numpy as np
import pytest
from mindspore import Tensor
from mindspore import context
from mindspore import nn
from mindspore.nn.optim.momentum import Momentum

from mindarmour.adv_robustness.defenses import AdversarialDefense
from mindarmour.utils.logger import LogUtil

from tests.ut.python.utils.mock_net import Net

LOGGER = LogUtil.get_instance()
TAG = 'Ad_Test'


@pytest.mark.level0
@pytest.mark.platform_arm_ascend_training
@pytest.mark.platform_x86_ascend_training
@pytest.mark.env_card
@pytest.mark.component_mindarmour
def test_ad():
    """UT for adversarial defense."""
    num_classes = 10
    batch_size = 32

    sparse = False
    context.set_context(mode=context.GRAPH_MODE)
    context.set_context(device_target='Ascend')

    # create test data
    inputs = np.random.rand(batch_size, 1, 32, 32).astype(np.float32)
    labels = np.random.randint(num_classes, size=batch_size).astype(np.int32)
    if not sparse:
        labels = np.eye(num_classes)[labels].astype(np.float32)

    net = Net()
    loss_fn = nn.SoftmaxCrossEntropyWithLogits(sparse=sparse)
    optimizer = Momentum(learning_rate=Tensor(np.array([0.001], np.float32)),
                         momentum=0.9,
                         params=net.trainable_params())

    ad_defense = AdversarialDefense(net, loss_fn=loss_fn, optimizer=optimizer)
    LOGGER.set_level(logging.DEBUG)
    LOGGER.debug(TAG, '--start adversarial defense--')
    loss = ad_defense.defense(inputs, labels)
    LOGGER.debug(TAG, '--end adversarial defense--')
    assert np.any(loss >= 0.0)
