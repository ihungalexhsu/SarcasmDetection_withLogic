#! /usr/bin/env python3

import datetime
import os
import time

import numpy as np
import tensorflow as tf
from tensorflow.contrib import learn


import data_helpers
from text_cnn import TextCNN
from logic_nn import LogicNN


# Parameters
# ==================================================

# Data loading params
tf.flags.DEFINE_float("dev_sample_percentage", 0.04, "Percentage of the training data to use for validation")
tf.flags.DEFINE_string("positive_data_file", "../Data/sarcasm_data_proc.npy", "Data source for the positive data.")
tf.flags.DEFINE_string("negative_data_file", "../Data/nonsarc_data_proc.npy", "Data source for the negative data.")
tf.flags.DEFINE_string("data_file", "../Data/train_balanced.npy", "Data source for the training data.")

# Model Hyperparameters
tf.flags.DEFINE_integer("embedding_dim", 128, "Dimensionality of character embedding (default: 128)")
tf.flags.DEFINE_string("filter_sizes", "3,4,5", "Comma-separated filter sizes (default: '3,4,5')")
tf.flags.DEFINE_integer("num_filters", 128, "Number of filters per filter size (default: 128)")
tf.flags.DEFINE_float("dropout_keep_prob", 0.5, "Dropout keep probability (default: 0.5)")
tf.flags.DEFINE_float("l2_reg_lambda", 0.0, "L2 regularization lambda (default: 0.0)")
tf.flags.DEFINE_string("pi_params", "0.95, 0", "parameters of pi: 'base of decay func, lower bound' (default: '0.95,0 ')")

# Training parameters
tf.flags.DEFINE_integer("batch_size", 32, "Batch Size (default: 32)")
tf.flags.DEFINE_integer("num_epochs", 80, "Number of training epochs (default: 80)")
tf.flags.DEFINE_integer("evaluate_every", 100, "Evaluate model on dev set after this many steps (default: 100)")
tf.flags.DEFINE_integer("checkpoint_every", 100, "Save model after this many steps (default: 100)")
tf.flags.DEFINE_integer("num_checkpoints", 5, "Number of checkpoints to store (default: 5)")

# Misc Parameters
tf.flags.DEFINE_boolean("allow_soft_placement", True, "Allow device soft device placement")
tf.flags.DEFINE_boolean("log_device_placement", False, "Log placement of ops on devices")
tf.flags.DEFINE_float("gpu_usage", 1.0, "per process gpu memory fraction, (defult: 1.0)")

FLAGS = tf.flags.FLAGS
FLAGS._parse_flags()
print("\nParameters:")
for attr, value in sorted(FLAGS.__flags.items()):
    print("{}={}".format(attr.upper(), value))
print("")
FLAGS.pi_params = list(map(int, FLAGS.pi_params.split(",")))

# Data Preparation
# ==================================================

# Load data
print("Loading data...")
# x_text, y = data_helpers.load_data_and_labels(FLAGS.positive_data_file, FLAGS.negative_data_file)
x_text, y = data_helpers.load_npy_data(FLAGS.data_file)

# Build vocabulary
max_document_length = max([len(x.split(' ')) for x in x_text])
vocab_processor = learn.preprocessing.VocabularyProcessor(max_document_length)
x = np.array(list(vocab_processor.fit_transform(x_text)))

# Randomly shuffle data
# np.random.seed(10)
# shuffle_indices = np.random.permutation(np.arange(len(y)))
# x_shuffled = x[shuffle_indices]
# y_shuffled = y[shuffle_indices]

x_shuffled = x
y_shuffled = y

# Split train/test set
# TODO: This is very crude, should use cross-validation
dev_sample_index = -1 * int(FLAGS.dev_sample_percentage * float(len(y)))
x_train, x_dev = x_shuffled[:dev_sample_index], x_shuffled[dev_sample_index:]
y_train, y_dev = y_shuffled[:dev_sample_index], y_shuffled[dev_sample_index:]
print("Vocabulary Size: {:d}".format(len(vocab_processor.vocabulary_)))
print("Train/Dev split: {:d}/{:d}".format(len(y_train), len(y_dev)))


# Training
# ==================================================

with tf.Graph().as_default():
    session_conf = tf.ConfigProto(
        gpu_options=tf.GPUOptions(per_process_gpu_memory_fraction=FLAGS.gpu_usage),
        allow_soft_placement=FLAGS.allow_soft_placement,
        log_device_placement=FLAGS.log_device_placement)
    sess = tf.Session(config=session_conf)
    with sess.as_default():
        cnn = TextCNN(
            sequence_length=x_train.shape[1],
            num_classes=y_train.shape[1],
            vocab_size=len(vocab_processor.vocabulary_),
            embedding_size=FLAGS.embedding_dim,
            filter_sizes=list(map(int, FLAGS.filter_sizes.split(","))),
            num_filters=FLAGS.num_filters,
            l2_reg_lambda=FLAGS.l2_reg_lambda)

        # build the feature of RULE1-rule
        # fea: symbolic feature tensor, of shape 3
        #      fea[0]   : 1 if x=x1_love_x2, 0 otherwise
        #      fea[1:2] : classifier.predict_p(x_2)

        rule1_ind = tf.placeholder(tf.int32, [FLAGS.batch_size, 1], name="rule1_ind")
        rule1_senti = tf.placeholder(tf.int32, [FLAGS.batch_size, 1], name="rule1_senti")
        rule1_rev = tf.ones_like(rule1_senti) - rule1_senti
        rule1_y_pred_p = tf.concatenate(rules_rev, rule1_senti, axis=1)
        rule1_full = tf.concatenate(rule1_ind, rule1_y_pred_p, axis=1)

        # add logic layer
        nclasses = 2
        rules = [FOL_Rule1(nclasses, cnn.embedded_chars, f_rule1_full)]
        rule_lambda = [1]  # confidence for the "rule1" rule = 1
        pi_holder = tf.placeholder(tf.float32, [1], name='pi')
        # pi: how percentage listen to teacher loss,
        #     starts from lower bound

        logic_nn = LogicNN(
            input=x,
            netwrok=cnn,
            rules=rules,
            rule_lambda=rule_lambda,
            pi=pi_holder,
            C=1.)

        # Define Training procedure
        global_step = tf.Variable(0, name="global_step", trainable=False)
        optimizer = tf.train.AdamOptimizer(1e-3)
        grads_and_vars = optimizer.compute_gradients(logic_nn.neg_log_liklihood)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

        # Keep track of gradient values and sparsity (optional)
        grad_summaries = []
        for g, v in grads_and_vars:
            if g is not None:
                grad_hist_summary = tf.summary.histogram("{}/grad/hist".format(v.name), g)
                sparsity_summary = tf.summary.scalar("{}/grad/sparsity".format(v.name), tf.nn.zero_fraction(g))
                grad_summaries.append(grad_hist_summary)
                grad_summaries.append(sparsity_summary)
        grad_summaries_merged = tf.summary.merge(grad_summaries)

        # Output directory for models and summaries
        timestamp = str(int(time.time()))
        out_dir = os.path.abspath(os.path.join(os.path.curdir, "runs", timestamp))
        print("Writing to {}\n".format(out_dir))

        # Summaries for loss and accuracy
        loss_summary = tf.summary.scalar("loss", cnn.loss)
        acc_summary = tf.summary.scalar("accuracy", cnn.accuracy)

        # Train Summaries
        train_summary_op = tf.summary.merge([loss_summary, acc_summary, grad_summaries_merged])
        train_summary_dir = os.path.join(out_dir, "summaries", "train")
        train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)

        # Dev summaries
        dev_summary_op = tf.summary.merge([loss_summary, acc_summary])
        dev_summary_dir = os.path.join(out_dir, "summaries", "dev")
        dev_summary_writer = tf.summary.FileWriter(dev_summary_dir, sess.graph)

        # Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
        checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
        checkpoint_prefix = os.path.join(checkpoint_dir, "model")
        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)
        saver = tf.train.Saver(tf.global_variables(), max_to_keep=FLAGS.num_checkpoints)

        # Write vocabulary
        vocab_processor.save(os.path.join(out_dir, "vocab"))

        # Initialize all variables
        sess.run(tf.global_variables_initializer())

        def train_step(x_batch, y_batch):
            """
            A single training step
            """
            cur_step = tf.train.global_step(sess, global_step)
            cur_epoch = int(cur_step * 1.0 / FLAGS.batch_size)
            pi = get_pi(cur_iter=cur_epoch, params=FLAGS.pi_params)

            feed_dict = {
                logic_nn.network.input_x: x_batch,
                logic_nn.network.input_y: y_batch,
                logic_nn.network.dropout_keep_prob: FLAGS.dropout_keep_prob,
                logic_nn.network.pi: pi
            }
            _, step, summaries, neg_log_liklihood, accuracy = sess.run(
                [train_op, global_step, train_summary_op, logic_nn.neg_log_liklihood, logic_nn.network.accuracy],
                feed_dict)
            time_str = datetime.datetime.now().isoformat()
            # if step % FLAGS.evaluate_every == 0:
            print("{}: step {}, nlld {:g}, acc {:g}".format(time_str, step, neg_log_liklihood, accuracy))
            train_summary_writer.add_summary(summaries, step)

        def dev_step(x_batch, y_batch, writer=None):
            """
            Evaluates model on a dev set
            """
            feed_dict = {
                logic_nn.network.input_x: x_batch,
                logic_nn.network.input_y: y_batch,
                logic_nn.network.dropout_keep_prob: 1.0
            }
            step, summaries, loss, accuracy = sess.run(
                [global_step, dev_summary_op, logic_nn.network..loss, logic_nn.network.accuracy],
                feed_dict)
            time_str = datetime.datetime.now().isoformat()
            print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
            if writer:
                writer.add_summary(summaries, step)

        def get_pi(cur_iter, params=None):
            """
            exponential decay: pi_t = max{1 - k^t, lb}
            pi: how percentage listen to teacher loss,
                starts from lower bound
            """
            k, lb = params[0], params[1]
            pi = max([1. - k**cur_iter, float(lb)])
            return pi

        # Batches Generator
        batches = data_helpers.batch_iter(
            list(zip(x_train, y_train)), FLAGS.batch_size, FLAGS.num_epochs)

        # Training loop. For each batch...
        for batch in batches:
            x_batch, y_batch = zip(*batch)
            train_step(x_batch, y_batch)
            current_step = tf.train.global_step(sess, global_step)
            if current_step % FLAGS.evaluate_every == 0:
                print("\nEvaluation:")
                dev_step(x_dev, y_dev, writer=dev_summary_writer)
                print("")
            if current_step % FLAGS.checkpoint_every == 0:
                path = saver.save(sess, checkpoint_prefix, global_step=current_step)
                print("Saved model checkpoint to {}\n".format(path))
