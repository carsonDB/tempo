"""Build cnn model
Input:
    * batch of tensors
    * batch of labels
Return:
    * batch of logits
"""
from __future__ import division
import tensorflow as tf

from config.config_agent import FLAGS, VARS
from kits import variable_on_cpu, variable_with_weight_decay
from models.model_proto import Model_proto


class Model(Model_proto):
    def __init__(self):
        super(Model, self).__init__()
        self.layers = FLAGS['graph']

    def infer(self, inputs):
        """Build the computation graph.

        Args:
            inputs: batch of tensors
            masks
        Returns:
            unscaled Logits.
        """
        self.batch_size = inputs.get_shape().as_list()[0]
        layers = self.layers
        num_class = self.num_class
        ly_id = 0
        ly_out = inputs

        for i, ly in enumerate(layers):
            ly_type = ly['type']

            # conv_2d layer
            if ly_type == 'conv2d':
                ly_id += 1
                with tf.variable_scope('conv2d_%d' % ly_id) as scope:
                    k_shape = ly['filter']
                    k_shape[-2] = ly_out.get_shape()[-1].value
                    kernel = variable_with_weight_decay('weights',
                                                        shape=k_shape,
                                                        stddev=ly['init_stddev'],
                                                        wd=ly['weight_decay'])
                    conv = tf.nn.conv2d(ly_out, kernel,
                                        strides=ly['strides'],
                                        padding=ly['padding'])
                    biases = variable_on_cpu('biases', [k_shape[-1]],
                                             tf.constant_initializer(0.0))
                    bias = tf.nn.bias_add(conv, biases)
                    ly_out = tf.nn.relu(bias, name=scope.name)
                    # _activation_summary(ly_out)

            # max_pool_2d layer
            elif ly_type == 'max_pool2d':
                ly_out = tf.nn.max_pool(ly_out, ksize=ly['ksize'],
                                        strides=ly['strides'],
                                        padding=ly['padding'],
                                        name=('pool2d_%d' % ly_id))

            elif ly_type == 'lrn':
                ly_out = tf.nn.lrn(ly_out, ly['depth_radius'], bias=ly['bias'],
                                   alpha=ly['alpha'], beta=ly['beta'],
                                   name='lrn_%d' % ly_id)

            elif ly_type == 'dropout':
                if VARS['mode'] == 'train':
                    ly_out = tf.nn.dropout(ly_out, keep_prob=ly['prob'])

            # full connected layer
            elif ly_type == 'fc':
                ly_id += 1
                with tf.variable_scope('local_%d' % ly_id) as scope:
                    reshape = tf.reshape(ly_out, [self.batch_size, -1])
                    dim0 = reshape.get_shape()[-1].value
                    shape = ly['shape']
                    dim1 = shape[-1]
                    if shape[0] != -1 and shape[0] != dim0:
                        raise ValueError('wrong dimension at fc-layer %d'
                                         % ly_id)

                    weights = variable_with_weight_decay('weights',
                                                         shape=[dim0, dim1],
                                                         stddev=ly['init_stddev'],
                                                         wd=ly['weight_decay'])
                    biases = variable_on_cpu('biases', [dim1],
                                             tf.constant_initializer(ly['init_bias']))
                    ly_out = tf.nn.relu(tf.matmul(reshape, weights) + biases,
                                        name=scope.name)
                    # _activation_summary(ly_out)
            else:
                raise ValueError('no such layer: %s' % ly_type)

        # softmax, i.e. softmax(WX + b)
        with tf.variable_scope('softmax_linear') as scope:
            # last layer dimension == dim1 ? (last == fc)
            assert(ly_type == 'fc')
            last_dim = ly_out.get_shape()[-1].value
            weights = variable_with_weight_decay('weights',
                                                 [last_dim, num_class],
                                                 stddev=1 / last_dim,
                                                 wd=0.0)

            biases = variable_on_cpu('biases', [num_class],
                                     tf.constant_initializer(0.0))
            softmax_linear = tf.add(tf.matmul(ly_out, weights), biases,
                                    name=scope.name)
            # _activation_summary(softmax_linear)
        # don't softmax in advance

        return softmax_linear

    def test(self, inputs, labels, masks, top):
        raise ValueError('...')
        batch_size = self.batch_size
        max_time_steps = self.flags['max_time_steps']
        num_class = self.num_class

        raw_shape = inputs.get_shape().as_list()
        # inputs_group: [b, g, m_s, d, h, w, c]
        inputs = tf.transpose(inputs, perm=[0, 2, 1] + range(3, 7))
        # inputs: [b, m_s, g, d, h, w, c]
        inputs = tf.reshape(inputs, shape=[-1] + raw_shape[-4:])
        # inputs: [b_ms_g, d, h, w, c]
        # Build a Graph that computes the logits predictions from the
        # inference model
        logits = self.inference(inputs)
        # logits: [b_ms_g, num_class]
        logits = tf.reshape(logits, shape=[batch_size, max_time_steps,
                                           -1, num_class])
        # logits: [b, m_s, g, num_class]
        avg_g_logits = tf.reduce_mean(logits, reduction_indices=2)
        # avg_g_logits: [b, m_s, num_class]
        masks = tf.expand_dims(masks, 2)
        # mask: [b, m_s, 1]
        real_len = tf.reduce_sum(masks, reduction_indices=1)
        # real_len: [b, 1]
        real_logits = avg_g_logits * masks
        # real_logits: [b, m_s, num_class]
        final_logits = (tf.reduce_sum(real_logits, reduction_indices=1)
                        / real_len)

        return tf.nn.in_top_k(final_logits, labels, top)
