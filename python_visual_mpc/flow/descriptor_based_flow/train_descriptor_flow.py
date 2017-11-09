# Copyright 2016 The TensorFlow Authors All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Code for training the prediction model."""
import os
import numpy as np
import tensorflow as tf
import imp
import sys
import cPickle
import pdb

import matplotlib.pyplot as plt
from python_visual_mpc.video_prediction.basecls.utils.get_designated_pix import Getdesig

from python_visual_mpc.video_prediction.utils_vpred.adapt_params_visualize import adapt_params_visualize
from tensorflow.python.platform import app
from tensorflow.python.platform import flags



from datetime import datetime
import collections
# How often to record tensorboard summaries.
SUMMARY_INTERVAL = 40

# How often to run a batch through the validation model.
VAL_INTERVAL = 200

# How often to save a model checkpoint
SAVE_INTERVAL = 2000



## Helper functions
def peak_signal_to_noise_ratio(true, pred):
    """Image quality metric based on maximal signal power vs. power of the noise.

    Args:
      true: the ground truth image.
      pred: the predicted image.
    Returns:
      peak signal to noise ratio (PSNR)
    """
    return 10.0 * tf.log(1.0 / mean_squared_error(true, pred)) / tf.log(10.0)


def mean_squared_error(true, pred):
    """L2 distance between tensors true and pred.

    Args:
      true: the ground truth image.
      pred: the predicted image.
    Returns:
      mean squared error between ground truth and predicted image.
    """
    return tf.reduce_sum(tf.square(true - pred)) / tf.to_float(tf.size(pred))



def l1_deriv_loss(flow_field):

    sobel_r = np.array([[ 1, 2, 1],
                        [ 0, 0, 0],
                        [-1,-2,-1]], dtype=np.float32)

    sobel_c = np.transpose(sobel_r)
    sobel_r = sobel_r.reshape([3,3,1,1])
    sobel_c = sobel_c.reshape([3, 3,1, 1])

    sobel_r = tf.constant(sobel_r, dtype=tf.float32)
    sobel_c = tf.constant(sobel_c, dtype=tf.float32)

    r_flow = tf.expand_dims(flow_field[:, :, :, 0], -1)
    c_flow = tf.expand_dims(flow_field[:, :, :, 1], -1)

    dr_dr_flow = tf.nn.conv2d(r_flow, sobel_r, strides=[1,1,1,1], padding='SAME')
    dr_dc_flow = tf.nn.conv2d(r_flow, sobel_c, strides=[1,1,1,1], padding='SAME')

    dc_dr_flow = tf.nn.conv2d(c_flow, sobel_r, strides=[1,1,1,1], padding='SAME')
    dc_dc_flow = tf.nn.conv2d(c_flow, sobel_c, strides=[1,1,1,1], padding='SAME')

    combined = tf.concat([dr_dr_flow, dr_dc_flow, dc_dr_flow, dc_dc_flow], axis= 3)
    return tf.norm(combined, ord=1)


class DescriptorModel(object):
    def __init__(self,
                 conf,
                 images=None,
                 reuse_scope=None,
                 pix_distrib=None,
                 train = True
                 ):

        self.conf = conf

        from descriptor_flow_model import Descriptor_Flow

        self.iter_num = tf.placeholder(tf.float32, [])
        summaries = []

        if train:
            rand_ind = tf.random_uniform([1], 0, self.conf['sequence_length']-1, dtype=tf.int64)
            self.rand_ind = rand_ind
            images = [images[:,tf.squeeze(rand_ind)], images[:,tf.squeeze(rand_ind+1)]]
        else:
            images = tf.split(images,2,1)
            images = [tf.reshape(im, (1, 64,64, 3)) for im in images]

        if reuse_scope is None:
            self.d = Descriptor_Flow(
                conf,
                images)
        else:  # If it's a validation or test model.
            with tf.variable_scope(reuse_scope, reuse=True):
                self.d = Descriptor_Flow(
                    conf,
                    images)

        # L2 loss, PSNR for eval.

        self.loss = mean_squared_error(images[1], self.d.transformed01)

        if 'forward_backward' in conf:
            self.loss += mean_squared_error(images[0], self.d.transformed10)

        summaries.append(tf.summary.scalar('recon_cost', self.loss))

        self.lr = tf.placeholder_with_default(conf['learning_rate'], ())

        self.train_op = tf.train.AdamOptimizer(self.lr).minimize(self.loss)
        self.summ_op = tf.summary.merge(summaries)


def search_region(conf, current_pos, d1, descp):
    if 'local_search' in conf:
        ksize = conf['kern_size']
        d1_padded = np.lib.pad(d1, ((ksize/2, ksize/2),(ksize/2,ksize/2), (0,0)), 'constant', constant_values=((0, 0,), (0,0) , (0, 0)))

        cur_r = current_pos[0]
        cur_c = current_pos[1]

        search_region = d1_padded[ksize/2 +cur_r-ksize/2:ksize/2 +cur_r+ksize/2+1,
                                  ksize/2 +cur_c-ksize/2:ksize/2 +cur_c+ksize/2+1]
        distances = np.sum(np.square(search_region - descp), 2)

        heatmap = np.zeros(d1_padded.shape[:2])
        heatmap[ksize/2 + cur_r-ksize/2:ksize/2 + cur_r+ksize/2+1,
                ksize/2 + cur_c-ksize/2:ksize/2 + cur_c+ksize/2+1] = distances

        heatmap = heatmap[ksize/2:ksize/2+64,ksize/2:ksize/2+64]
        heatmap = heatmap[None, :, :, None]

        distances = np.sum(np.square(search_region - descp), 2)

        minimum_dist = distances.min()

        newpos = current_pos + np.unravel_index(distances.argmin(), distances.shape) - np.array([ksize/2, ksize/2 ])
        newpos = np.clip(newpos, 0, 63)

        # if minimum_dist > 400:
        #     print 'track lost, remaining on current pos'
        #     newpos = current_pos

        # print 'delta pos', np.unravel_index(distances.argmin(), distances.shape) - np.array([ksize / 2, ksize / 2])
    elif 'global_search' in conf:
        distances = np.sum(np.square(d1 - descp), 2)
        newpos = np.unravel_index(distances.argmin(), distances.shape)

        heatmap = distances[None, :, :, None]
        minimum_dist = distances.min()
    else:
        raise ValueError("no search type specificed")



    # np.sum(np.square(search_region), -1)
    # fig = plt.figure()
    # plt.imshow(np.sum(np.square(search_region), -1), cmap=plt.get_cmap('jet'))
    # fig.suptitle('squared euclidean norm of descriptors', fontsize=20)

    # print 'mind index', np.unravel_index(distances.argmin(), distances.shape)
    #
    # fig = plt.figure()
    # plt.imshow(distances, cmap=plt.get_cmap('jet'))
    # fig.suptitle('squared euclidean distance', fontsize=20)
    # plt.show()

    return newpos, heatmap, minimum_dist


def visualize(conf):
    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.4)
    # Make training session.

    sess = tf.InteractiveSession(config=tf.ConfigProto(gpu_options=gpu_options))

    tf.train.start_queue_runners(sess)

    input_distrib = tf.placeholder(tf.float32, shape=(1, 64, 64, 1))

    images = tf.placeholder(tf.float32, name='images',
                            shape=(1, 2, 64, 64, 3))
    conf['batch_size'] = 1
    with tf.variable_scope('model', reuse=None):
        model = DescriptorModel(conf, images, pix_distrib= input_distrib, train=False)

    sess.run(tf.global_variables_initializer())

    import re
    itr_vis = re.match('.*?([0-9]+)$', conf['visualize']).group(1)
    saver = tf.train.Saver(tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES), max_to_keep=0)
    saver.restore(sess, conf['output_dir'] + '/' + FLAGS.visualize)
    print 'restore done.'

    conf['batch_size'] = 10

    if 'adim' in conf:
        from python_visual_mpc.video_prediction.read_tf_record_wristrot import build_tfrecord_input as build_tfrecord_fn
    else:
        from python_visual_mpc.video_prediction.read_tf_record_sawyer12 import build_tfrecord_input as build_tfrecord_fn
    val_images, _, _ = build_tfrecord_fn(conf, training=False)
    tf.train.start_queue_runners(sess)

    [ground_truth] = sess.run([val_images])

    b_exp = 4
    initial_img = ground_truth[b_exp][0]
    c = Getdesig(initial_img, conf, 'b{}'.format(b_exp))
    desig_pos_aux1 = c.coords.astype(np.int32)
    # desig_pos_aux1 = np.array([31, 29])

    output_distrib_list, transformed10_list, transformed01_list, flow01_list, flow10_list = [], [], [], [], []
    masks01_l, masks10_l = [], []

    pos_list = []
    heat_maps = []
    min_dists = []

    for t in range(conf['sequence_length']-1):
        feed_dict = {
                     model.lr: 0,
                     images: ground_truth[b_exp,t:t+2].reshape(1,2,64,64,3),  #could alternatively feed in gen_image
                     # input_distrib: next_input_distrib
                     }

        if 'forward_backward' in conf:
            # transformed01, transformed10, d0, d1, flow01, flow10, masks01, masks10 = sess.run([model.d.transformed01,
            transformed01, transformed10, d0, d1, flow01, flow10 = sess.run([model.d.transformed01,
                                                                             model.d.transformed10,
                                                                             model.d.d0,
                                                                             model.d.d1,
                                                                             model.d.flow_vectors01,
                                                                             model.d.flow_vectors10,
                                                                             # model.d.masks01,
                                                                             # model.d.masks10
                                                                             ], feed_dict)
            transformed10_list.append(transformed10)
            flow10_list.append(flow10)
            # masks10_l.append(masks10)
        else:
            transformed01, d0, d1, flow01 = sess.run([model.d.transformed01,
                                                      model.d.d0,
                                                      model.d.d1,
                                                      model.d.flow_vectors01], feed_dict)

        flow01_list.append(flow01)
        transformed01_list.append(transformed01)
        # masks01_l.append(masks01)

        d0 = np.squeeze(d0)
        d1 = np.squeeze(d1)

        if t == 0:
            plt.figure()
            f, axarr = plt.subplots(1, 2)
            axarr[0].imshow(np.sum(np.square(d0), -1), cmap=plt.get_cmap('jet'))
            axarr[0].set_title('squared euclidean norm of descriptors d0', fontsize=8)

            axarr[1].imshow(np.sum(np.square(d1), -1), cmap=plt.get_cmap('jet'))
            axarr[1].set_title('squared euclidean norm of descriptors d1', fontsize=8)
            plt.savefig(conf['output_dir']+ '/euclidean_norm_t0.png')
            plt.close()

        if t == 0:
            tar_descp =  d0[desig_pos_aux1[0], desig_pos_aux1[1]]
            current_pos = desig_pos_aux1
            pos_list.append(current_pos)

        current_pos, heat_map, min_dist = search_region(conf, current_pos, d1, tar_descp)
        pos_list.append(current_pos)
        heat_maps.append(heat_map)
        min_dists.append(min_dist)


    plt.figure()
    plt.plot(min_dists)
    plt.xlabel('steps')
    plt.ylabel('minimum squared distanced to initial descriptor')
    plt.savefig(conf['output_dir'] + '/min_dist_overtime')
    plt.close()

    dict = collections.OrderedDict()
    ground_truth = ground_truth[b_exp].reshape(1, conf['sequence_length'], 64, 64, 3)

    ground_truth = add_crosshairs(ground_truth, pos_list)
    dict['ground_truth'] = ground_truth

    dict['transformed01'] = transformed01_list

    dict['heat_map'] = heat_maps
    dict['flow01'] = flow01_list

    if 'forward_backward' in conf:
        dict['transformed10'] = transformed10_list
        dict['flow10'] = flow10_list

    dict['transformed01'] = transformed01_list

    # dict['masks01'] = masks01_l
    # dict['masks10'] = masks10_l

    # pos_list = [np.expand_dims(p,axis=0) for p in pos_list]
    # dict['overlay_ground_truth'] = pos_list

    dict['iternum'] = itr_vis

    cPickle.dump(dict, open(conf['output_dir'] + '/pred.pkl', 'wb'))
    print 'written files to:' + conf['output_dir']

    from python_visual_mpc.video_prediction.utils_vpred.animate_tkinter import Visualizer_tkinter
    v = Visualizer_tkinter(dict, numex=1, append_masks=True,
                           filepath=conf['output_dir'],
                           suf='flow_b{}_l{}'.format(b_exp, conf['sequence_length']),
                           renorm_heatmaps=False)
    v.build_figure()

def add_crosshairs(images, pos):

    out = []

    for t in range(images.shape[1]):
        im = np.squeeze(images[:,t])
        p = pos[t]
        im[p[0]-5:p[0]-2,p[1]] = np.array([0, 1,1])
        im[p[0]+3:p[0]+6, p[1]] = np.array([0, 1, 1])

        im[p[0],p[1]-5:p[1]-2] = np.array([0, 1,1])

        im[p[0], p[1]+3:p[1]+6] = np.array([0, 1, 1])

        im[p[0], p[1]] = np.array([0, 1, 1])

        # plt.imshow(im)
        # plt.show()
        out.append(im)

    out = np.stack(out, axis=0)
    out = out[None,...]

    return out

def main(unused_argv, conf_script= None):
    os.environ["CUDA_VISIBLE_DEVICES"] = str(FLAGS.device)
    print 'using CUDA_VISIBLE_DEVICES=', FLAGS.device
    from tensorflow.python.client import device_lib
    print device_lib.list_local_devices()

    if conf_script == None: conf_file = FLAGS.hyper
    else: conf_file = conf_script

    if not os.path.exists(FLAGS.hyper):
        sys.exit("Experiment configuration not found")
    hyperparams = imp.load_source('hyperparams', conf_file)
    conf = hyperparams.configuration
    if FLAGS.visualize:
        print 'creating visualizations ...'
        conf = adapt_params_visualize(conf, FLAGS.visualize)
    print '-------------------------------------------------------------------'
    print 'verify current settings!! '
    for key in conf.keys():
        print key, ': ', conf[key]
    print '-------------------------------------------------------------------'

    if conf['visualize']:
        print 'visualizing'
        visualize(conf)

    if 'adim' in conf:
        from python_visual_mpc.video_prediction.read_tf_record_wristrot import \
            build_tfrecord_input as build_tfrecord_fn
    else:
        from python_visual_mpc.video_prediction.read_tf_record_sawyer12 import \
            build_tfrecord_input as build_tfrecord_fn

    print 'Constructing models and inputs'
    with tf.variable_scope('model', reuse=None) as training_scope:
        images,_ , _ = build_tfrecord_fn(conf, training=True)
        model = DescriptorModel(conf, images)

    with tf.variable_scope('val_model', reuse=None):
        val_images,_ , _ = build_tfrecord_fn(conf, training=False)
        val_model = DescriptorModel(conf, val_images, reuse_scope=training_scope)

    print 'Constructing saver.'
    saver = tf.train.Saver(tf.get_collection(tf.GraphKeys.VARIABLES), max_to_keep=0)

    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.85)
    # Make training session.
    sess = tf.InteractiveSession(config=tf.ConfigProto(gpu_options=gpu_options))
    summary_writer = tf.summary.FileWriter(conf['output_dir'], graph=sess.graph, flush_secs=10)

    tf.train.start_queue_runners(sess)
    sess.run(tf.global_variables_initializer())


    itr_0 =0
    if conf['pretrained_model']:    # is the order of initialize_all_variables() and restore() important?!?
        saver.restore(sess, conf['pretrained_model'])
        # resume training at iteration step of the loaded model:
        import re
        itr_0 = re.match('.*?([0-9]+)$', conf['pretrained_model']).group(1)
        itr_0 = int(itr_0)
        print 'resuming training at iteration:  ', itr_0

    tf.logging.info('iteration number, cost')

    starttime = datetime.now()
    t_iter = []
    # Run training.
    for itr in range(itr_0, conf['num_iterations'], 1):
        t_startiter = datetime.now()
        # Generate new batch of data_files.
        feed_dict = {
                     model.iter_num: np.float32(itr),
                     model.lr: conf['learning_rate']}
        cost, _, summary_str = sess.run([model.loss, model.train_op, model.summ_op],
                                        feed_dict)

        # Print info: iteration #, cost.
        if (itr) % 10 ==0:
            tf.logging.info(str(itr) + ' ' + str(cost))

        if (itr) % VAL_INTERVAL == 2:
            # Run through validation set.
            feed_dict = {val_model.lr: 0.0,
                         val_model.iter_num: np.float32(itr)}
            _, val_summary_str = sess.run([val_model.train_op, val_model.summ_op],
                                          feed_dict)
            summary_writer.add_summary(val_summary_str, itr)


        if (itr) % SAVE_INTERVAL == 2:
            tf.logging.info('Saving model to' + conf['output_dir'])
            saver.save(sess, conf['output_dir'] + '/model' + str(itr))

        t_iter.append((datetime.now() - t_startiter).seconds * 1e6 +  (datetime.now() - t_startiter).microseconds )

        if itr % 100 == 1:
            hours = (datetime.now() -starttime).seconds/3600
            tf.logging.info('running for {0}d, {1}h, {2}min'.format(
                (datetime.now() - starttime).days,
                hours,
                (datetime.now() - starttime).seconds/60 - hours*60))
            avg_t_iter = np.sum(np.asarray(t_iter))/len(t_iter)
            tf.logging.info('time per iteration: {0}'.format(avg_t_iter/1e6))
            tf.logging.info('expected for complete training: {0}h '.format(avg_t_iter /1e6/3600 * conf['num_iterations']))

        if (itr) % SUMMARY_INTERVAL:
            summary_writer.add_summary(summary_str, itr)

    tf.logging.info('Saving model.')
    saver.save(sess, conf['output_dir'] + '/model')
    tf.logging.info('Training complete')
    tf.logging.flush()


if __name__ == '__main__':
    FLAGS = flags.FLAGS
    flags.DEFINE_string('hyper', '', 'hyperparameters configuration file')
    flags.DEFINE_string('visualize', '', 'model within hyperparameter folder from which to create gifs')
    flags.DEFINE_integer('device', 0, 'the value for CUDA_VISIBLE_DEVICES variable')

    tf.logging.set_verbosity(tf.logging.INFO)
    app.run()
