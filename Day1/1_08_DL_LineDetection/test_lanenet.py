#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 18-5-23 上午11:33
# @Author  : MaybeShewill-CV
# @Site    : https://github.com/MaybeShewill-CV/lanenet-lane-detection
# @File    : test_lanenet.py
# @IDE: PyCharm Community Edition
"""
test LaneNet model on single image
"""
import argparse
import os.path as ops
import time

import cv2
import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf

from lanenet_model import lanenet
from lanenet_model import lanenet_postprocess
from local_utils.config_utils import parse_config_utils
from local_utils.log_util import init_logger

# from . import lanenet
# from . import lanenet_postprocess
# from . import parse_config_utils
# from . import init_logger

CFG = parse_config_utils.lanenet_cfg
LOG = init_logger.get_logger(log_file_name_prefix='lanenet_test')


def init_args():
    """

    :return:
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--image_path', type=str, help='The image path or the src image save dir')
    parser.add_argument('--weights_path', type=str, help='The model weights path')

    return parser.parse_args()


def args_str2bool(arg_value):
    """

    :param arg_value:
    :return:
    """
    if arg_value.lower() in ('yes', 'true', 't', 'y', '1'):
        return True

    elif arg_value.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Unsupported value encountered.')


def minmax_scale(input_arr):
    """

    :param input_arr:
    :return:
    """
    min_val = np.min(input_arr)
    max_val = np.max(input_arr)

    output_arr = (input_arr - min_val) * 255.0 / (max_val - min_val)

    return output_arr


def test_lanenet(image_path, weights_path):
    """

    :param image_path:
    :param weights_path:
    :return:
    """
    assert ops.exists(image_path), '{:s} not exist'.format(image_path)

    LOG.info('Start reading image and preprocessing')
    t_start = time.time()
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    # image_vis = image
    image_vis = cv2.resize(image, (512, 256), interpolation=cv2.INTER_LINEAR)
    image = cv2.resize(image, (512, 256), interpolation=cv2.INTER_LINEAR)
    image = image / 127.5 - 1.0
    LOG.info('Image load complete, cost time: {:.5f}s'.format(time.time() - t_start))

    input_tensor = tf.placeholder(dtype=tf.float32, shape=[1, 256, 512, 3], name='input_tensor')

    net = lanenet.LaneNet(phase='test', cfg=CFG)
    binary_seg_ret, instance_seg_ret = net.inference(input_tensor=input_tensor, name='LaneNet')

    postprocessor = lanenet_postprocess.LaneNetPostProcessor(cfg=CFG)

    # Set sess configuration
    sess_config = tf.ConfigProto()
    sess_config.gpu_options.per_process_gpu_memory_fraction = CFG.GPU.GPU_MEMORY_FRACTION
    sess_config.gpu_options.allow_growth = CFG.GPU.TF_ALLOW_GROWTH
    sess_config.gpu_options.allocator_type = 'BFC'

    sess = tf.Session(config=sess_config)

    # define moving average version of the learned variables for eval
    with tf.variable_scope(name_or_scope='moving_avg'):
        variable_averages = tf.train.ExponentialMovingAverage(
            CFG.SOLVER.MOVING_AVE_DECAY)
        variables_to_restore = variable_averages.variables_to_restore()

    # define saver
    saver = tf.train.Saver(variables_to_restore)

    with sess.as_default():
        saver.restore(sess=sess, save_path=weights_path)

        t_start = time.time()
        loop_times = 1 #500
        for i in range(loop_times):
            binary_seg_image, instance_seg_image = sess.run(
                [binary_seg_ret, instance_seg_ret],
                feed_dict={input_tensor: [image]}
            )
        t_cost = time.time() - t_start
        t_cost /= loop_times
        LOG.info('Single imgae inference cost time: {:.5f}s'.format(t_cost))

        postprocess_result = postprocessor.postprocess(
            binary_seg_result=binary_seg_image[0],
            instance_seg_result=instance_seg_image[0],
            source_image=image_vis
        )
        mask_image = postprocess_result['mask_image']

        for i in range(CFG.MODEL.EMBEDDING_FEATS_DIMS):
            instance_seg_image[0][:, :, i] = minmax_scale(instance_seg_image[0][:, :, i])
        embedding_image = np.array(instance_seg_image[0], np.uint8)

        plt.figure('mask_image')
        plt.imshow(mask_image[:, :, (2, 1, 0)])
        plt.savefig("./result/mask_image.png")
        plt.figure('src_image')
        plt.imshow(image_vis[:, :, (2, 1, 0)])
        plt.savefig("./result/src_image.png")
        plt.figure('instance_image')
        plt.imshow(embedding_image[:, :, (2, 1, 0)])
        plt.savefig("./result/instance_image.png")
        plt.figure('binary_image')
        plt.imshow(binary_seg_image[0] * 255, cmap='gray')
        plt.savefig("./result/binary_image.png")


        mask_gray = cv2.cvtColor(mask_image, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(mask_gray, 10, 255, cv2.THRESH_BINARY)
        mask_inv = cv2.bitwise_not(mask)
        mask_image = cv2.bitwise_and(mask_image, mask_image, mask=mask)
        image_vis = cv2.bitwise_and(image_vis, image_vis, mask=mask_inv)
        result = cv2.add(mask_image, image_vis)
        cv2.imshow('result', result)
        cv2.waitKey(0)
        plt.savefig("./result/result.png")

    sess.close()

    return


if __name__ == '__main__':
    """
    test code
    """
    # init args
    args = init_args()

    test_lanenet(args.image_path, args.weights_path)

    # a = cv2.imread("./data/tusimple_test_image/0.jpg")
    # cv2.imshow("a", a)
    # cv2.waitKey(0)

    # test_lanenet("./model/tusimple_lanenet", "./data/tusimple_test_image/0.jpg")



# python tools/test_lanenet.py --weights_path /PATH/TO/YOUT/CKPT_FILE_PATH  --image_path ./data/tusimple_test_image/0.jpg

# python test_lanenet.py --weights_path ./model/tusimple_lanenet/tusimple_lanenet.ckpt --image_path ./data/tusimple_test_image/0.jpg
# python test_lanenet.py --weights_path ./model/tusimple_lanenet/tusimple_lanenet.ckpt --image_path ./data/335.png
# python test_lanenet.py --weights_path ./model/tusimple_lanenet/tusimple_lanenet.ckpt --image_path ./data/train_set/clips/0313-1/120/20.jpg
# python test_lanenet.py --weights_path ./model/tusimple_lanenet/tusimple_lanenet.ckpt --image_path ./data/test_set/clips/0531/1495489721550725241/20.jpg
